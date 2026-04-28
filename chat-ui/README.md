# chat-ui

React 18 + Vite + TypeScript SPA for the **Path A** chat-api Container App
(`ca-mpwflow-dev-chat-api`). Talks the SSE event contract documented in
`../chat-api/README.md`.

This UI targets **only** the original Path-A backend. A sibling AF-targeted
UI lives elsewhere (when it exists).

## Auth

Backend has `DEV_BYPASS_AUTH=true`. The UI asks for a caller email on first
load, persists it to `localStorage`, and appends `?as_user=<email>` to every
backend call. There is no real auth here; do not deploy as-is to production.

## Local dev

```powershell
cd chat-ui
npm install
npm run dev
# open http://localhost:5173
```

Vite dev server proxies `/api/*` to the deployed backend FQDN to avoid CORS.
Override the backend with the `BACKEND_BASE_URL` env var:

```powershell
$env:BACKEND_BASE_URL = "http://localhost:8080"
npm run dev
```

## Build (local)

```powershell
npm run build
npm run preview  # serves dist/ on :4173
```

## Container build & deploy

```powershell
cd C:\Users\memasanz\repos\multipersonworkflow
az acr build -r acrmpwflowdeva3qzr7isqw476 -t chat-ui:0.1.0 chat-ui/
az deployment group create -g rg-mpwflow-dev `
  -f infra/main.bicep -p infra/main.parameters.json `
  -p chatUiImage=acrmpwflowdeva3qzr7isqw476.azurecr.io/chat-ui:0.1.0
```

The Container App (`ca-mpwflow-dev-chat-ui`) runs nginx on port 8080 with a
`/api/*` reverse proxy to `BACKEND_BASE_URL`, defaulted to the deployed
chat-api FQDN. To point at a different backend, set the
`BACKEND_BASE_URL` env var on the Container App.

## Manual smoke checklist

(Playwright skipped to stay within the 60-min budget; backend smoke is
already covered by `chat-api/smoke_test.py` against the same SSE contract.)

1. Open the deployed UI URL in a browser.
2. Enter `e2e-ui@contoso.com` and click **Start session**.
3. Expect the right sidebar to render the caller email + assignments list.
4. In the composer, send: *"I have 2 questions: (1) Can our DE C-corp claim
   the R&D credit for AI compute? (2) Is our standard NDA enforceable in CA?"*
5. Expect agent bubbles to stream in (`submissions` first, possibly `tax_sme`
   and `legal_sme`).
6. Expect an **Approval required** modal when an agent proposes a tool call
   (e.g. `create_project`). Click **Approve**.
7. Verify the project lands in Cosmos via the MCP server (use the
   chat-api smoke test pattern).

## Files

| Path | Purpose |
|---|---|
| `src/api.ts` | Fetch helpers + SSE URL builder |
| `src/types.ts` | TypeScript types matching SSE event payloads |
| `src/state/session.ts` | React reducer + context for transcript / approvals |
| `src/components/` | ChatView, Composer, ApprovalModal, AssignmentsSidebar, AgentBadge, ToolCallExpando, LoginGate |
| `vite.config.ts` | Dev proxy (`/api → BACKEND_BASE_URL`) |
| `nginx.conf` | Prod reverse proxy (`/api → BACKEND_BASE_URL`) + SPA fallback |
| `Dockerfile` | Multi-stage node-build → nginx-serve on port 8080 |

## Pinned deps

- `react` / `react-dom` 18.3.1
- `react-markdown` 9.0.1
- `vite` 5.4.11, `@vitejs/plugin-react` 4.3.4
- `typescript` 5.6.3

Nothing else heavy: no MUI, no Tailwind, no router (single-view app).
