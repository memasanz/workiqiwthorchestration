# Infrastructure (Bicep)

Provisions the Azure footprint for the multi-person workflow plan:

- **User-assigned managed identity** (`id-<base>-<env>-mcp`) — used by the
  MCP container app for both Cosmos data-plane access and ACR pull.
- **Log Analytics + App Insights** — workspace-based, 30-day retention.
- **Cosmos DB** (NoSQL, serverless) with database `workflow` and 3
  containers:
  - `projects` (pk `/projectId`)
  - `questions` (pk `/projectId`)
  - `routing` (pk `/category`)
- **Azure Container Registry** (Basic, admin disabled) — UAMI gets `AcrPull`.
- **Azure Container Apps environment** + an MCP **container app** with
  external HTTPS ingress on port 8080. Image is a placeholder until the
  MCP server is built and pushed.
- **Azure AI Foundry** account (modern `Microsoft.CognitiveServices/accounts`
  `kind: AIServices`, `allowProjectManagement: true`) plus a Foundry
  **project** and a **chat model deployment**. The user-assigned MI gets
  `Azure AI Developer` and `Cognitive Services User` on the account so it
  can create and run agents and call the model with Entra-only auth.
  - Model: `gpt-4o-mini` (`2024-07-18`), SKU `GlobalStandard`, **30 K TPM**.
  - Region: same as the resource group (`eastus2`). `gpt-4o-mini` /
    `GlobalStandard` is available there, so no region split was needed.
    If you change models and a region doesn't have it, override
    `foundryLocation` and the model params in `main.parameters.json` —
    the Foundry account can live in a different region than the rest.

Cosmos local-auth is disabled (`disableLocalAuth: true`) so the MCP
server *must* use the managed identity. No keys are used anywhere.

## Files

```
infra/
  main.bicep                    # orchestrator (resource-group scope)
  main.parameters.json          # baseName, environmentName
  modules/
    identity.bicep              # user-assigned managed identity
    monitoring.bicep            # Log Analytics + App Insights
    cosmos.bicep                # account + db + containers + RBAC
    registry.bicep              # ACR + AcrPull role assignment
    container-env.bicep         # Container Apps environment
    mcp-app.bicep               # MCP server container app
    foundry.bicep               # AI Foundry account + project + model + RBAC
```

## Deploy

Pre-reqs: Azure CLI logged in to the right subscription, Bicep CLI
installed (or rely on Azure CLI's auto-install).

```bash
RG=rg-mpwflow-dev
LOC=eastus2

az group create -n $RG -l $LOC

az deployment group create \
  -g $RG \
  -f infra/main.bicep \
  -p infra/main.parameters.json \
  -p mcpImage=mcr.microsoft.com/azuredocs/containerapps-helloworld:latest
```

After the first deploy the container app is running the placeholder
"hello world" image. Build the MCP server, push it to the ACR, then
re-run the deployment with the new image (or use
`az containerapp update`):

```bash
ACR=$(az deployment group show -g $RG -n main --query properties.outputs.registryName.value -o tsv)
az acr login -n $ACR

# (build & push your image, e.g.)
docker build -t $ACR.azurecr.io/mcp-server:0.1.0 ./mcp-server
docker push $ACR.azurecr.io/mcp-server:0.1.0

az deployment group create \
  -g $RG \
  -f infra/main.bicep \
  -p infra/main.parameters.json \
  -p mcpImage=$ACR.azurecr.io/mcp-server:0.1.0
```

## Outputs

After deploy:

| Output | What for |
|---|---|
| `cosmosEndpoint` | `COSMOS_ENDPOINT` env var (already set on the container app) |
| `cosmosDatabase` | `COSMOS_DATABASE` env var (already set on the container app) |
| `cosmosAccountName` | For `az cosmosdb` CLI commands |
| `registryLoginServer` | For `docker login`/`az acr login` |
| `registryName` | For `az acr` commands |
| `mcpAppFqdn` | The HTTPS endpoint Foundry will register as the MCP server URL |
| `mcpAppName` | For `az containerapp` commands |
| `userAssignedIdentityClientId` | Already injected as `AZURE_CLIENT_ID`; useful for local debugging |
| `foundryAccountName` / `foundryHubName` | Foundry (AIServices) account name |
| `foundryProjectName` | Foundry project name |
| `foundryProjectEndpoint` | `https://<account>.services.ai.azure.com/api/projects/<project>` — pass to `AIProjectClient` |
| `modelDeploymentName` | Chat model deployment name (default `gpt-4o-mini`) |

## Notes

- **Throughput**: Cosmos defaults to **serverless** to keep idle cost
  near zero. Switch to `autoscale` by passing
  `-p throughputMode=autoscale -p autoscaleMaxRu=1000`.
- **Ingress**: container app ingress is **external** so Foundry can
  reach it. Tighten with VNet integration + private endpoint later if
  needed.
- **Region**: defaults to the resource group's location.
- **Naming**: change `baseName` in `main.parameters.json` if `mpwflow`
  collides. ACR name appends `uniqueString(rg)` to stay globally unique.
- **Known first-deploy race**: Cosmos account creation occasionally
  reports success before the data plane is ready, so the database
  resource fails with *"the database account is in the process of
  being created"*. **Just re-run the same `az deployment group
  create` command** — the second pass succeeds because the account is
  fully ready by then. ARM is idempotent so nothing else is recreated.
