#!/usr/bin/env pwsh
# Runs as the azd `preprovision` hook. Two jobs:
#   1. Load .env values (created by the developer / setup-entra.ps1) into
#      the active azd environment so bicepparam's readEnvironmentVariable
#      calls find them.
#   2. Capture the deploying user's objectId into ADMIN_PRINCIPAL_ID so
#      bicep can grant them Cosmos data-plane RBAC.
#
# Idempotent — re-runs are safe.
$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path "$PSScriptRoot\..\.."
$envFile  = Join-Path $repoRoot '.env'

if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy .env.sample to .env, fill in the values from setup-entra.ps1, and re-run."
}

Write-Host "▶ Loading $envFile into azd environment" -ForegroundColor Cyan
$loaded = 0
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') {
        $key = $matches[1]
        $val = $matches[2].Trim().Trim('"')
        if ($val) {
            azd env set $key $val | Out-Null
            $loaded++
        }
    }
}
Write-Host "  ✓ $loaded values copied to azd env"

Write-Host "▶ Capturing admin principalId for Cosmos data-plane RBAC" -ForegroundColor Cyan
$me = az ad signed-in-user show --query id -o tsv 2>$null
if (-not $me) {
    Write-Warning "  Could not get signed-in user (run 'az login'). Skipping ADMIN_PRINCIPAL_ID."
    return
}
azd env set ADMIN_PRINCIPAL_ID $me | Out-Null
Write-Host "  ✓ ADMIN_PRINCIPAL_ID=$me"
