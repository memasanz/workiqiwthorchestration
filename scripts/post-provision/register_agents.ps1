# azd postprovision hook — register / update the 3 Foundry SME PromptAgents.
#
# Reads azd env values populated by `infra/main.bicep` outputs and invokes
# each `agents/<name>/create_agent.py`. Idempotent — each script does
# `agents.create_version` on a fixed agent_name.
#
# Required env vars (read from `azd env get-values` — bicep outputs are
# preserved in their original camelCase, not upper-snake):
#   foundryProjectEndpoint
#   modelDeploymentName
#   mcpSubmissionsAppFqdn
#   mcpTaxAppFqdn
#   mcpLegalAppFqdn

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Require-Env($name) {
    $v = [Environment]::GetEnvironmentVariable($name)
    if (-not $v) { throw "Missing env var '$name' from azd env." }
    return $v
}

# Pull values straight from `azd env get-values` so we don't depend on
# the host's case-sensitivity quirks (azd preserves bicep camelCase).
Write-Step "Reading azd env"
$rawEnv = & azd env get-values 2>$null
$envMap = @{}
foreach ($line in $rawEnv) {
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$') {
        $envMap[$matches[1]] = $matches[2].Trim('"').Trim("'")
    }
}
function Get-AzdValue($name) {
    if (-not $envMap.ContainsKey($name) -or -not $envMap[$name]) {
        throw "azd env value '$name' is missing or empty. Did 'azd provision' finish?"
    }
    return $envMap[$name]
}

$endpoint        = Get-AzdValue "foundryProjectEndpoint"
$model           = Get-AzdValue "modelDeploymentName"
$submissionsFqdn = Get-AzdValue "mcpSubmissionsAppFqdn"
$taxFqdn         = Get-AzdValue "mcpTaxAppFqdn"
$legalFqdn       = Get-AzdValue "mcpLegalAppFqdn"
Write-Ok "endpoint: $endpoint"
Write-Ok "model:    $model"

# Pick a Python interpreter: prefer the agents venv, fall back to system.
$pythonCandidates = @(
    Join-Path $repoRoot ".agentvenv\Scripts\python.exe"
    Join-Path $repoRoot ".venv\Scripts\python.exe"
    "python"
)
$python = $pythonCandidates | Where-Object {
    if ($_ -eq "python") { $true } else { Test-Path $_ }
} | Select-Object -First 1
Write-Ok "python:   $python"

$agents = @(
    @{ Name = "submissions"; Script = Join-Path $repoRoot "agents\submissions\create_agent.py"; UrlVar = "SUBMISSIONS_MCP_URL"; Fqdn = $submissionsFqdn }
    @{ Name = "tax";         Script = Join-Path $repoRoot "agents\tax\create_agent.py";         UrlVar = "TAX_MCP_URL";         Fqdn = $taxFqdn         }
    @{ Name = "legal";       Script = Join-Path $repoRoot "agents\legal\create_agent.py";       UrlVar = "LEGAL_MCP_URL";       Fqdn = $legalFqdn       }
)

foreach ($a in $agents) {
    Write-Step "Registering $($a.Name) agent"
    $env:FOUNDRY_PROJECT_ENDPOINT = $endpoint
    $env:MODEL_DEPLOYMENT_NAME    = $model
    [Environment]::SetEnvironmentVariable($a.UrlVar, "https://$($a.Fqdn)/mcp", "Process")
    & $python $a.Script
    if ($LASTEXITCODE -ne 0) {
        throw "$($a.Name) registration failed with exit $LASTEXITCODE"
    }
    Write-Ok "$($a.Name) registered"
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " ✓ All 3 SME PromptAgents registered / updated."                       -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
