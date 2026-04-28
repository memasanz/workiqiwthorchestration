# Multi-Person Workflow — Implementation Plan

## Scope

Three Foundry agents, one shared MCP server backed by Cosmos DB.

**In scope:**

- 3 Azure AI Foundry agents: **Submissions**, **Tax SME**, **Legal SME**
- 1 shared **MCP server** exposing 12 verb-aligned tools backed by Cosmos DB
- Cosmos data model (`projects`, `questions`, `routing`)

**Deferred (explicitly not in this plan):**

- Microsoft Teams integration (no bot, no proactive cards)
- Cross-agent orchestration / workflow runtime (each agent is invoked
  by a user and acts independently)
- WorkIQ / OBO / Teams SSO / per-user delegated identity (the MCP
  server uses managed identity to Cosmos; agents pass `userId` as a
  parameter and trust it for now)
- Notifications (no agent-to-agent or agent-to-user push)

These are the right next slices to add later, but they all depend on
the data and tool surface this plan delivers, so they come after.

## The three agents

### 1. Submissions Agent

**Who talks to it:** project submitters.

**What it does:** takes a free-form description of one or more
questions, classifies each as `tax` or `legal`, picks an assignee from
the routing table, creates a project in Cosmos, and writes the
questions in.

**Tools it uses (MCP):** `get_routing`, `create_project`,
`submit_questions`, `update_project_status`, `get_project`.

**Internal logic (no tool):** classify each question (LLM) before
calling `submit_questions`.

**Sample turn:**

> User: "I have 3 questions. (1) Can our subsidiary deduct R&D…
> (2) Is the NDA enforceable in Texas… (3) What's the depreciation
> schedule for the new servers?"
>
> Agent: classifies (1=tax, 3=tax, 2=legal); calls `get_routing('tax')`
> and `get_routing('legal')`; calls `create_project`; calls
> `submit_questions` with all three (each with classification +
> assignedUserId); replies *"Created project ACME-2026-Q1 with 3
> questions. Tax → Maya. Legal → Devon."*

### 2. Tax SME Agent

**Who talks to it:** the tax SME.

**What it does:** shows the SME their assigned tax questions across
projects, helps draft answers, and records final answers back to
Cosmos.

**Tools it uses (MCP):** `get_my_assignments` (filtered to
`classification='tax'`), `get_question`, `get_project`, `save_draft`,
`submit_answer`, `update_question_status`,
`set_question_classification` (for reclassifying mistakes), and
`assign_question` (for handing off).

**Sample turn:**

> SME: "What's on my plate?"
>
> Agent: calls `get_my_assignments(maya, classification='tax')` →
> *"You have 4 tax questions across 2 projects: …"*
>
> SME: "Let's do question 1."
>
> Agent: `get_question(q1)` → drafts an answer → SME edits → calls
> `submit_answer(q1, "<final>", by=maya)`. Question moves to
> `approved`.

### 3. Legal SME Agent

Identical shape to the Tax SME agent, but the agent's prompt is
scoped to legal work and `get_my_assignments` is filtered to
`classification='legal'`.

Two distinct agents (rather than one parameterized agent) keeps the
prompts/personas focused and lets us evolve them independently.

## MCP tool set (12 tools)

One MCP server, used by all three agents. Tools are aligned to the
verbs the agents actually want to express.

### Write tools (8)

| Tool | Used by | Purpose |
|---|---|---|
| `create_project` | Submissions | Creates a project with `status='uncompleted'`; returns `projectId` |
| `submit_questions` | Submissions | Adds one or more questions to a project; each carries `text`, `classification`, `assignedUserId` |
| `submit_answer` | SMEs | Persists a final answer for one question and flips its status to `approved` in one call |
| `update_project_status` | Submissions, SMEs | `uncompleted` ↔ `completed` |
| `update_question_status` | SMEs | Status changes other than approval (`rejected`, `escalated`, etc.) |
| `assign_question` | Submissions, SMEs | Re-assign a question to a different user |
| `set_question_classification` | Submissions, SMEs | Change a question's `classification`. Does **not** auto-reassign — caller follows with `assign_question` if needed |
| `save_draft` | SMEs | Optional in-progress save before final `submit_answer` |

### Read tools (4)

| Tool | Used by | Purpose |
|---|---|---|
| `get_my_assignments` | SMEs | Questions assigned to a user, filterable by `classification` and `status[]` |
| `get_project` | All | Project metadata + question summary |
| `get_question` | All | One full question record |
| `get_routing` | Submissions | Returns user list (and round-robin pointer) for a category |

### Tool design notes

- **Idempotency**: `create_project` and `submit_questions` accept an
  optional `clientRequestId` so retries don't double-write.
- **Audit fields**: every write tool requires a `by` parameter
  (the actor's userId) and appends an entry to the question's
  `history` array.
- **Concurrency**: `submit_answer`, `update_question_status`,
  `assign_question`, and `set_question_classification` use Cosmos
  optimistic concurrency on `_etag` to prevent racing writes.
- **Authorization**: out of scope for this iteration — the MCP
  server trusts the `by` / `userId` it receives. Tightening this is
  part of the deferred identity work.

## Cosmos data model (3 containers)

### `projects` (pk `/projectId`)

```json
{
  "id": "<projectId>",
  "projectId": "<projectId>",
  "displayName": "ACME-2026-Q1",
  "submitterId": "user1@contoso.com",
  "status": "uncompleted",
  "createdAt": "2026-04-24T...",
  "completedAt": null
}
```

### `questions` (pk `/projectId`)

```json
{
  "id": "<questionId>",
  "questionId": "<questionId>",
  "projectId": "<projectId>",
  "text": "Can our subsidiary deduct...",
  "classification": "tax",
  "assignedUserId": "maya@contoso.com",
  "status": "assigned",
  "draft": null,
  "finalAnswer": null,
  "history": [
    {"at": "...", "by": "submissions-agent", "action": "created"},
    {"at": "...", "by": "submissions-agent",
     "action": "assigned", "to": "maya@contoso.com"}
  ],
  "createdAt": "...",
  "updatedAt": "..."
}
```

**Question states:**

- `assigned` — created and assigned, no work yet.
- `drafting` — SME or agent has started a draft (`save_draft` called).
- `awaiting_review` — optional intermediate state if you want
  multi-person review later (not used in this iteration).
- `approved` — final answer submitted (`submit_answer` called).
- `rejected` — SME rejected; back to `drafting`.
- `escalated` — SME couldn't handle; flagged for follow-up.

**Project states:** `uncompleted` · `completed`. The Submissions
agent (or any agent) can set `completed` when all questions are
`approved`. We do **not** auto-flip in this iteration since there's
no orchestrator — it's an explicit `update_project_status` call.

### `routing` (pk `/category`)

```json
{
  "id": "tax",
  "category": "tax",
  "userIds": ["maya@contoso.com", "alex@contoso.com"],
  "roundRobinIndex": 0
}
```

Seeded with one document per category (`tax`, `legal`).
`get_routing` returns the next user and increments the index;
`submit_questions` records that user as `assignedUserId`.

## Architecture

```
   Submitter ─► Submissions Agent ──┐
                                    │
   Tax SME   ─► Tax SME Agent ──────┤
                                    │
   Legal SME ─► Legal SME Agent ────┤
                                    │  MCP (Foundry agent → MCP tool call)
                                    ▼
                          ┌────────────────────┐
                          │   MCP Server       │
                          │   (Python, Azure   │
                          │    Container Apps) │
                          └────────┬───────────┘
                                   │ Cosmos SDK + managed identity
                                   ▼
                          ┌────────────────────┐
                          │   Cosmos DB        │
                          │   projects         │
                          │   questions        │
                          │   routing          │
                          └────────────────────┘
```

**No Teams, no workflow runtime, no per-user delegated identity.**
Each agent is invoked from wherever it's published (Foundry chat,
test harness, Copilot Studio, etc.) and is fully autonomous within
the scope of one user turn.

## Components

| Component | Tech | Hosting | Identity |
|---|---|---|---|
| Submissions Agent | Azure AI Foundry agent | Foundry-managed | (Foundry agent identity) |
| Tax SME Agent | Azure AI Foundry agent | Foundry-managed | (Foundry agent identity) |
| Legal SME Agent | Azure AI Foundry agent | Foundry-managed | (Foundry agent identity) |
| MCP Server | Python (FastMCP or official MCP SDK) + FastAPI | Azure Container Apps | Managed identity → Cosmos data plane RBAC |
| Cosmos DB | NoSQL API | Azure | Managed identity (no keys) |
| App Insights | Telemetry | Azure | Managed identity |

## Build order

1. **Cosmos** — provision account/db/containers (`projects`,
   `questions`, `routing`). Seed `routing` with `tax` and `legal`
   docs containing test users.
2. **MCP server** — implement the 12 tools, write unit tests against
   a Cosmos emulator, deploy to Container Apps with managed identity.
3. **Submissions agent** — Foundry agent + MCP tool registration +
   prompt that classifies questions and calls `get_routing` →
   `create_project` → `submit_questions`.
4. **Tax SME agent** — Foundry agent + MCP tools + prompt for tax
   drafting/review using `get_my_assignments`, `get_question`,
   `submit_answer`.
5. **Legal SME agent** — clone of #4 with legal prompt.
6. **Smoke test** — submit a 3-question project via Submissions agent;
   verify Cosmos state; play SME role in Tax + Legal agents to draft
   and submit answers; flip the project to `completed` via
   `update_project_status`.

## Iteration 2 (added after wave-1 + wave-2 complete)

These three follow-ons clean up the architecture instead of adding net-new features:

### A. Model upgrade: gpt-5.3

Switch all agents from `gpt-4o-mini` to `gpt-5.3`. Steps:

1. Verify `gpt-5.3` model name + version + region availability via
   `az cognitiveservices model list` or Microsoft Learn. eastus2 may
   not have it on day one — pick the closest variant (e.g.
   `gpt-5.3-mini`) or move the Foundry account region.
2. Add the new model deployment to `infra/modules/foundry.bicep`
   alongside (or replacing) `gpt-4o-mini`. Redeploy.
3. Bump `MODEL_DEPLOYMENT_NAME` in
   `agents/{submissions,tax,legal}/create_agent.py`.
4. Re-run `create_agent.py` for each (idempotent — updates existing
   agent in place).
5. Re-run each smoke test plus the e2e test.

### B. Split MCP into 3 profile-filtered backends

Today the server exposes all 12 tools and we rely on Foundry's
`allowed_tools` allowlist. That's filtering at the model client, not
the server. Split it so the tool surface is structural:

1. **Server change.** Add an `AGENT_PROFILE` env var
   (`submissions` | `tax_sme` | `legal_sme`). Move tool registration
   behind a profile filter so a process started with
   `AGENT_PROFILE=tax_sme` simply doesn't `@mcp.tool()`-register
   `create_project`/`submit_questions`/`update_project_status`.

2. **Tool matrix:**

   | Tool | submissions | tax_sme | legal_sme |
   |---|---|---|---|
   | `get_routing` | ✓ |  |  |
   | `create_project` | ✓ |  |  |
   | `submit_questions` | ✓ |  |  |
   | `update_project_status` | ✓ |  |  |
   | `get_project` | ✓ | ✓ | ✓ |
   | `get_my_assignments` |  | ✓ | ✓ |
   | `get_question` |  | ✓ | ✓ |
   | `save_draft` |  | ✓ | ✓ |
   | `submit_answer` |  | ✓ | ✓ |
   | `update_question_status` |  | ✓ | ✓ |
   | `assign_question` |  | ✓ | ✓ |
   | `set_question_classification` |  | ✓ | ✓ |

3. **Infra.** Parameterize `infra/modules/mcp-app.bicep` over
   `agentProfile` (and `appName`); deploy 3 Container Apps from the
   same image:
   - `ca-mpwflow-dev-mcp-submissions`
   - `ca-mpwflow-dev-mcp-tax`
   - `ca-mpwflow-dev-mcp-legal`

   Each gets its own UAMI and Cosmos Data Contributor RBAC.

4. **Agent re-registration.** Update each `create_agent.py` to point
   at its dedicated URL and drop the `allowed_tools` parameter — no
   longer needed because the server doesn't expose the others.

5. Re-run smoke tests + e2e.

### C. Magentic group-chat orchestrator + UI

The end user should talk to **all three agents in one conversation**,
not pick a surface. Use the **Microsoft Agent Framework** Magentic
pattern (`MagenticBuilder`) as the orchestrator; it plans turns and
calls participant agents. The user only ever sees the orchestrator.

This is split into **two parallel-friendly todos**:

#### C1. Magentic chat **API** (`magentic-chat-api`)

1. **Participants:** the existing 3 Foundry agents (submissions, tax,
   legal) wrapped as Magentic participants via the Foundry connector.
2. **Service:** FastAPI hosting `MagenticBuilder`. Endpoints:
   - `POST /sessions` — start a session (bootstraps with
     `get_my_assignments` for the signed-in user).
   - `POST /sessions/{id}/messages` — submit a user turn.
   - `GET  /sessions/{id}/stream` — SSE stream of orchestrator
     events: agent turn started/finished, tool call, approval
     required, final reply.
   - `POST /sessions/{id}/approvals/{requestId}` — resume after a
     `RequestInfoExecutor` pause (Approve / Edit / Reject).
3. **HITL — in-session approvals:** `RequestInfoExecutor` interrupts
   before destructive calls (`create_project`, `submit_questions`,
   `submit_answer`); UI prompts; resume on approval POST.
4. **HITL — cross-user handoff:** session start calls
   `get_my_assignments(user=<easy-auth-email>)` so each user lands on
   their own work. Per-user sessions; state lives in Cosmos.
5. **Auth:** trust Container Apps Easy Auth — read
   `X-MS-CLIENT-PRINCIPAL` header; extract email/oid; pass as
   `user_id`/`by` to participant tool calls.
6. **Hosting:** Container App `ca-mpwflow-dev-chat-api` in the
   existing env. New `infra/modules/chat-api.bicep`.
7. **Smoke test:** HTTP-only Python script driving a 2-question
   project lifecycle through the API (no UI).

#### C2. Magentic chat **UI** (`magentic-chat-ui`)

1. React + Vite SPA, single chat page.
2. Message stream with per-turn agent attribution (submissions / tax
   / legal / orchestrator), markdown rendering, code blocks, tool
   call collapsibles.
3. Approval modal — rendered when API emits `approval-required`,
   buttons for Approve / Edit / Reject.
4. Assignments sidebar — populated from session bootstrap response.
5. Auth: relies on Container Apps Easy Auth at the edge. SPA does
   not handle login itself.
6. Talks to `magentic-chat-api` via `fetch` + `EventSource`.
7. Built and served either from the same container as the API
   (Vite build → `static/` mounted by FastAPI) or a sidecar nginx —
   sub-agent picks at build time.
8. **Smoke test:** Playwright script submits a 2-question project
   and approves the SME drafts.

#### Future state

Foundry's multi-agent orchestration (Connected Agents → broader
patterns) is still maturing. When it's ready, the orchestrator can
move into Foundry. Participant agents and MCP backends don't change
in that migration.

### D. Migrate submissions-agent to versioned Agents API

`submissions-agent` was created with the legacy `AgentsClient` v1
("Assistants"-style, id `asst_…`). It works but **does not appear in
the new Foundry portal Agents tab**. Tax/legal use the newer
`agents.create_version` API and do appear.

Steps:
1. Rewrite `agents/submissions/create_agent.py` to use
   `AIProjectClient.agents.create_version` (mirror `agents/tax/`).
2. Delete the legacy `asst_WaSjHasHGpi72F3CPfJg1WLZ` from the project
   to avoid duplicates.
3. Re-run `create_agent.py` and `smoke_test.py` for submissions.
4. Update `agents/e2e_smoke_test.py` if it references the old
   assistant id directly (use name lookup, not id).

## Iteration 3 (deferred)

### E. WorkIQ user-identity (OBO)

Add **one** delegated WorkIQ tool — `workiq_get_user` — so all three
agents can answer "who am I?" / "who is the caller?" using the
signed-in user's WorkIQ profile. **No mailbox access, no email search
in this iteration** — strictly profile lookup.

Token flow:

```
user (Entra)
  └─► chat-app (Easy Auth → has user bearer token)
        └─► Foundry agent (forward as MCP header X-User-Token)
              └─► mcp-{profile} server
                    ├─► validate user token
                    ├─► OBO swap via MSAL (confidential client) → WorkIQ-scoped token
                    └─► call WorkIQ as the user
```

Pieces:

1. **Entra app registration** for the chat-app with delegated
   `WorkIQ.User.Read` scope (verify exact scope name); request admin
   consent.
2. **Chat-app**: forward the Easy Auth access token to participant
   Foundry agents on each turn (custom MCP request header
   `X-User-Token`).
3. **MCP server middleware** (`tax_sme`, `legal_sme`, and
   `submissions` profiles): inspect `X-User-Token`, do MSAL OBO
   exchange (client id/secret from Entra app, secret in Key Vault),
   cache the WorkIQ token per user/oid for its lifetime.
4. **New MCP tool** `workiq_get_user` — returns
   `{displayName, email, oid, manager, org, ...}` from WorkIQ.
   Registered in **all three** profile filters.
5. **System-prompt updates** in all three agents — call
   `workiq_get_user` at session start when caller identity isn't
   already known, or whenever the user asks "who am I?". Use the
   returned email as `user_id` / `by` for subsequent tool calls
   (replacing the current "user states their email" pattern).
6. **Cosmos UAMI flow stays the same** — only WorkIQ tools use the
   OBO token; project/question/routing tools still use the server's
   managed identity to Cosmos.
7. **Docs**: update architecture diagram + add a token-flow sequence
   diagram + scopes/permissions table to PLAN.md.
8. **Smoke test**: in chat, "who am I?" returns the real signed-in
   user's WorkIQ profile.

**Out of scope for this slice:** WorkIQ mailbox search, calendar,
files, Graph access. Those would require additional scopes + admin
review; revisit in a later iteration if needed.

### F. Teams proactive notifications

Today the magentic-chat is pull-based: an SME has to log in to
discover new assignments. Iteration 3 adds proactive push:

1. Teams app + Bot Framework bot.
2. Cosmos change feed → Function/Logic App → bot proactive message
   to the assigned SME ("You have a new tax question on
   project X — open it").
3. Deep-link to the magentic-chat URL with project/question
   pre-loaded.
4. Requires Teams app manifest, per-user install, and conversation
   reference storage (Cosmos container `bot_state` or table storage).

## Iteration 4 — Registered FoundryAgent + OAuth identity passthrough

### Why

Today (`chat-api:0.3.1`) the backend uses Microsoft Agent Framework with
`FoundryChatClient` + an **ephemeral** `Agent(...)` per session. Foundry runs
the model and the MCP tool, but the registered agents (`submissions-agent v4`,
`tax-sme-agent v5`, `legal-sme-agent v5`) are never loaded. Portal edits don't
take effect; we lose evals/governance scoped to a registered agent; and we
fake per-user identity by injecting a custom `X-User-Token` header that
nothing on the MCP side validates.

This iteration switches the runtime to **the registered Foundry agents** via
`agent_framework.foundry.FoundryAgent` and lets **Foundry's hosted MCP do
OAuth identity passthrough** — Foundry stores each user's token after one
consent and attaches it to every MCP call. The MCP server validates the
bearer and uses the real `oid`/`upn` for `by:` audit and (later, in the
deferred WorkIQ slice) for OBO.

References (canonical):

- `agent_framework.foundry.FoundryAgent` —
  https://github.com/microsoft/agent-framework/blob/main/python/packages/foundry/agent_framework_foundry/_agent.py
- Sample —
  https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/providers/foundry/foundry_agent_basic.py
- Foundry MCP auth doc —
  https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication

### Target architecture

```
Maya (browser)
  └─► chat-ui  (Easy Auth → Maya signed in via Entra)
        └─► chat-api  (FoundryAgent(agent_name="tax-sme-agent", agent_version="5"))
              └─► Foundry Agent Service
                    ├─► invokes registered tax-sme-agent v5
                    └─► calls hosted MCP connection (custom OAuth)
                          ├─► first call per user → oauth_consent_request
                          │     (consent_link surfaces back through chat-api SSE
                          │      → chat-ui modal → Maya signs in)
                          └─► after consent → Authorization: Bearer <Maya>
                                └─► mcp-tax  (FastMCP)
                                      ├─► validate JWT against
                                      │     api://mpwflow-mcp/access_as_user
                                      ├─► extract oid/upn → request context
                                      └─► tool body uses upn as `by:`,
                                          calls Cosmos via UAMI (unchanged)
```

What changes vs. 0.3.1:

- chat-api builds `FoundryAgent(agent_name=..., agent_version=...)` instead
  of `Agent(client=FoundryChatClient(...), instructions=..., tools=[...])`.
- chat-api no longer minted an MCP tool with a fake `X-User-Token` header —
  Foundry handles the Authorization header itself.
- MCP server gains an Entra JWT validator (FastMCP middleware).
- Foundry MCP connections are recreated with **custom OAuth** referencing a
  new Entra app (`mpwflow-mcp`) and its `access_as_user` scope.
- Registered agents re-rolled (`agents.create_version`) using those OAuth-
  configured connections + a `require_approval` HITL map (write tools
  require approval, reads don't).

### Pieces (build order)

#### 1. Entra app registration `mpwflow-mcp`

- New app reg in the same tenant as Foundry. (Decision Q1 below.)
- Application ID URI: `api://mpwflow-mcp`.
- Expose one scope: `access_as_user` (admin + user consent).
- Redirect URI: the one Foundry shows after creating each custom-OAuth
  connection (one redirect URI per connection — Foundry will give us 3, one
  per profile, OR one shared if we use one connection).
- Confidential client: yes (client secret, store in Key Vault).
- Auth URL: `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize`
- Token URL: `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`
- Scopes for the MCP connections: `api://mpwflow-mcp/access_as_user offline_access`
- Admin-grant scope to the chat-ui's Easy Auth app reg so the user-consent
  step is one click (or pre-grant tenant-wide).

#### 2. MCP server: Entra bearer validation

In `mcp-server`:

1. Add dependency: `PyJWT[crypto]` + `httpx` (likely already present).
2. New module `mcp_server/auth.py`:
   - Cache JWKS from
     `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` (refresh
     hourly).
   - Validate `iss` (`https://sts.windows.net/<tenant>/` *or*
     `https://login.microsoftonline.com/<tenant>/v2.0`), `aud`
     (`api://mpwflow-mcp` or its client GUID), `exp`, `nbf`, `signature`.
   - On success, return `{oid, upn, name, tid, scp}`.
3. FastMCP / FastAPI middleware that pulls `Authorization: Bearer …`,
   validates, stashes the user record on a contextvar.
4. New helper `current_user()` available to every tool body. Tools that take a
   `by:` parameter must default it to `current_user().upn` instead of trusting
   what the LLM sent. The `userId` arg passed by the agent is now informational
   only.
5. `/health` stays anonymous; everything under `/mcp` requires a valid token.
6. Smoke test: hit `/mcp/...` with no header → 401; with a bad header → 401;
   with a token from an MSAL `acquire_token_for_client` against the new scope
   (works in CI without a real user) → 200 and `by:` populated from `oid`.

#### 3. Foundry MCP connections — custom OAuth

For each profile (submissions, tax, legal):

1. In Foundry portal → project → Connections → new MCP connection
   (custom OAuth). Provide:
   - Server URL: existing `https://ca-mpwflow-dev-mcp-<profile>.../mcp`
   - Client ID: `mpwflow-mcp`
   - Client Secret: from Key Vault
   - Auth URL / Token URL: as above
   - Scope: `api://mpwflow-mcp/access_as_user offline_access`
2. Copy the Foundry-provided redirect URI back into the app reg redirect URIs.
3. (Optional) Single connection vs. three — see Decision Q3.

#### 4. Re-register the 3 agents

In `agents/{submissions,tax,legal}/create_agent.py`:

- Replace the existing `MCPTool(server_url=..., require_approval="never")`
  with one that references the **OAuth-configured connection** (not a raw
  `server_url`). Per the SDK, this means a `connection_id` or named
  connection rather than `server_url=...`.
- Add `require_approval` map per agent. Suggested:

  | Agent | always_require_approval | never_require_approval |
  |---|---|---|
  | submissions | `create_project`, `submit_questions`, `update_project_status` | `get_routing`, `get_project` |
  | tax / legal | `submit_answer`, `update_question_status`, `assign_question`, `set_question_classification`, `save_draft` | `get_my_assignments`, `get_question`, `get_project` |

- Run `create_agent.py` for each → bumps to v5/v6/v6 (auto-incremented).
- Update Foundry agents `tax-sme-agent` v6, `legal-sme-agent` v6,
  `submissions-agent` v5 will be the new pinned versions.

#### 5. chat-api → `FoundryAgent`

Files (all under `chat-api/src/chat_api/`):

- `config.py`:
  - Drop / repurpose `AGENT_TO_MCP_PROFILE` (no longer used directly — the
    MCP wiring lives on the registered agent now).
  - Add `AGENT_TO_FOUNDRY_NAME = {"submissions": "submissions-agent",
    "tax": "tax-sme-agent", "legal": "legal-sme-agent"}`.
  - Add `AGENT_TO_FOUNDRY_VERSION` (env-driven, defaults pinned to the
    versions from step 4).
- `af_orchestrator.py`:
  - Delete `make_mcp_tool()` (no longer needed — MCP is server-side on the
    registered agent).
  - Replace `make_agent()` with:

    ```python
    from agent_framework.foundry import FoundryAgent

    def make_agent(fs, cfg, agent_id):
        return FoundryAgent(
            project_endpoint=cfg.foundry_project_endpoint,
            agent_name=AGENT_TO_FOUNDRY_NAME[agent_id],
            agent_version=AGENT_TO_FOUNDRY_VERSION[agent_id],
            credential=cfg.credential,
        )
    ```
  - `_system_prompt()` → delete (instructions live on the registered agent).
  - Keep the per-session `FoundrySession` wrapper (thread state).
- `agents.py`:
  - `load_system_prompt` → delete (or keep as a fallback for tests).
- Approvals: `result.user_input_requests` pattern still works — `FoundryAgent`
  inherits from `BaseAgent`. The contract to chat-ui doesn't change.
- New SSE event types to plumb through:
  - `oauth-consent-required` — emit when `oauth_consent_request` appears in
    the result stream. Payload: `{consent_link, response_id}`.
  - chat-ui modal "Click to sign in to MCP" → on close → POST
    `/sessions/{id}/oauth-resume` with `response_id` → chat-api re-issues
    `previous_response_id=...` to continue the same agent run.

#### 6. chat-ui

- New `OAuthConsentModal.tsx`. When SSE delivers `oauth-consent-required`,
  open the consent link in a popup window; when the popup closes (or the
  user clicks "I'm done"), POST resume.
- Display "Connected as: maya@contoso.com" in the chat header (from
  `X-MS-CLIENT-PRINCIPAL`).
- No other UX changes.

#### 7. Migration & smoke

1. Deploy MCP servers with the new auth middleware in **enforcement-warn**
   mode first (validate token if present, log; don't 401). Verify via
   `/health` and an authenticated curl.
2. Flip MCP servers to enforcement (401 on bad/missing token).
3. Create the OAuth connections in Foundry, re-register all 3 agents.
4. Deploy chat-api 0.4.0 with `FoundryAgent`.
5. Smoke as 2 different test users:
   - Both must complete consent once.
   - Each user's `submit_answer` writes `by: <their upn>` (verify in Cosmos).
   - HITL approval modal fires for write tools, not for reads.
6. Confirm in Foundry portal that runs are attributed to
   `tax-sme-agent v6` (registered), not an ephemeral agent.

### Open decisions (please confirm before execution)

**Q1.** Entra app registration — new dedicated `mpwflow-mcp` app or reuse
existing chat-ui Easy Auth app reg?

**Q2.** Tenant-wide admin consent for `access_as_user` (zero-friction for
end users), or per-user consent on first use (default)?

**Q3.** Single shared MCP connection in Foundry (one OAuth connection used
by all 3 agents) or three connections (one per profile)? Single is simpler;
three lets us scope tools by connection if Foundry adds that capability.

**Q4.** HITL approval map — does the suggested table above match your
intent, or do you want certain write tools to be auto-approved? (e.g.,
`save_draft` is non-destructive — could be `never_require_approval`.)

**Q5.** Foundry agent version pinning — pin to specific versions
(`tax-sme-agent v6`) for reproducibility, or auto-latest (`agent_version=None`)
so portal edits flow through immediately? Recommendation: env-pinned per
deployment (dev=auto-latest, prod=pinned).

**Q6.** Smoke tests — currently call MCP unauthenticated. After this
iteration, smoke tests need an MSAL `acquire_token_for_client` against the
new scope (using a service principal with admin-consented `.default`).
Confirm we add a service principal + `application` permission to the app
reg for CI.

### Out of scope for this iteration

- WorkIQ tool. (The new bearer flow makes it trivial later — MCP does OBO
  swap from `access_as_user` to WorkIQ scope.)
- Network restriction of MCP (still public, just authenticated).
- Per-tool consent. Foundry's consent is per connection, not per tool.
- Replacing user identity in routing logic (still uses Cosmos
  `routing` doc; the upn is now sourced from the validated token, not from
  the LLM's argument).

### Todos for this iteration

To be reflected in SQL `todos` after the user approves:

- `entra-app-mpwflow-mcp` — create the app reg + scope + redirect URIs
- `mcp-bearer-validation` — JWT middleware in mcp-server, deploy in warn-mode
- `mcp-bearer-enforce` — flip mcp-server to 401 on missing/invalid token
- `foundry-mcp-connections-oauth` — create custom-OAuth MCP connections in Foundry
- `re-register-3-agents` — `create_agent.py` updates with OAuth connection + HITL map
- `chat-api-foundry-agent` — switch chat-api to `FoundryAgent`, plumb consent events
- `chat-ui-consent-modal` — render `oauth_consent_request` link
- `migration-smoke` — 2-user e2e smoke with Cosmos audit verification

## Iteration 5 — End-user identity passthrough (MSAL + OBO + FIC) so WorkIQ works

### Why

Iteration 4 shipped: registered Foundry agents are loaded, all 3 agents now
have the **WorkIQUser** MCP tool, and system prompts call `GetMyDetails`
instead of asking for the user's email. **But** every WorkIQ tool invocation
fails with:

```
ARA OBO token request failed with status BadRequest
```

Foundry's Agent Run Authority (ARA) tries to OBO-swap the **end user's**
Entra token into a WorkIQ token. Today chat-api authenticates to Foundry
with its **UAMI** — UAMIs cannot be the subject of an OBO swap, so ARA
returns 400. WorkIQ MCP fundamentally requires per-user identity, end-to-
end.

### Architecture (mirrors `ropesandgray` reference repo, ported to Python)

> **Important — what we are NOT changing.** The custom group-chat
> orchestrator in `chat_api/af_orchestrator.py` (router + handoff loop in
> `run_turn`, one agent per turn, handoff via `ctx.next_agent`) stays
> exactly as it is. Participants stay as registered Foundry agents
> loaded via `FoundryAgent(agent_name=..., agent_version=None,
> allow_preview=True)`. The orchestrator still emits the same SSE events
> (`router_decision`, `participant`, `agent_message`, etc.).
> **The only thing that changes is the `credential` argument** that
> gets passed to `FoundryAgent(...)` and `AgentSession(...)` — today
> it's the chat-api UAMI, tomorrow it's a per-request
> `OnBehalfOfCredential` built from the signed-in user's JWT. The
> `ropesandgray` repo is the reference for **how to plumb that
> credential in** (MSAL on the SPA → JWT bearer on the API →
> FIC-secretless OBO swap), not for the agent runtime.

```
                Maya in browser
                      │
                      ▼  MSAL.js: PKCE auth-code → SPA app reg
              ┌───────────────┐
              │ chat-ui (SPA) │
              │ msal-browser  │
              └──────┬────────┘
                     │ acquireTokenSilent({
                     │   scopes:["api://<backend-id>/Chat.ReadWrite"]
                     │ })
                     │
                     │ fetch /api/... with
                     │   Authorization: Bearer <user JWT for backend audience>
                     ▼
        ┌─────────────────────────────┐
        │ chat-api (Python/FastAPI)   │
        │                             │
        │ 1. PyJWT validate (JWKS)    │  ← issuer=tenant, aud=api://<backend-id>
        │    require scp=Chat.ReadWrite
        │                             │
        │ 2. Build per-request:       │
        │    OnBehalfOfCredential(    │
        │      tenant_id,             │
        │      backend_client_id,     │
        │      client_assertion_func= │
        │        FIC(uami_token),     │  ← NO secret. UAMI federates with
        │      user_assertion=        │     backend app reg via FIC.
        │        <user JWT>           │
        │    )                        │
        │                             │
        │ 3. FoundryAgent(            │
        │      credential=obo_cred,   │  ← acquires Foundry-audience token
        │      agent_name=...         │     for Maya, not for the UAMI
        │    ).run(...)               │
        └────────────┬────────────────┘
                     │ HTTPS to Foundry as Maya
                     ▼
            ┌─────────────────────────────────┐
            │ chat-api group-chat orchestrator│
            │ (router + handoff loop, one     │
            │  registered agent per turn —    │
            │  UNCHANGED)                     │
            ├─────────────────────────────────┤
            │ - submissions / tax / legal     │ ← Agent Framework
            │   FoundryAgent participants     │   participants
            │ - workflow MCP                  │ ← called as Maya (audit OK)
            │ - WorkIQ MCP                    │ ← ARA OBOs Maya's token →
            └─────────────────────────────────┘   Agent365 → GetMyDetails
                                                  returns Maya's profile
```

**Key design decisions copied from ropesandgray:**

1. **NO Easy Auth.** Auth is at the application layer. Backend validates the
   JWT itself; frontend uses MSAL.js. This avoids the brittleness of the
   token store and lets us control the audience precisely.
2. **NO client secret.** Backend OBO uses **Federated Identity Credential
   (FIC)** — the chat-api UAMI gets an MI token, signs that as a client
   assertion, and exchanges it (plus the user's token) at the token
   endpoint. Secret rotation = zero.
3. **Two app regs**: SPA (`mpwflow-spa`) and backend (`mpwflow-api`).
   Backend exposes one delegated scope: `Chat.ReadWrite`.
4. **Per-request `AgentSession` + `FoundryAgent`** in OBO mode (cannot
   cache because each user gets their own credential). The custom
   group-chat orchestrator (`run_turn` + handoff loop) still wires the
   same 3 participants in the same way — the only difference is the
   `credential` it hands them. Caching of the resolved registered-agent
   versions (`get_resolved_version`) is fine to keep because version
   metadata is not user-scoped.

### Pieces (build order)

#### 1. Two Entra app registrations

**App reg `mpwflow-api` (backend):**
- Single tenant.
- **Expose an API**:
  - Application ID URI: `api://<backend-app-id>`.
  - Scope: `Chat.ReadWrite` (admin + user consent, displayed as
    "Read and write chat sessions").
- **API permissions (delegated, on this app reg's behalf)**:
  - `Azure AI / user_impersonation` (resource `https://ai.azure.com`)
  - `Microsoft Graph / User.Read`
  - WorkIQ scope (Q1 — see open questions; likely something on
    `https://substrate.office.com/...` or
    `https://agent365.svc.cloud.microsoft/...`)
  - Admin grant all tenant-wide.
- **Federated Credentials**: add **one** federated credential with
  - Issuer: `https://login.microsoftonline.com/<tenant-id>/v2.0`
  - Subject: the chat-api UAMI's **principalId** in the form
    `<uami-object-id>` (verify exact subject format with
    `az identity federated-credential` docs)
  - Audience: `api://AzureADTokenExchange`
  - This is what makes the OBO swap secretless.

**App reg `mpwflow-spa` (frontend):**
- Single tenant.
- **Authentication**:
  - SPA platform with redirect URIs:
    - `https://ca-mpwflow-dev-chat-ui.icyground-4e2c6fde.eastus2.azurecontainerapps.io/`
    - `http://localhost:5173/` (dev)
- **API permissions (delegated)**:
  - `mpwflow-api / Chat.ReadWrite` (the scope you just exposed)
  - Admin grant.
- No client secret, no certificate.

#### 2. chat-api code changes

**Dependencies** (`chat-api/pyproject.toml`):

```
PyJWT[crypto]>=2.8
azure-identity>=1.19   # already present, but needs OnBehalfOfCredential
                       #   + ClientAssertionCredential + ManagedIdentityCredential
cryptography           # transitive — required for JWKS
```

**`chat-api/src/chat_api/auth.py`** — replace dev-bypass logic with real
JWT validation:

```python
import jwt
from jwt import PyJWKClient

class TokenValidator:
    def __init__(self, tenant_id: str, audience: str, required_scope: str):
        self._jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._audience = audience
        self._required_scope = required_scope

    def validate(self, bearer: str) -> dict:
        signing_key = self._jwks.get_signing_key_from_jwt(bearer).key
        claims = jwt.decode(
            bearer,
            signing_key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["exp", "aud", "iss", "sub"]},
        )
        scopes = (claims.get("scp") or "").split()
        if self._required_scope not in scopes:
            raise PermissionError(f"missing scope {self._required_scope}")
        return claims

def caller_from_request(request, validator: TokenValidator) -> Caller:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer")
    claims = validator.validate(auth.split(None, 1)[1].strip())
    return Caller(
        oid=claims.get("oid"),
        email=claims.get("preferred_username") or claims.get("upn"),
        display_name=claims.get("name"),
        raw_token=auth.split(None, 1)[1].strip(),
    )
```

**New `chat_api/foundry_credential.py`** (the OBO factory):

```python
from azure.identity import (
    OnBehalfOfCredential,
    ManagedIdentityCredential,
    ClientAssertionCredential,
    DefaultAzureCredential,
)

class UserCredentialFactory:
    """
    Builds an OnBehalfOfCredential per request using:
      - the user's bearer token (user_assertion)
      - a Federated Identity Credential client assertion derived from
        chat-api's UAMI (NO client secret).
    """
    def __init__(self, tenant_id: str, backend_client_id: str, uami_client_id: str):
        self._tenant = tenant_id
        self._client_id = backend_client_id
        # MI credential used to fetch the FIC assertion. The exchange audience
        # MUST be api://AzureADTokenExchange (Entra's well-known FIC audience).
        self._mi = ManagedIdentityCredential(client_id=uami_client_id)

    def _get_fic_assertion(self) -> str:
        token = self._mi.get_token("api://AzureADTokenExchange/.default")
        return token.token

    def for_user(self, user_jwt: str) -> OnBehalfOfCredential:
        return OnBehalfOfCredential(
            tenant_id=self._tenant,
            client_id=self._client_id,
            client_assertion_func=self._get_fic_assertion,
            user_assertion=user_jwt,
        )
```

**`chat_api/af_orchestrator.py`** — switch `make_foundry_session` and
`make_agent` to per-request user credentials:

```python
def make_foundry_session(cfg, *, user_credential: TokenCredential | None):
    cred = user_credential or cfg.credential   # cfg.credential = UAMI; dev-only fallback
    return AgentSession(credential=cred, project_endpoint=cfg.foundry_project_endpoint)

def make_agent(fs, agent_id: str):
    return FoundryAgent(
        project_endpoint=fs.project_endpoint,
        agent_name=AGENT_TO_FOUNDRY_NAME[agent_id],
        agent_version=None,
        credential=fs.credential,        # ← already per-user from above
        allow_preview=True,
    )
```

**`chat_api/routes/sessions.py`** — wire it together:

```python
@router.post("/sessions")
def create_session(request: Request, cfg: Config = Depends(...)):
    caller = caller_from_request(request, cfg.token_validator)
    user_cred = cfg.user_cred_factory.for_user(caller.raw_token)
    fs = make_foundry_session(cfg, user_credential=user_cred)
    # store both caller AND user_cred on the session record so subsequent
    # /messages and /stream calls can re-issue with a fresh token from the
    # next request's Authorization header (tokens expire ~1h)
    ...
```

For `/messages` and `/stream`, the user re-presents the bearer on every
request — so build a fresh OBO credential per request and inject it
into the participant agents (don't hold the OBO credential past the
request).

#### 3. chat-ui changes (MSAL.js)

**Dependencies** (`chat-ui/package.json`):

```
"@azure/msal-browser": "^3",
"@azure/msal-react":   "^2"
```

**Wire MSAL** in `chat-ui/src/main.tsx`:

```tsx
const msal = new PublicClientApplication({
  auth: {
    clientId: import.meta.env.VITE_SPA_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_TENANT_ID}`,
    redirectUri: window.location.origin,
  },
  cache: { cacheLocation: "sessionStorage" },
});

createRoot(...).render(
  <MsalProvider instance={msal}>
    <AuthGate><App/></AuthGate>
  </MsalProvider>
);
```

`AuthGate` redirects unauthenticated users to MSAL login. After login,
every fetch to chat-api goes through a wrapper:

```ts
async function authFetch(input: RequestInfo, init: RequestInit = {}) {
  const account = msal.getAllAccounts()[0];
  const result = await msal.acquireTokenSilent({
    account,
    scopes: [`api://${import.meta.env.VITE_API_CLIENT_ID}/Chat.ReadWrite`],
  });
  return fetch(input, {
    ...init,
    headers: {
      ...(init.headers || {}),
      Authorization: `Bearer ${result.accessToken}`,
    },
  });
}
```

For the SSE `EventSource` (which doesn't support custom headers):
- Either swap to `fetchEventSource` from `@microsoft/fetch-event-source`
  (supports headers) — recommended.
- OR pass the token as a one-time query param like `?at=<jwt>` —
  works but logs the token in any access logs. Avoid.

Add a "signed in as &lt;name&gt;" chip and a logout button.

#### 4. Infra changes

**`infra/main.bicep` / chat-api module**:
- Drop `DEV_BYPASS_AUTH=true` env var.
- Add new env vars:
  - `ENTRA_TENANT_ID`
  - `ENTRA_BACKEND_CLIENT_ID` (= `mpwflow-api` app reg id)
  - `ENTRA_REQUIRED_SCOPE=Chat.ReadWrite`
  - `MANAGED_IDENTITY_CLIENT_ID` (existing UAMI, used for FIC assertion)
- No new secrets.

**`infra/main.bicep` / chat-ui module**:
- Add build-time env vars (Vite reads `VITE_*` at build time):
  - `VITE_TENANT_ID`
  - `VITE_SPA_CLIENT_ID` (= `mpwflow-spa` app reg id)
  - `VITE_API_CLIENT_ID` (= `mpwflow-api` app reg id)

These get passed as `--build-arg` to the chat-ui Dockerfile.

**No Container Apps Easy Auth** is enabled. Both apps stay open at the
ingress level; auth is enforced inside chat-api.

#### 5. Observability bonus (steal from ropesandgray)

Add middleware in chat-api that tags the OpenTelemetry current span with
`enduser.id = <oid>` so Foundry traces and App Insights end-to-end
transactions can be filtered per user. Trivially small change — see
`Program.cs:169-179` in ropesandgray.

#### 6. Smoke

1. Build the new chat-api + chat-ui images, deploy.
2. Open chat-ui in a fresh incognito browser. Get redirected to
   `login.microsoftonline.com`. Sign in as `userA@<tenant>`. Land back
   on chat-ui.
3. Send "Hi" → submissions agent should call `GetMyDetails` → return
   userA's profile → greet by name.
4. Open Foundry portal → traces tab → confirm the run shows userA's
   `enduser.id` in span attributes (not the UAMI).
5. Sign out, sign back in as userB, verify `GetMyDetails` returns
   userB.
6. Curl chat-api directly without a bearer → 401.

### Open questions

**Q1.** Which exact scope does the `WorkIQUser` Foundry connection
expect on the user token? Need to inspect the connection in Foundry
portal (`Connections → WorkIQUser → Configuration`). Likely something
on `https://substrate.office.com/...` or
`https://agent365.svc.cloud.microsoft/...`. This drives the API
permissions list on `mpwflow-api`.

**Q2.** Federated Credential subject format for a UAMI. Verify against
[`az identity federated-credential create`](https://learn.microsoft.com/cli/azure/identity/federated-credential)
docs — the subject must match what Entra sends in the MI-issued JWT
(typically the UAMI's principal-id).

**Q3.** Conversation/session continuity across token refreshes: if the
user's MSAL silent refresh fails (e.g., session timeout), should
chat-api preserve the session and 401 the next request, or invalidate
the session? Recommendation: 401, surface a re-login prompt, preserve
session.

**Q4.** Local dev: stand up MSAL against `localhost:5173` and run
chat-api with `OnBehalfOfCredential` (FIC won't work locally without an
MI — use a client secret OR `ChainedTokenCredential` like ropesandgray
does for dev). Recommendation: dev-only env var
`DEV_USE_AZURE_CLI_CRED=true` that swaps `make_foundry_session` to
`AzureCliCredential` and skips OBO. Document that WorkIQ won't work in
that mode.

### Out of scope (for THIS iteration)

- Workflow MCP server bearer validation (mpwflow servers stay open).
  Hardening those is a follow-up.
- HITL approval gates (still `require_approval=never`).
- Cross-tenant users.
- B2B guest accounts (may work, may need adjustments).

### Migration risks

- chat-api 0.4.0 will hard-401 anything without a bearer once the new
  build deploys. Existing browser sessions (cookies / `?as_user=` query
  param) immediately stop working.
- The 100s default Azure SDK NetworkTimeout has bitten ropesandgray —
  pre-emptively bump to 230s on `AIProjectClient` (env
  `AGENT_NETWORK_TIMEOUT_SECONDS=230`) to avoid spurious failures on
  long agent runs.
- FIC token caching: `ManagedIdentityCredential` caches its token; the
  exchange audience `api://AzureADTokenExchange/.default` is the
  well-known FIC exchange audience. Don't customize it.

### Todos for this iteration

- `entra-app-mpwflow-api` — create backend app reg, expose
  `Chat.ReadWrite`, add API permissions, admin grant
- `entra-app-mpwflow-spa` — create SPA app reg with redirect URIs,
  delegated `Chat.ReadWrite` permission, admin grant
- `entra-fic-uami` — register the chat-api UAMI as a Federated
  Credential on the backend app reg
- `chat-api-jwt-validate` — add PyJWT + JWKS, replace dev-bypass with
  bearer validation in `auth.py`
- `chat-api-obo-credential` — `UserCredentialFactory` building
  `OnBehalfOfCredential` per request with FIC assertion
- `chat-api-per-request-cred` — refactor `make_foundry_session` /
  `make_agent` / route handlers to use per-request user credential
- `chat-api-network-timeout` — env-driven 230s timeout on
  `AIProjectClient` (FoundryAgent client options)
- `chat-api-otel-enduser` — middleware to tag spans with
  `enduser.id`/`user.oid`
- `chat-ui-msal` — install msal-browser/msal-react, AuthGate, authFetch
  wrapper, signed-in chip, logout
- `chat-ui-sse-with-token` — swap EventSource to
  `@microsoft/fetch-event-source` to send Authorization on SSE
- `infra-app-reg-vars` — pipe app reg ids and tenant id into chat-api
  env + chat-ui build args
- `workiq-scope-discovery` — figure out WorkIQ scope (Q1) and add to
  backend app reg API permissions
- `migration-smoke-2-users` — incognito sign-in test with two distinct
  users; verify GetMyDetails returns the right identity each time



## Out of scope (intentional, for the next iteration)

- **Teams integration** — bot, proactive cards, Teams SSO.
- **Orchestration** — workflow that watches Cosmos and nudges
  agents/users. Right now everything is human-initiated.
- **WorkIQ + OBO** — agents drafting answers using the SME's M365
  context. Will require Teams SSO or a consent flow plus per-call OBO
  in the SME agents.
- **Auth on MCP server** — currently trusts the `by` / `userId`
  parameters. Will need either OBO from the calling agent or a token
  binding scheme.
- **Auto-completion of projects** — currently a manual
  `update_project_status` call.
- **Multi-reviewer workflows** — `awaiting_review` state exists but
  isn't used.

## Open questions to resolve during build

- **Foundry MCP integration shape** — confirm whether to register the
  MCP server via Foundry's MCP connector (HTTPS) or run MCP locally as
  a tool process. HTTPS preferred for shared use across all 3 agents.
- **Round-robin vs. simple-pick** — current plan increments a counter
  on every `get_routing`. Confirm fairness behavior is good enough,
  or move to least-loaded.
- **Reclassification side effects** — when an SME calls
  `set_question_classification`, should we always also clear
  `assignedUserId` to force a follow-up `assign_question` call?
  Default: no, leave the existing assignee.
- **History array growth** — bounded? Or keep appending forever?
  Default: keep appending; revisit if a question routinely exceeds
  ~50 history entries.

## Todos

Tracked in SQL (`todos` table). High-level groupings:

- Cosmos (account, db, containers, RBAC, routing seed).
- MCP server (12 tools, Cosmos repo, tests, ACA deploy).
- Submissions agent (Foundry registration, MCP wiring, prompt,
  classification logic).
- Tax SME agent (Foundry registration, MCP wiring, prompt).
- Legal SME agent (clone of tax with legal prompt).
- Smoke test end-to-end.
- Observability (App Insights logging from MCP server, audit
  history queries).
- **Iteration 2:** model upgrade to gpt-5.3, split MCP into 3
  profile-filtered backends, build submitter UI.
