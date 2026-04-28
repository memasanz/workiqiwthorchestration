# chat-api (0.3.1 — FoundryChatClient + hosted MCP + HITL approval)

A small FastAPI **group-chat orchestrator** for the multipersonworkflow
project. It mediates a conversation between a human and three specialist
participants (`submissions`, `tax`, `legal`). Each participant is an
`agent_framework.Agent` backed by a per-session
`agent_framework.foundry.FoundryChatClient`. Each Agent is given a hosted
**`FoundryMCPTool`** pointing at the matching profile-filtered MCP backend,
configured with an explicit per-tool approval policy. Foundry executes MCP
calls server-side and surfaces `FunctionApprovalRequest` content for any
tool listed in `always_require_approval`, which we propagate to chat-ui via
the existing SSE contract.

## Architecture: `FoundryChatClient.get_mcp_tool(...)` + explicit approvals

```python
client = FoundryChatClient(project_endpoint=..., model="gpt-5_3-chat", credential=DefaultAzureCredential())
mcp = client.get_mcp_tool(
    name="submissions_mcp",
    url="https://.../submissions/mcp",
    approval_mode={
        "always_require_approval": ["create_project", "submit_questions",
                                    "submit_answer", "update_project_status"],
        "never_require_approval":  ["get_routing", "get_my_assignments", ...],
    },
    headers={"X-User-Token": "<inbound JWT>"},
)
agent = Agent(client=client, instructions=load_system_prompt("submissions"),
              name="submissions", tools=[mcp])
```

The approval-resume cycle follows the canonical pattern from
[`foundry_chat_client_with_hosted_mcp.py`](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/providers/foundry/foundry_chat_client_with_hosted_mcp.py):

```python
result = await agent.run(query, session=session)
while result.user_input_requests:
    contents = [req.to_function_approval_response(approved=...) for req in result.user_input_requests]
    result = await agent.run([Message(role="user", contents=contents)], session=session)
```

### Foundry agent registration: NOT required (Approach A)

We do **not** pre-register Foundry agents. Each `Agent(client=client, ...)`
spawns ephemeral runs that show up in the project for tracing / governance.
The previously registered `submissions-agent v4` / `tax-sme-agent v5` /
`legal-sme-agent v5` definitions are now superseded by these in-process
Agents — keep them in the project for historical traces, but they are no
longer the source of truth for persona or tool wiring. (To switch to
Approach B later — point Foundry at registered agents by id — we'd swap
the `Agent(client=client, ...)` factory for a registered-agent client; the
SSE / approval plumbing in this module is unchanged.)

### HITL gate sets (per brief)

| Always require approval | Never require approval (auto) |
|---|---|
| `create_project` | `get_routing`, `get_my_assignments`, `get_question`, `get_project` |
| `submit_questions` | `save_draft`, `update_question_status` |
| `submit_answer` | `assign_question`, `set_question_classification` |
| `update_project_status` | |

### Per-user `X-User-Token` forwarding

The inbound `Authorization: Bearer ...` (or `?as_user=` in dev) is captured
on `POST /sessions`, stashed on the `FoundrySession`, and passed as
`headers={"X-User-Token": <token>}` on **every** `client.get_mcp_tool(...)`
call (built fresh per session, per agent). The MCP backend already accepts
this header — the OBO swap on the MCP side is the next workiq-user item.

### Handoff

Each agent's system prompt instructs it to end with the literal sentinel
`[HANDOFF: <agent>]` if a peer is better suited. The orchestrator parses
the sentinel, emits a fresh `router_decision`, strips the sentinel from
the user-visible message, and re-runs the next agent. Capped at
`MAX_AGENT_RUNS_PER_TURN=6`.

### SDK pins

```
agent-framework==1.2.0
agent-framework-core==1.2.0
agent-framework-foundry==1.2.0
azure-ai-projects>=1.0.0b9
pydantic>=2.11,<3
```

Python 3.12 required. **Do not** use `agent-framework[all]` — the resolver
thrashes on extras.

## SSE event contract (preserved from 0.2.0/0.3.0)

```
event: session_start     data: {sessionId, caller, caller_email, assignments}
event: router_decision   data: {agents: ["<active>"]}
event: agent_turn_start  data: {turn, agent}
event: agent_message     data: {turn, agent, text}
event: tool_proposed     data: {turn, agent, tool, args, approval_id}
event: approval_required data: {turn, agent, tool, args, approval_id, requestId}
event: tool_executed     data: {turn, agent, tool, ok, approval_id, result, result_summary, error?}
event: final             data: {turn, session_id}
event: error             data: {message}
```

`router_decision` fires on `session_start` and again on every handoff with
a single-element `agents` list. Subscribers that connect *after*
`session_start` still receive it — events are buffered per-session.

## HTTP endpoints

| Verb | Path | Notes |
|---|---|---|
| `POST` | `/sessions` | Create session; bootstraps assignments. Returns 201 + `{sessionId, caller, assignments}`. |
| `POST` | `/sessions/{id}/messages` | Body **`{text}`** for a user turn, **or** `{approve: "<approval_id>"}` / `{reject: "<approval_id>", reason?}` for HITL responses. 202. |
| `GET`  | `/sessions/{id}/stream` | SSE stream. |
| `POST` | `/sessions/{id}/approvals/{requestId}` | Legacy: body `{decision, editedArgs?, reason?}`. |
| `GET`  | `/sessions/{id}/assignments` | Re-fetch assignments. |
| `GET`  | `/health` | `{status, model, agents:[...], mcpBackends, version: "0.3.1"}`. |

## Auth

Production: trust Container Apps Easy Auth — base64 JSON in
`X-MS-CLIENT-PRINCIPAL`. We extract `preferred_username` (or `upn`/`email`).

Dev: `DEV_BYPASS_AUTH=true` accepts `?as_user=email@x.com`. The inbound
`Authorization: Bearer ...` (or `X-User-Token` header) is forwarded to MCP.

## Run locally

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:DEV_BYPASS_AUTH = "true"
$env:FOUNDRY_PROJECT_ENDPOINT = "https://aif-mpwflow-dev-….services.ai.azure.com/api/projects/proj-mpwflow-dev"
$env:MODEL_DEPLOYMENT_NAME = "gpt-5_3-chat"
$env:SUBMISSIONS_MCP_URL = "https://ca-mpwflow-dev-mcp-submissions.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
$env:TAX_MCP_URL = "https://ca-mpwflow-dev-mcp-tax.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"
$env:LEGAL_MCP_URL = "https://ca-mpwflow-dev-mcp-legal.icyground-4e2c6fde.eastus2.azurecontainerapps.io/mcp"

pip install -e .[dev]
uvicorn chat_api.main:app --reload --port 8080
```

## Build & deploy

```powershell
docker build -t acrmpwflowdeva3qzr7isqw476.azurecr.io/chat-api:0.3.1 .
az acr login -n acrmpwflowdeva3qzr7isqw476
docker push acrmpwflowdeva3qzr7isqw476.azurecr.io/chat-api:0.3.1
az deployment group create -g rg-mpwflow-dev -f ../infra/main.bicep -p ../infra/main.parameters.json
```

> Container Apps reuses revisions when the image tag is unchanged — pass
> `--revision-suffix` on the resulting `az containerapp update` to force pull.

## Tests

```powershell
pip install -e .[dev]
pytest -q
```

The test suite covers:
1. Hosted MCP factory accepts `headers` + `approval_mode` (mock client).
2. `user_input_requests` → SSE `tool_proposed` + `approval_required` mapping.
3. `req.to_function_approval_response(True)` resumes correctly.
4. `req.to_function_approval_response(False)` rejects correctly.
5. Handoff between agents emits a new `router_decision`.
6. `X-User-Token` is forwarded on outbound MCP calls.

Plus the legacy session/auth/MCP-tool tests (21 total).

## Constraints honored

- No edits to `agents/` or `mcp-server/`.
- SSE event names + JSON shapes unchanged so chat-ui 0.1.1 keeps working.
- No secrets in source.

