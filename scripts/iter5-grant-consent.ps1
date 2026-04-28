# Iteration 5 — admin consent script.
#
# Run this AS A TENANT ADMIN after scripts/iter5-create-app-regs.ps1
# has produced scripts/iter5-app-reg-output.json.
#
# What this script does:
#   1. Grants admin consent for the backend app reg's API permissions:
#      - Microsoft Graph / User.Read
#      - Azure AI / user_impersonation
#      - WorkIQ / user_impersonation
#   2. Grants admin consent for the SPA app reg's permission:
#      - mpwflow-api / Chat.ReadWrite
#
# Idempotent: re-running does not create duplicate grants.
#
# Prereqs:
#   - az login as a Global Admin / Privileged Role Admin / Cloud App Admin
#     (any role with `Application.ReadWrite.All` + admin-consent rights)
#   - PowerShell 7+

[CmdletBinding()]
param(
    [string]$InputPath = "$PSScriptRoot/iter5-app-reg-output.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "  ↷ $msg" -ForegroundColor DarkYellow }

if (-not (Test-Path $InputPath)) {
    throw "Missing $InputPath. Run scripts/iter5-create-app-regs.ps1 first."
}
$cfg = Get-Content $InputPath -Raw | ConvertFrom-Json

$tenantId       = $cfg.tenantId
$backendAppId   = $cfg.backend.appId
$spaAppId       = $cfg.spa.appId

Write-Host "Tenant : $tenantId"
Write-Host "Backend: $($cfg.backend.displayName) ($backendAppId)"
Write-Host "SPA    : $($cfg.spa.displayName) ($spaAppId)"

# ── Helper: add an oAuth2PermissionGrant (delegated admin consent) ───────────
function Grant-DelegatedAdminConsent {
    param(
        [Parameter(Mandatory)] [string] $ClientAppId,    # the app being consented FOR
        [Parameter(Mandatory)] [string] $ResourceAppId,  # the API being consented TO
        [Parameter(Mandatory)] [string] $Scope           # space-separated scope names
    )

    $clientSp   = az ad sp show --id $ClientAppId   2>$null | ConvertFrom-Json
    $resourceSp = az ad sp show --id $ResourceAppId 2>$null | ConvertFrom-Json
    if (-not $clientSp)   { throw "No SP for client app $ClientAppId. Run create script first." }
    if (-not $resourceSp) { throw "No SP for resource $ResourceAppId. Bootstrap with 'az ad sp create --id $ResourceAppId'." }

    $existing = az rest --method GET --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($clientSp.id)' and resourceId eq '$($resourceSp.id)' and consentType eq 'AllPrincipals'" 2>$null | ConvertFrom-Json
    if ($existing.value -and $existing.value.Count -gt 0) {
        $existingScopes = $existing.value[0].scope
        $needed = $Scope.Split(' ') | Where-Object { $existingScopes -notmatch "(^|\s)$_(\s|$)" }
        if ($needed.Count -eq 0) {
            Write-Skip "$($resourceSp.displayName) / $Scope — already consented"
            return
        }
        # Update existing grant by appending missing scopes
        $merged = ($existingScopes.Split(' ') + $needed | Sort-Object -Unique) -join ' '
        $body = @{ scope = $merged } | ConvertTo-Json -Compress
        $tmp = New-TemporaryFile
        Set-Content $tmp $body -Encoding utf8
        az rest --method PATCH `
            --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/$($existing.value[0].id)" `
            --headers "Content-Type=application/json" `
            --body "@$tmp"
        Remove-Item $tmp
        Write-Ok "$($resourceSp.displayName) / $Scope — appended (now: $merged)"
        return
    }

    $body = @{
        clientId    = $clientSp.id
        consentType = "AllPrincipals"
        principalId = $null
        resourceId  = $resourceSp.id
        scope       = $Scope
    } | ConvertTo-Json -Compress

    $tmp = New-TemporaryFile
    Set-Content $tmp $body -Encoding utf8
    az rest --method POST `
        --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" `
        --headers "Content-Type=application/json" `
        --body "@$tmp" | Out-Null
    Remove-Item $tmp
    Write-Ok "$($resourceSp.displayName) / $Scope — granted"
}

# ── Backend consents ─────────────────────────────────────────────────────────
$GRAPH_APP_ID    = "00000003-0000-0000-c000-000000000000"
$AZURE_AI_APP_ID = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
$WORKIQ_APP_ID   = "ea9ffc3e-8a23-4a7d-836d-234d7c7565c1"

Write-Step "Granting admin consent on $($cfg.backend.displayName)"

# Need to discover the actual scope name on the WorkIQ SP since 'user_impersonation'
# is not WorkIQ's scope name; we use McpServers.Me.All for the Me MCP Server.
$workIqSp = az ad sp show --id $WORKIQ_APP_ID | ConvertFrom-Json
$preferred = @($workIqSp.oauth2PermissionScopes | Where-Object { $_.value -eq "McpServers.Me.All" })
if ($preferred.Count -gt 0) {
    $workIqScopeName = "McpServers.Me.All"
} else {
    $fallback = @($workIqSp.oauth2PermissionScopes | Where-Object { $_.value -match "Me\." })
    if ($fallback.Count -eq 0) { $fallback = @($workIqSp.oauth2PermissionScopes) }
    $workIqScopeName = $fallback[0].value
}
Write-Host "  WorkIQ scope name: $workIqScopeName"

Grant-DelegatedAdminConsent -ClientAppId $backendAppId -ResourceAppId $GRAPH_APP_ID    -Scope "User.Read"
Grant-DelegatedAdminConsent -ClientAppId $backendAppId -ResourceAppId $AZURE_AI_APP_ID -Scope "user_impersonation"
Grant-DelegatedAdminConsent -ClientAppId $backendAppId -ResourceAppId $WORKIQ_APP_ID   -Scope $workIqScopeName

# ── SPA consents ─────────────────────────────────────────────────────────────
Write-Step "Granting admin consent on $($cfg.spa.displayName)"
Grant-DelegatedAdminConsent -ClientAppId $spaAppId -ResourceAppId $backendAppId -Scope "Chat.ReadWrite"

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " ✓ Admin consent complete. Users can now sign in without prompts."   -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
