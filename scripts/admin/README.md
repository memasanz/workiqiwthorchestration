# Admin scripts (run once per tenant)

These scripts are run by a tenant administrator before/after `azd up`.
They are deliberately separate from the developer-driven `azd up` flow
because they require **Entra (Azure AD) admin rights** that everyday
developers should not have.

## 1. Manual prerequisites in the Foundry portal

`azd up` does **not** provision Foundry. Before running it the admin
must, in the target Microsoft 365 tenant:

1. Create / pick an **Azure AI Foundry** project.
2. Deploy a chat model (e.g. `gpt-4o-mini`) and note the deployment name.
3. Add the two **WorkIQ catalog MCP connections**:
   - **Name**: `WorkIQUser` → `https://agent365.svc.cloud.microsoft/agents/servers/mcp_MeServer`
   - **Name**: `WorkIQMail` → `https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools`

   In the Foundry portal: *Project → Connections → + New connection →
   Microsoft Agent 365 → pick the catalog server → name it exactly
   `WorkIQUser` / `WorkIQMail`*.

   The connection name **must** match the `project_connection_id`
   passed by `agents/{tax,legal,submissions}/create_agent.py`.

## 2. Create Entra app regs (`setup-entra.ps1`)

```pwsh
# First run — create app regs and output IDs
./scripts/admin/setup-entra.ps1                   # uses default prefix "mpwflow"
# or with a custom prefix:
./scripts/admin/setup-entra.ps1 -Prefix myorg     # creates myorg-api + myorg-spa
# or override individual names:
./scripts/admin/setup-entra.ps1 -BackendAppName myorg-backend -SpaAppName myorg-frontend
# … records appIds. Paste them into .env (ENTRA_BACKEND_APP_ID + ENTRA_SPA_APP_ID).
```

After `azd up` has run once and produced the chat-api UAMI:

```pwsh
# Add the federated identity credential + finalise SPA redirect URIs
./scripts/admin/setup-entra.ps1 `
  -UamiPrincipalId <chatApiUamiPrincipalId>  ` # azd env get-values | grep CHAT_API_UAMI_PRINCIPAL_ID
  -ChatUiFqdn      <chatUiAppFqdn>           ` # azd env get-values | grep CHAT_UI_APP_FQDN
  -EnvSuffix       ncus                       # used to scope the FIC name
```

Idempotent — safe to re-run per region.

## 3. Tenant-admin consent (`grant-consent.ps1`)

A user with `Application Administrator` (or higher) runs this once per
tenant to grant admin consent for:
- backend → Microsoft Graph `User.Read`
- backend → Azure AI `user_impersonation`
- backend → WorkIQ `McpServers.Me.All`
- spa → backend `Chat.ReadWrite`

```pwsh
./scripts/admin/grant-consent.ps1
```

## Output file

`setup-entra.ps1` writes `scripts/admin/.app-reg-output.json` (gitignored)
which `grant-consent.ps1` reads.
