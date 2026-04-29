# multipersonworkflow

Multi-person, multi-agent workflow on Azure AI Foundry. Three registered
Foundry agents (submissions / tax / legal) hand off through a custom
group-chat orchestrator (`chat-api`), with a React SPA front-end
(`chat-ui`), Cosmos DB for project + assignment state, and three MCP
backends that expose workflow tools per role profile.

```
chat-ui (React/Vite SPA)
   │  Authorization: Bearer <user JWT for api://mpwflow-api/Chat.ReadWrite>
   ▼
chat-api (FastAPI, Python)
   │  validates JWT (PyJWT) → builds OnBehalfOfCredential per request
   │  via Federated Identity Credential (UAMI signs assertion → no secret)
   ▼
Azure AI Foundry  ← runs registered agents as the signed-in user
   ├─ submissions / tax / legal agents (FoundryAgent participants)
   ├─ workflow MCP (Cosmos-backed) — open today; per-role profile filter
   └─ WorkIQ MCP (Microsoft 365 User Profile) — ARA OBOs the user token
```

## Repo layout

| Path | What it is |
|---|---|
| `chat-api/` | Python/FastAPI orchestrator (custom group-chat router + handoff loop) |
| `chat-ui/` | React/Vite SPA, MSAL.js auth |
| `agents/{submissions,tax,legal}/` | Registered Foundry agent definitions + `create_agent.py` |
| `mcp-server/` | FastMCP server exposing workflow tools (3 deployed instances, one per profile) |
| `infra/` | Bicep templates + `main.bicepparam` (azd-driven) |
| `azure.yaml` | azd top-level service + hook configuration |
| `scripts/deploy.ps1` | One-shot end-to-end wrapper (use this for fresh deploys) |
| `scripts/admin/` | Admin scripts (Entra app regs, tenant consent, Cosmos seed) |
| `scripts/pre-provision/` | azd `preprovision` hook — auto-loads `.env` into azd env + captures admin principalId for Cosmos RBAC |
| `scripts/pre-deploy/` | azd `predeploy` hook — writes `chat-ui/.env.production` so Vite inlines real MSAL IDs at build |
| `scripts/post-provision/` | azd `postprovision` hook — registers Foundry agents |
| `scripts/post-deploy/` | azd `postdeploy` hook — fans MCP image to tax + legal apps and seeds routing |
| `.env.sample` | Copy → `.env`, fill in, then `azd up` |
| `PLAN.md` / `PLAN.html` | Living iteration plan |

## Deploy from zero

Two M365 portal prereqs (manual, admin-only) + **one command** for everything else.

### 0. Tools

```pwsh
winget install Microsoft.AzureCLI
winget install Microsoft.Azd                  # Azure Developer CLI
winget install Microsoft.PowerShell           # PowerShell 7+
winget install Astral-sh.uv                   # for local Python work
winget install OpenJS.NodeJS.LTS              # for local chat-ui work

az login
azd auth login
```

### 1. Admin: Foundry portal prereqs (one-time, manual)

`azd` cannot provision the Foundry project or WorkIQ catalog
connections — these are M365 portal actions.

In the target tenant:

1. Create / pick an **Azure AI Foundry** project.
2. Deploy a chat model (e.g. `gpt-4o-mini`) and note the deployment name.
3. Add two WorkIQ catalog MCP connections (*Project → Connections →
   + New connection → Microsoft Agent 365*):
   - **`WorkIQUser`** → `https://agent365.svc.cloud.microsoft/agents/servers/mcp_MeServer`
   - **`WorkIQMail`** → `https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools`

   The names **must** match exactly — `agents/{tax,legal}/create_agent.py`
   look them up by name.

### 2. Developer: configure `.env`

```pwsh
cp .env.sample .env
# Fill in:
#   AZURE_SUBSCRIPTION_ID
#   AZURE_BASE_NAME            (3-12 chars, lowercase alphanumeric)
#   AZURE_ENV_NAME             (e.g. <base>-dev)
#   AZURE_LOCATION             (e.g. eastus2)
#   FOUNDRY_PROJECT_ENDPOINT
#   FOUNDRY_MODEL_DEPLOYMENT_NAME
# (ENTRA_TENANT_ID / ENTRA_BACKEND_APP_ID / ENTRA_SPA_APP_ID are
#  filled in automatically by step 3 — leave blank on first run.)
```

### 3. One-shot deploy

```pwsh
pwsh ./scripts/deploy.ps1
```

This wrapper does everything, idempotently:

1. **Entra app regs (first pass)** — `setup-entra.ps1` creates the
   `<base>-api` and `<base>-spa` registrations and writes their IDs back
   into `.env`.
2. **`azd up`** — provisions Bicep + builds + deploys + runs four hooks:
   - **preprovision** (`scripts/pre-provision/load_env_and_admin.ps1`):
     loads `.env` into the azd env and captures your user objectId so
     bicep can grant *you* Cosmos data-plane RBAC for seed scripts.
   - **predeploy** (`scripts/pre-deploy/write_chat_ui_env.ps1`): writes
     `chat-ui/.env.production` so Vite inlines real MSAL `tenantId` /
     `clientId` into the JS bundle (otherwise sign-in fails with
     `AADSTS900144`).
   - **postprovision** (`scripts/post-provision/register_agents.ps1`):
     registers / updates the 3 Foundry PromptAgents with the MCP FQDNs
     just created.
   - **postdeploy** (`scripts/post-deploy/fanout_mcp_image.ps1`):
     copies the freshly built mcp-server image to the tax + legal
     Container Apps and seeds Cosmos `routing/tax` + `routing/legal`
     queues (via `seed_routing.py`).
3. **Entra app regs (second pass)** — adds the federated credential
   binding the backend app reg to the chat-api UAMI (which now exists)
   plus the production redirect URI on the SPA.
4. **Tenant admin consent** — `grant-consent.ps1`.
5. Opens the deployed chat-ui in your default browser.

> **Why the two passes of `setup-entra.ps1`?** The federated credential
> on the backend app reg trusts the chat-api UAMI by `principalId` —
> which doesn't exist until `azd up` provisions it. The first pass
> creates app regs only; the second adds the FIC + prod redirect URIs.
> Both passes are idempotent and only add what's missing.

> **Edit SME assignees**: by default, `seed_routing.py` populates the
> tax + legal queues with placeholder emails. Open
> `scripts/admin/seed_routing.py` and edit the `ROUTING_DOCS` list to
> use real users in your tenant before the first real test.

### 4. Smoke

The browser should be open. Sign in with a tenant user. Send "Hi" — the
submissions agent should greet you by name (via WorkIQ `GetMyDetails`).
Tax / legal questions are routed to the matching SME agent and answered
as you (OBO).

## Re-deploy a single service

```pwsh
azd deploy chat-api      # or chat-ui, or mcp-server
```

`azd deploy` rebuilds + pushes a new image and updates the matching
Container App. To re-run a hook only, run the script directly:

```pwsh
pwsh ./scripts/pre-provision/load_env_and_admin.ps1   # refresh azd env from .env
pwsh ./scripts/pre-deploy/write_chat_ui_env.ps1       # refresh chat-ui MSAL config
pwsh ./scripts/post-provision/register_agents.ps1     # re-register Foundry agents
pwsh ./scripts/post-deploy/fanout_mcp_image.ps1       # re-fan-out mcp image + reseed routing
python ./scripts/admin/seed_routing.py                # reseed routing only
```

> **Note for `azd deploy <single-service>`:** when targeting a single
> service, some azd versions skip the global `predeploy` hook. If MSAL
> IDs have changed, run `pwsh ./scripts/pre-deploy/write_chat_ui_env.ps1`
> first (or just re-run `pwsh ./scripts/deploy.ps1` — it's idempotent).

> **`BACKEND_BASE_URL` in `chat-ui/Dockerfile`** has a hard-coded default
> URL that's only a placeholder — at runtime nginx reads the real value
> from the Container App env var (set in `infra/modules/chat-ui.bicep`),
> so the default is never actually used.

## Tear down

```pwsh
azd down --purge --force
```

Note: this only removes resources `azd up` created. It does **not**
delete the Foundry project, WorkIQ connections, or Entra app regs —
those are admin-managed prerequisites. Specifically, the federated
identity credential and production redirect URI added by the second
`setup-entra.ps1` pass will remain on the backend / SPA app regs and
need to be cleaned up manually if you want a fully clean slate.

## Iteration plan

See [`PLAN.md`](./PLAN.md) (or [`PLAN.html`](./PLAN.html) for a rendered
view). Iteration 8 collapsed deploy to a single command
(`scripts/deploy.ps1`) driven by `.env`.

## End-user identity passthrough (OBO + FIC)

When **System Administrator** asks "do I have any open projects?", the
WorkIQ MCP server must see a token *for that user* — not the chat-api's
service identity. Otherwise everyone would see everyone's projects.

### Token flow

```mermaid
sequenceDiagram
    autonumber
    actor User as 👤 User
    participant UI as chat-ui (SPA)
    participant API as chat-api
    participant OBO as OnBehalfOf<br/>Credential
    participant MI as User-Assigned<br/>Managed Identity
    participant Entra as Entra ID
    participant AzSDK as azure.core<br/>BearerTokenPolicy
    participant Foundry as Foundry<br/>(Hosted V2 agent)
    participant MCP as WorkIQ MCP

    Note over User,UI: 1️⃣ Sign-in (MSAL.js)
    User->>UI: click sign in
    UI->>Entra: authorize (scope=Chat.ReadWrite)
    Entra-->>UI: id_token + access_token (aud=backend)

    Note over UI,API: 2️⃣ Call chat-api
    UI->>API: POST /sessions/{id}/messages<br/>Authorization: Bearer <userJWT>

    Note over API: 3️⃣ Validate JWT (PyJWT + JWKS)<br/>aud=backend, scope=Chat.ReadWrite ✓

    Note over API,OBO: 4️⃣ Build per-request OBO credential
    API->>OBO: factory.for_user(userJWT)

    Note over API: 5️⃣ Hand credential to FoundryAgent<br/>FoundryAgent(credential=fs.credential)

    Note over API,Foundry: 6️⃣ agent.run("do i have...")
    API->>AzSDK: AIProjectClient HTTP call
    AzSDK->>OBO: get_token("https://ai.azure.com/.default")

    Note over OBO,Entra: 7️⃣ OBO exchange
    OBO->>MI: get_token("api://AzureADTokenExchange/.default")
    MI-->>OBO: client_assertion (FIC JWT)
    OBO->>Entra: POST /oauth2/v2.0/token<br/>grant=jwt-bearer<br/>client_id=backend<br/>client_assertion=<MI-FIC><br/>assertion=<userJWT><br/>scope=ai.azure.com/.default
    Entra-->>OBO: access_token<br/>aud=ai.azure.com, sub=user
    OBO-->>AzSDK: token

    AzSDK->>Foundry: POST /agents/legal-sme-agent/...<br/>Authorization: Bearer <userTokenForFoundry>

    Note over Foundry,MCP: 8️⃣ Foundry auto-OBO again<br/>(UserEntraToken connection)
    Foundry->>Entra: OBO → scope=McpServers.Me.All
    Entra-->>Foundry: access_token aud=workiq, sub=user
    Foundry->>MCP: tool call with user-context token
    MCP-->>Foundry: per-user data (only this user's projects)

    Foundry-->>AzSDK: response
    AzSDK-->>API: response
    API-->>UI: SSE event (assistant message)
    UI-->>User: render reply
```

### Scopes used at each hop

| Hop | Scope | Asked by | Audience of returned token |
|-----|-------|----------|----------------------------|
| 1. Browser → backend | `api://<backend-app-id>/Chat.ReadWrite` `openid` `profile` `offline_access` | chat-ui (MSAL.js) | backend app reg |
| 2. MI → Entra (assertion) | `api://AzureADTokenExchange/.default` | `OnBehalfOfCredential.client_assertion_func` | special audience used only as a FIC `client_assertion` |
| 3. backend → Foundry (OBO) | `https://ai.azure.com/.default` | `AIProjectClient` via `BearerTokenPolicy` | `https://ai.azure.com` (Foundry data plane) |
| 4. Foundry → WorkIQ (auto-OBO) | `api://<workiq-app-id>/McpServers.Me.All` | Foundry runtime (`UserEntraToken` connection) | WorkIQ MCP app reg |

The `.default` suffix means *"give me every scope that has been
admin-consented for this resource"* (vs. naming individual scopes).
Required for client_credentials, OBO, and managed-identity flows.

### Where each scope is set

- **Hop 1** — `chat-ui/src/auth/msal.ts` `loginRequest.scopes`. The backend
  app reg exposes `Chat.ReadWrite` as a delegated scope.
- **Hop 2** — `chat-api/src/chat_api/foundry_credential.py` — the
  assertion func calls
  `ManagedIdentityCredential().get_token("api://AzureADTokenExchange/.default")`.
  The federated credential on the backend app reg must trust this UAMI's
  `principalId` (NOT its clientId).
- **Hop 3** — **NOT in our code.** Hard-coded inside `azure-ai-projects`
  (`AIProjectClient._config.credential_scopes`). The OBO exchange asks
  Entra for this scope; the backend app reg must have admin-consented
  API permission to *Azure AI Services / user_impersonation* (or
  equivalent) — that's why the user only sees the consent prompt once.
- **Hop 4** — Configured per-tool in the **Foundry agent definition**
  (auth type = `UserEntraToken`, scope = `McpServers.Me.All`). Foundry
  stores the scope and runs the second OBO automatically when invoking
  the MCP connection.

### Why a Federated Identity Credential (FIC) instead of a client secret?

chat-api has no client secret. It proves it's the backend app reg by
minting a FIC assertion: it asks its UAMI for a token with audience
`api://AzureADTokenExchange`, then hands that token to Entra as the
`client_assertion` in the OBO call. The backend app reg has a federated
credential pointing at the UAMI's `principalId` — Entra trusts the
assertion because it's signed by a UAMI that was pre-approved.

Net result: *no service principal can spoof a user, no user token leaves
the trust boundary unencrypted, and there is no client secret to rotate
or leak.*

### Key code references

| What | File / line |
|------|-------------|
| MSAL config | `chat-ui/src/auth/msal.ts` |
| JWT validation | `chat-api/src/chat_api/token_validator.py` |
| Caller extraction | `chat-api/src/chat_api/auth.py` |
| OBO credential factory | `chat-api/src/chat_api/foundry_credential.py` |
| Per-request OBO build | `chat-api/src/chat_api/routes/sessions.py` (`_build_user_credential`) |
| Hand-off to FoundryAgent | `chat-api/src/chat_api/af_orchestrator.py` (`credential=fs.credential`) |
| App reg + FIC setup | `scripts/admin/setup-entra.ps1`, `scripts/admin/grant-consent.ps1` |

