# Tax SME Foundry agent

The **Tax SME** agent helps a tax subject-matter expert triage and answer
questions assigned to them. It is one of three agents in the multi-person
workflow project; it talks to the shared MCP server (Cosmos-backed) and
**only** handles tax work.

## Files

| File | Purpose |
|---|---|
| `system_prompt.md` | Instructions baked into the agent. |
| `create_agent.py`  | Idempotently creates / updates the agent in Foundry. |
| `smoke_test.py`    | End-to-end conversation against the deployed agent. |

## Constants

- `PROJECT_ENDPOINT = https://aif-mpwflow-dev-a3qzr7isqw476.services.ai.azure.com/api/projects/proj-mpwflow-dev`
- `MODEL_DEPLOYMENT_NAME = gpt-4o-mini`
- `MCP_SERVER_URL = https://ca-mpwflow-dev-mcp.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp`
- `AGENT_NAME = tax-sme-agent`

## Allowed MCP tools

`get_my_assignments`, `get_question`, `get_project`, `save_draft`,
`submit_answer`, `update_question_status`, `assign_question`,
`set_question_classification`.

The agent never calls `create_project`, `submit_questions`, or
`update_project_status` (those belong to the Submissions agent), and never
queries `classification="legal"`.

## Run

```powershell
cd C:\Users\memasanz\repos\multipersonworkflow
if (-not (Test-Path .agentvenv)) {
  python -m venv .agentvenv
  .\.agentvenv\Scripts\python.exe -m pip install -U pip `
    azure-ai-projects azure-ai-agents azure-identity requests
}
.\.agentvenv\Scripts\python.exe agents\tax\create_agent.py
.\.agentvenv\Scripts\python.exe agents\tax\smoke_test.py
```

Auth uses `DefaultAzureCredential`; locally `az login` is enough.
