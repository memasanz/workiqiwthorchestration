# Submissions agent

Foundry agent that takes a submitter's free-text questions, classifies each
as `tax` or `legal`, picks an assignee per category via round-robin
(`get_routing`), creates a project, and writes all questions in one
`submit_questions` call.

## Prereqs

- `az login` with access to subscription `7ee2b43a-eaea-4259-be7b-c8c220bfbcf9`
  and the `proj-mpwflow-dev` Foundry project.
- Python 3.10+.
- Install deps from `agents/requirements.txt`:

```powershell
cd C:\Users\memasanz\repos\multipersonworkflow
python -m venv .agentvenv
.\.agentvenv\Scripts\python.exe -m pip install -U pip -r agents\requirements.txt --pre
```

## Create / update the agent (idempotent)

```powershell
.\.agentvenv\Scripts\python.exe agents\submissions\create_agent.py
```

Looks up an agent named `submissions-agent` and updates it in place if
present, otherwise creates a new one. Prints the agent ID at the end.

## Smoke test

```powershell
.\.agentvenv\Scripts\python.exe agents\submissions\smoke_test.py
```

Sends a 3-question sample submission, polls the run, prints every MCP tool
call (name, arguments, truncated result) and the final assistant reply.
Exits non-zero if `create_project` or `submit_questions` was not called.
