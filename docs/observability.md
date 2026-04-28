# Observability — Multi-Person Workflow

This document defines the telemetry contract for the MCP server, ready-to-run KQL
queries, recommended alerts, and a workbook skeleton for the
`appi-mpwflow-dev` Application Insights instance (Log Analytics workspace
`log-mpwflow-dev`, resource group `rg-mpwflow-dev`).

The MCP server runs in Container App `ca-mpwflow-dev-mcp` and is wired to push
telemetry via `APPLICATIONINSIGHTS_CONNECTION_STRING`. The three Foundry agents
call this MCP server over HTTP/SSE; their tool calls show up here as a single
`traces` row per invocation (see contract below).

---

## 1. Telemetry contract for the MCP server

Every MCP tool invocation **must emit exactly one structured log record** with
the fields below in `customDimensions`. The record should be written *after*
the tool finishes (or fails) so `outcome` and `latency_ms` are known.

### Required fields (in `customDimensions`)

| Field              | Type    | When                                 | Notes |
| ------------------ | ------- | ------------------------------------ | ----- |
| `tool`             | string  | always                               | Tool name, e.g. `submit_answer`, `update_question_status`. |
| `outcome`          | string  | always                               | One of: `success`, `etag_conflict_retry`, `validation_error`, `not_found`, `error`. |
| `latency_ms`       | int     | always                               | Wall-clock duration of the tool handler in milliseconds. |
| `by`               | string  | when an acting user id is present    | The user id from the agent's session (project owner, reviewer, etc.). |
| `projectId`        | string  | when the tool operates on a project  | Cosmos partition key for the `projects` container. |
| `questionId`       | string  | when the tool operates on a question | Stable id of the question row. |
| `clientRequestId`  | string  | when supplied for idempotency        | The caller-provided idempotency key. |
| `traceparent`      | string  | always (auto)                        | W3C trace context, propagated by SDK auto-instrumentation; do **not** set manually. |

The message text of the log line should be a short human-readable summary
(e.g. `"submit_answer ok"`); the structured fields above are the source of
truth for queries and alerts.

### Why one row per tool call (and not many)

- **One unit of work = one row.** A tool call is the smallest meaningful business operation
  (it either succeeds, fails, or retries). Emitting one row per call keeps the
  cardinality low, makes `count()` a real throughput metric, and makes
  `percentile(latency_ms, 95)` correct without weighting tricks.
- **Fan-out is already captured.** Auto-instrumentation emits child
  `dependencies` rows for the Cosmos and Azure SDK calls inside the tool, all
  correlated via `operation_Id` / `traceparent`. So we keep detail without
  paying for it on the hot path.
- **Cheap alerting.** Alerts and dashboards become trivial filters on
  `tool`, `outcome`, and `latency_ms` rather than reductions over many
  fragments of the same call.
- **Audit trail is linear.** Reconstructing "what happened to project X" is a
  simple chronological scan of one row per action — no de-duplication required.

---

## 2. KQL queries

All queries below run in the App Insights resource (`appi-mpwflow-dev`) — the
`traces`, `requests`, `dependencies`, and `exceptions` tables are
workspace-based and also visible in `log-mpwflow-dev`.

### 2.1 Per-tool throughput + latency p50/p95 (last 24h)

```kql
// Throughput and latency percentiles per MCP tool over the last 24h.
let lookback = 24h;
traces
| where timestamp > ago(lookback)
| where isnotempty(tostring(customDimensions.tool))
| extend tool       = tostring(customDimensions.tool),
         outcome    = tostring(customDimensions.outcome),
         latency_ms = toint(customDimensions.latency_ms)
| summarize
    calls       = count(),
    successes   = countif(outcome == "success"),
    p50_ms      = percentile(latency_ms, 50),
    p95_ms      = percentile(latency_ms, 95),
    p99_ms      = percentile(latency_ms, 99)
    by tool
| extend success_rate = round(100.0 * successes / calls, 2)
| order by calls desc
```

### 2.2 Error rate by tool

```kql
// Error rate per tool. "error" buckets all non-success outcomes except idempotent retries.
let lookback = 24h;
traces
| where timestamp > ago(lookback)
| where isnotempty(tostring(customDimensions.tool))
| extend tool    = tostring(customDimensions.tool),
         outcome = tostring(customDimensions.outcome)
| summarize
    calls            = count(),
    errors           = countif(outcome in ("error", "validation_error", "not_found")),
    etag_retries     = countif(outcome == "etag_conflict_retry")
    by tool, bin(timestamp, 5m)
| extend error_rate_pct = round(100.0 * errors / calls, 2)
| order by timestamp desc, error_rate_pct desc
```

### 2.3 Audit trail for one project (chronological)

```kql
// Full audit trail for a single project: who did what, when, with what result.
let projectId_param = "REPLACE-ME";
traces
| where isnotempty(tostring(customDimensions.tool))
| where tostring(customDimensions.projectId) == projectId_param
| extend tool       = tostring(customDimensions.tool),
         by_user    = tostring(customDimensions.by),
         outcome    = tostring(customDimensions.outcome),
         questionId = tostring(customDimensions.questionId),
         note       = message
| project timestamp, by_user, tool, questionId, outcome, note, operation_Id
| order by timestamp asc
```

### 2.4 Per-user activity

```kql
// Activity by acting user: tool calls and distinct projects touched in the last 7d.
let lookback = 7d;
traces
| where timestamp > ago(lookback)
| where isnotempty(tostring(customDimensions.by))
| extend by_user   = tostring(customDimensions.by),
         tool      = tostring(customDimensions.tool),
         projectId = tostring(customDimensions.projectId),
         outcome   = tostring(customDimensions.outcome)
| summarize
    calls            = count(),
    successes        = countif(outcome == "success"),
    distinct_projects = dcount(projectId),
    distinct_tools    = dcount(tool),
    last_seen         = max(timestamp)
    by by_user
| order by calls desc
```

### 2.5 etag conflict retries (hot keys)

```kql
// Rows where the optimistic-concurrency retry path fired. Repeated hits on the
// same (projectId, questionId) indicate a hot key worth investigating.
let lookback = 24h;
traces
| where timestamp > ago(lookback)
| where tostring(customDimensions.outcome) == "etag_conflict_retry"
| extend tool       = tostring(customDimensions.tool),
         projectId  = tostring(customDimensions.projectId),
         questionId = tostring(customDimensions.questionId),
         by_user    = tostring(customDimensions.by)
| summarize retries = count(), users = make_set(by_user, 10)
    by tool, projectId, questionId
| order by retries desc
```

### 2.6 Idempotent retry hits (clientRequestId reused)

```kql
// Same clientRequestId seen more than once == client retried an idempotent op.
let lookback = 24h;
traces
| where timestamp > ago(lookback)
| where isnotempty(tostring(customDimensions.clientRequestId))
| extend clientRequestId = tostring(customDimensions.clientRequestId),
         tool            = tostring(customDimensions.tool),
         outcome         = tostring(customDimensions.outcome)
| summarize
    hits        = count(),
    outcomes    = make_set(outcome, 10),
    first_seen  = min(timestamp),
    last_seen   = max(timestamp)
    by clientRequestId, tool
| where hits > 1
| order by hits desc
```

### 2.7 Cold-start latency for the Container App

```kql
// Replica spin-ups for ca-mpwflow-dev-mcp. Looks for "Reserving" / "Started" /
// "Listening on" markers emitted during a cold start.
let lookback = 24h;
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(lookback)
| where ContainerAppName_s == "ca-mpwflow-dev-mcp"
| where Reason_s in ("ReplicaScheduled", "AssigningIP", "ContainerCreated", "ContainerStarted")
   or Log_s has_any ("Reserving", "Started", "Listening on", "Now listening")
| project TimeGenerated, RevisionName_s, ReplicaName_s, Reason_s, Log_s
| order by TimeGenerated desc
```

If only `containerappconsoleLogs_CL` is enabled, use this variant:

```kql
// Cold-start markers from the application console stream.
let lookback = 24h;
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(lookback)
| where ContainerAppName_s == "ca-mpwflow-dev-mcp"
| where Log_s has_any ("Listening on", "Now listening", "Application started", "Started server")
| project TimeGenerated, RevisionName_s, ReplicaName_s, Log_s
| order by TimeGenerated desc
```

### 2.8 Question state transitions

```kql
// Sequence of update_question_status calls per question, in order.
let lookback = 7d;
traces
| where timestamp > ago(lookback)
| where tostring(customDimensions.tool) == "update_question_status"
| extend questionId = tostring(customDimensions.questionId),
         projectId  = tostring(customDimensions.projectId),
         by_user    = tostring(customDimensions.by),
         outcome    = tostring(customDimensions.outcome),
         note       = message
| project timestamp, projectId, questionId, by_user, outcome, note
| order by projectId asc, questionId asc, timestamp asc
```

---

## 3. Suggested alerts

All alerts target the `appi-mpwflow-dev` resource (or `log-mpwflow-dev` for
Container App logs) using log-search alert rules unless noted.

| Name | Signal / KQL | Threshold | Severity | Why |
| ---- | ------------ | --------- | -------- | --- |
| `mcp-tool-error-rate-high` | `traces` filtered to `customDimensions.tool` not empty, grouped per tool: `errors / calls` over 5 min (see query 2.2) | > **5%** for 2 consecutive evaluations | Sev 2 | Catches a regression in a single tool without being drowned by total volume. |
| `mcp-tool-p95-latency-high` | Per-tool `percentile(latency_ms, 95)` over 5 min (variant of 2.1) | > **1500 ms** | Sev 3 | Detects Cosmos slowdowns or runaway tool handlers before users notice. |
| `mcp-silent-failure-business-hours` | `count()` of `traces` where `customDimensions.outcome == "success"` over 30 min, scheduled M–F 08:00–18:00 local | `== 0` | Sev 2 | If no tool succeeds during business hours, the agents or MCP are wedged. |
| `mcp-replica-restart-loop` | `ContainerAppSystemLogs_CL` where `Reason_s == "ContainerStarted"` for `ca-mpwflow-dev-mcp` | > **5** restarts in 10 min | Sev 2 | Crash loop / OOM / bad revision. |
| `cosmos-throttling-429` | `dependencies` where `type == "Azure DocumentDB"` and `resultCode == "429"` | > **10** in 5 min | Sev 2 | Indicates RU/s pressure or a hot partition; pairs with query 2.5. |

KQL skeleton for the silent-failure alert:

```kql
traces
| where timestamp > ago(30m)
| where tostring(customDimensions.outcome) == "success"
| count
```

KQL skeleton for the Cosmos 429 alert:

```kql
dependencies
| where timestamp > ago(5m)
| where type == "Azure DocumentDB"
| where toint(resultCode) == 429
| summarize throttles = count() by bin(timestamp, 1m), target
```

---

## 4. Pinning to an Azure Workbook

To turn these queries into a dashboard:

1. In the Azure portal, open `appi-mpwflow-dev` → **Workbooks** → **+ New**.
2. Click **Advanced Editor** (`</>` icon) and paste the JSON skeleton below.
3. Click **Apply**, then **Save** to your resource group.
4. To add more queries, click **+ Add → Add query**, paste any KQL block from
   section 2, and choose a visualization (Table for audit trails, Time chart
   for throughput / latency).
5. Pin individual tiles to an Azure Dashboard via the pin icon on each part.

### Workbook JSON skeleton (two queries pre-loaded)

```json
{
  "version": "Notebook/1.0",
  "items": [
    {
      "type": 1,
      "content": {
        "json": "# MCP — Multi-Person Workflow\nOperational view for `ca-mpwflow-dev-mcp`. Queries hit App Insights `appi-mpwflow-dev`."
      },
      "name": "title"
    },
    {
      "type": 9,
      "content": {
        "version": "KqlParameterItem/1.0",
        "parameters": [
          {
            "id": "p-lookback",
            "version": "KqlParameterItem/1.0",
            "name": "lookback",
            "type": 4,
            "value": { "durationMs": 86400000 },
            "typeSettings": {
              "selectableValues": [
                { "durationMs": 3600000 },
                { "durationMs": 21600000 },
                { "durationMs": 86400000 },
                { "durationMs": 604800000 }
              ]
            }
          }
        ]
      },
      "name": "params"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "traces\n| where timestamp {lookback}\n| where isnotempty(tostring(customDimensions.tool))\n| extend tool=tostring(customDimensions.tool), outcome=tostring(customDimensions.outcome), latency_ms=toint(customDimensions.latency_ms)\n| summarize calls=count(), successes=countif(outcome=='success'), p50_ms=percentile(latency_ms,50), p95_ms=percentile(latency_ms,95) by tool\n| extend success_rate=round(100.0*successes/calls,2)\n| order by calls desc",
        "size": 0,
        "title": "Per-tool throughput + latency",
        "queryType": 0,
        "resourceType": "microsoft.insights/components",
        "visualization": "table"
      },
      "name": "throughput-latency"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "traces\n| where timestamp {lookback}\n| where isnotempty(tostring(customDimensions.tool))\n| extend tool=tostring(customDimensions.tool), outcome=tostring(customDimensions.outcome)\n| summarize calls=count(), errors=countif(outcome in ('error','validation_error','not_found')) by tool, bin(timestamp, 5m)\n| extend error_rate_pct=round(100.0*errors/calls,2)\n| project timestamp, tool, error_rate_pct\n| render timechart",
        "size": 0,
        "title": "Error rate by tool (5-min buckets)",
        "queryType": 0,
        "resourceType": "microsoft.insights/components",
        "visualization": "timechart"
      },
      "name": "error-rate"
    }
  ],
  "fallbackResourceIds": [],
  "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"
}
```

> Note: the `{lookback}` token is a Workbook parameter binding — the editor
> will substitute it with the picker value at run time.
