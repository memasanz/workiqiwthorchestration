#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-command deploy of the multipersonworkflow stack.

.DESCRIPTION
    Idempotent end-to-end deploy. Assumes the M365 portal prereqs are
    already done (Foundry project + WorkIQUser/WorkIQMail connections).

    Steps:
      1. setup-entra (first pass — creates app regs if missing)
      2. azd up — preprovision hook auto-loads .env into azd env and
                  captures admin principalId; bicep grants Cosmos RBAC;
                  predeploy bakes MSAL into chat-ui; postprovision
                  registers Foundry agents; postdeploy fans MCP image
                  + seeds routing queues.
      3. setup-entra (second pass — adds FIC + prod redirect URI)
      4. grant-consent (tenant admin)
      5. open chat-ui in default browser

    Re-run any time. Each step is idempotent.

.EXAMPLE
    pwsh ./scripts/deploy.ps1
#>
[CmdletBinding()]
param(
    [string]$EnvName = $env:AZURE_ENV_NAME,
    [switch]$SkipBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Step($n, $msg) {
    Write-Host ""
    Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " STEP $n — $msg" -ForegroundColor Magenta
    Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Magenta
}

$repoRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $repoRoot

# Sanity: .env exists
if (-not (Test-Path .env)) {
    throw "Missing .env at repo root. Copy .env.sample to .env and fill it in (see README step 2)."
}

# Resolve env name (use AZURE_ENV_NAME from .env or current azd env)
if (-not $EnvName) {
    $envFromFile = (Get-Content .env | Select-String '^AZURE_ENV_NAME=' | Select-Object -First 1) -replace '^AZURE_ENV_NAME=', '' -replace '"', ''
    if ($envFromFile) { $EnvName = $envFromFile.Trim() }
}
if (-not $EnvName) {
    $base = (Get-Content .env | Select-String '^AZURE_BASE_NAME=' | Select-Object -First 1) -replace '^AZURE_BASE_NAME=', '' -replace '"', ''
    if ($base) { $EnvName = "$($base.Trim())-dev" } else { $EnvName = 'mpwflow-dev' }
}

# Ensure azd env exists + selected
$existing = (azd env list --output json | ConvertFrom-Json) | Where-Object { $_.Name -eq $EnvName }
if (-not $existing) {
    Write-Host "Creating azd env: $EnvName" -ForegroundColor Cyan
    azd env new $EnvName | Out-Null
} else {
    azd env select $EnvName | Out-Null
}

# Seed AZURE_SUBSCRIPTION_ID + AZURE_LOCATION from .env BEFORE azd up,
# because azd resolves those before running the preprovision hook.
function Get-EnvValue([string]$Key) {
    $line = Get-Content .env | Select-String "^\s*$Key\s*=" | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line.ToString() -replace "^\s*$Key\s*=", '' -replace '^"|"$', '' -replace "^'|'$", '').Trim()
}

$subFromEnv = Get-EnvValue 'AZURE_SUBSCRIPTION_ID'
$locFromEnv = Get-EnvValue 'AZURE_LOCATION'
if (-not $subFromEnv) {
    $subFromEnv = az account show --query id -o tsv 2>$null
    if ($subFromEnv) {
        Write-Host "AZURE_SUBSCRIPTION_ID not in .env — using current az context: $subFromEnv" -ForegroundColor DarkYellow
    } else {
        throw "AZURE_SUBSCRIPTION_ID not set in .env and no active 'az login'. Run 'az login' or add it to .env."
    }
}
if (-not $locFromEnv) { $locFromEnv = 'eastus2' ; Write-Host "AZURE_LOCATION not in .env — defaulting to $locFromEnv" -ForegroundColor DarkYellow }

azd env set AZURE_SUBSCRIPTION_ID $subFromEnv | Out-Null
azd env set AZURE_LOCATION        $locFromEnv | Out-Null

Step 1 "Entra app registrations (first pass)"
pwsh ./scripts/admin/setup-entra.ps1

# After first pass, .env contains the new ENTRA_* IDs — preprovision will load them.

Step 2 "azd up (provision + deploy + all hooks)"
azd up --no-prompt
if ($LASTEXITCODE -ne 0) { throw "azd up failed (exit $LASTEXITCODE)." }

Step 3 "Entra app registrations (second pass — FIC + redirect URI)"
$values = azd env get-values --output json | ConvertFrom-Json
pwsh ./scripts/admin/setup-entra.ps1 `
    -UamiPrincipalId $values.chatApiUamiPrincipalId `
    -ChatUiFqdn      $values.chatUiAppFqdn

Step 4 "Tenant admin consent"
pwsh ./scripts/admin/grant-consent.ps1

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host " ✓ Deploy complete. chat-ui: https://$($values.chatUiAppFqdn)"        -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Green

if (-not $SkipBrowser) {
    Start-Process "https://$($values.chatUiAppFqdn)"
}
