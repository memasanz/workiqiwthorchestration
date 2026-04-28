# Multi-Person Workflow MCP Server

Python FastMCP server exposing 12 tools backed by Azure Cosmos DB. Deployed to Azure Container Apps with managed identity to Cosmos.

## Tools

**Writes:** `create_project`, `submit_questions`, `submit_answer`, `update_project_status`, `update_question_status`, `assign_question`, `set_question_classification`, `save_draft`

**Reads:** `get_my_assignments`, `get_project`, `get_question`, `get_routing`

`get_routing` atomically advances a round-robin pointer and returns the chosen user.

## Local development

```powershell
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
pytest
```

## Run locally (against Cosmos)

```powershell
$env:COSMOS_ENDPOINT = "https://<acct>.documents.azure.com:443/"
$env:COSMOS_DATABASE = "workflow"
$env:AZURE_CLIENT_ID = "<uami-client-id>"
python -m mcp_server.server
```

The HTTP transport listens on `0.0.0.0:8080`. MCP endpoint is `/mcp/`; health probe is `/health`.

## Container build / deploy

```powershell
$ACR = "acrmpwflowdeva3qzr7isqw476"
az acr build -t "$ACR.azurecr.io/mcp-server:0.1.0" -r $ACR mcp-server
az containerapp update -g rg-mpwflow-dev -n ca-mpwflow-dev-mcp `
  --image "$ACR.azurecr.io/mcp-server:0.1.0"
```

## Environment

| Var | Purpose |
|---|---|
| `COSMOS_ENDPOINT` | Cosmos account URL |
| `COSMOS_DATABASE` | DB name (default `workflow`) |
| `AZURE_CLIENT_ID` | UAMI client ID for `DefaultAzureCredential` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Optional telemetry |
| `PORT` | HTTP port (default 8080) |
