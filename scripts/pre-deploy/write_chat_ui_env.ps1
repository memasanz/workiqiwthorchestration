#!/usr/bin/env pwsh
# Writes chat-ui/.env.production from azd env values so Vite inlines the
# real MSAL client/tenant IDs at build time. This is more reliable than
# docker.buildArgs with remoteBuild=true, which has been observed to
# silently drop args.
$ErrorActionPreference = 'Stop'

$values  = azd env get-values --output json | ConvertFrom-Json
$tenant  = $values.ENTRA_TENANT_ID
$spa     = $values.ENTRA_SPA_APP_ID
$backend = $values.ENTRA_BACKEND_APP_ID

if (-not $tenant -or -not $spa -or -not $backend) {
    throw "Missing ENTRA_* values in azd env (tenant=$tenant spa=$spa backend=$backend). Run setup-entra.ps1 + azd env set first."
}

$envFile = Join-Path $PSScriptRoot '..\..\chat-ui\.env.production'
@(
    "VITE_TENANT_ID=$tenant"
    "VITE_SPA_CLIENT_ID=$spa"
    "VITE_API_CLIENT_ID=$backend"
) | Set-Content -Path $envFile -Encoding utf8

Write-Host "✓ Wrote $envFile" -ForegroundColor Green
Write-Host "    VITE_TENANT_ID=$tenant"
Write-Host "    VITE_SPA_CLIENT_ID=$spa"
Write-Host "    VITE_API_CLIENT_ID=$backend"
