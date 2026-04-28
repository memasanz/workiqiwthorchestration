# Iteration 5 — create the two Entra app registrations + Federated Credential.
#
# What this script DOES (no admin consent required for any of it):
#   1. Creates `mpwflow-api` app reg (or reuses if it already exists)
#      - identifier URI: api://<backend-app-id>
#      - exposes one delegated scope: `Chat.ReadWrite`
#      - adds API permissions (Microsoft Graph User.Read,
#        Azure AI user_impersonation, WorkIQ user_impersonation)
#        WITHOUT granting them.
#      - adds a Federated Identity Credential whose subject is the
#        chat-api UAMI's principalId  ⇒ secretless OBO.
#   2. Creates `mpwflow-spa` app reg (or reuses if it already exists)
#      - SPA platform with redirect URIs for prod chat-ui FQDN + localhost
#      - delegated permission on `mpwflow-api / Chat.ReadWrite`
#        WITHOUT granting it.
#   3. Writes everything to scripts/iter5-app-reg-output.json so the
#      consent script and infra/code can read the IDs.
#
# What this script does NOT do:
#   - Grant admin consent. Run scripts/iter5-grant-consent.ps1 (admin)
#     for that.
#   - Create or modify any chat-api UAMI. Assumes the UAMI already
#     exists (it does — id-mpwflow-dev-chat-api).
#
# Idempotent: safe to re-run. Each az command is gated on a lookup.
#
# Prereqs:
#   - az login (your account)
#   - PowerShell 7+
#   - Permission to create app registrations in the tenant
#     (granted by default to all users unless tenant policy restricts it)

[CmdletBinding()]
param(
    [string]$BackendAppName = "mpwflow-api",
    [string]$SpaAppName     = "mpwflow-spa",
    [string]$ResourceGroup  = "rg-mpwflow-dev",
    [string]$UamiName       = "id-mpwflow-dev-chat-api",
    [string]$ChatUiFqdn     = "ca-mpwflow-dev-chat-ui.icyground-4e2c6fde.eastus2.azurecontainerapps.io",
    [string]$OutputPath     = "$PSScriptRoot/iter5-app-reg-output.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "  ↷ $msg" -ForegroundColor DarkYellow }

# Well-known first-party app IDs (stable across tenants)
$GRAPH_APP_ID            = "00000003-0000-0000-c000-000000000000"
$AZURE_AI_APP_ID         = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
$WORKIQ_APP_ID           = "ea9ffc3e-8a23-4a7d-836d-234d7c7565c1"
$FIC_EXCHANGE_AUDIENCE   = "api://AzureADTokenExchange"

# ── Tenant + UAMI lookup ─────────────────────────────────────────────────────
Write-Step "Looking up tenant + chat-api UAMI"
$tenantId = az account show --query tenantId -o tsv
if (-not $tenantId) { throw "Could not resolve tenant. Run 'az login' first." }
Write-Ok "tenant=$tenantId"

$uami = az identity show -g $ResourceGroup -n $UamiName 2>$null | ConvertFrom-Json
if (-not $uami) { throw "UAMI '$UamiName' not found in '$ResourceGroup'. Deploy infra first." }
$uamiPrincipalId = $uami.principalId
$uamiClientId    = $uami.clientId
Write-Ok "UAMI principalId=$uamiPrincipalId clientId=$uamiClientId"

# ── Resolve scope IDs from service principals ────────────────────────────────
Write-Step "Resolving delegated-scope IDs from first-party SPs"

function Get-ScopeId([string]$appId, [string]$scopeValue) {
    $sp = az ad sp show --id $appId 2>$null | ConvertFrom-Json
    if (-not $sp) { throw "No service principal found for app id $appId in this tenant." }
    $scope = $sp.oauth2PermissionScopes | Where-Object { $_.value -eq $scopeValue }
    if (-not $scope) { throw "Scope '$scopeValue' not found on app $appId. Available: $($sp.oauth2PermissionScopes.value -join ', ')" }
    return $scope.id
}

$graphUserReadId = Get-ScopeId $GRAPH_APP_ID "User.Read"
Write-Ok "Microsoft Graph User.Read = $graphUserReadId"

$azureAiUserImpersonationId = Get-ScopeId $AZURE_AI_APP_ID "user_impersonation"
Write-Ok "Azure AI user_impersonation = $azureAiUserImpersonationId"

# WorkIQ may not have an SP in your tenant yet — bootstrap one.
$workIqSp = az ad sp show --id $WORKIQ_APP_ID 2>$null | ConvertFrom-Json
if (-not $workIqSp) {
    Write-Skip "WorkIQ SP missing — creating one (no consent yet)"
    az ad sp create --id $WORKIQ_APP_ID -o none
    $workIqSp = az ad sp show --id $WORKIQ_APP_ID | ConvertFrom-Json
}
$workIqMatch = @($workIqSp.oauth2PermissionScopes | Where-Object { $_.value -eq "McpServers.Me.All" })
if ($workIqMatch.Count -gt 0) {
    $workIqUserImpersonationId = $workIqMatch[0].id
    $workIqScopeName = "McpServers.Me.All"
    Write-Ok "WorkIQ McpServers.Me.All = $workIqUserImpersonationId"
} else {
    # Fallback for tenants where the scope name has changed
    $candidate = @($workIqSp.oauth2PermissionScopes | Where-Object { $_.value -match "Me\." })
    if ($candidate.Count -eq 0) { $candidate = @($workIqSp.oauth2PermissionScopes) }
    $workIqUserImpersonationId = $candidate[0].id
    $workIqScopeName = $candidate[0].value
    Write-Skip "WorkIQ McpServers.Me.All not found — using '$workIqScopeName' ($workIqUserImpersonationId)"
}

# ── Backend app reg ──────────────────────────────────────────────────────────
Write-Step "Backend app registration: $BackendAppName"
$backend = az ad app list --display-name $BackendAppName --query "[?displayName=='$BackendAppName']|[0]" 2>$null | ConvertFrom-Json
if ($backend) {
    Write-Skip "exists — appId=$($backend.appId)"
} else {
    $backend = az ad app create --display-name $BackendAppName --sign-in-audience AzureADMyOrg | ConvertFrom-Json
    Write-Ok "created — appId=$($backend.appId)"
}
$backendAppId    = $backend.appId
$backendObjectId = $backend.id

# Set identifierUris = api://<backend-app-id>
$desiredIdUri = "api://$backendAppId"
if (-not ($backend.identifierUris -contains $desiredIdUri)) {
    az ad app update --id $backendAppId --identifier-uris $desiredIdUri
    Write-Ok "identifierUri set to $desiredIdUri"
} else {
    Write-Skip "identifierUri already set"
}

# Expose `Chat.ReadWrite` scope
$existingScope = $null
if ($backend.api -and $backend.api.oauth2PermissionScopes) {
    $existingScope = $backend.api.oauth2PermissionScopes | Where-Object { $_.value -eq "Chat.ReadWrite" }
}
if (-not $existingScope) {
    Write-Ok "Adding Chat.ReadWrite scope"
    $scopeId = (New-Guid).Guid
    $apiBlob = @{
        api = @{
            oauth2PermissionScopes = @(
                @{
                    id                      = $scopeId
                    adminConsentDescription = "Allow the app to read and write chat sessions on behalf of the signed-in user."
                    adminConsentDisplayName = "Read and write chat sessions"
                    userConsentDescription  = "Allow this app to read and write your chat sessions."
                    userConsentDisplayName  = "Read and write your chat sessions"
                    value                   = "Chat.ReadWrite"
                    type                    = "User"
                    isEnabled               = $true
                }
            )
            requestedAccessTokenVersion = 2
        }
    }
    $apiJson = ($apiBlob | ConvertTo-Json -Depth 10 -Compress)
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $apiJson -Encoding utf8
    az rest --method PATCH `
        --url "https://graph.microsoft.com/v1.0/applications/$backendObjectId" `
        --headers "Content-Type=application/json" `
        --body "@$tmp"
    Remove-Item $tmp
} else {
    Write-Skip "Chat.ReadWrite already exposed"
}

# Required API permissions on backend (Graph User.Read, Azure AI user_impersonation, WorkIQ)
Write-Ok "Setting requiredResourceAccess (Graph + Azure AI + WorkIQ)"
$rraBlob = @{
    requiredResourceAccess = @(
        @{
            resourceAppId = $GRAPH_APP_ID
            resourceAccess = @(
                @{ id = $graphUserReadId; type = "Scope" }
            )
        },
        @{
            resourceAppId = $AZURE_AI_APP_ID
            resourceAccess = @(
                @{ id = $azureAiUserImpersonationId; type = "Scope" }
            )
        },
        @{
            resourceAppId = $WORKIQ_APP_ID
            resourceAccess = @(
                @{ id = $workIqUserImpersonationId; type = "Scope" }
            )
        }
    )
}
$rraJson = ($rraBlob | ConvertTo-Json -Depth 10 -Compress)
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $rraJson -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$backendObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Backend FIC: chat-api UAMI as subject
Write-Step "Backend FIC for chat-api UAMI"
$ficName = "chat-api-uami-fic"
$existingFic = az ad app federated-credential list --id $backendAppId 2>$null | ConvertFrom-Json
$matchFic = $existingFic | Where-Object { $_.subject -eq $uamiPrincipalId }
if ($matchFic) {
    Write-Skip "FIC already exists for subject=$uamiPrincipalId"
} else {
    $ficBlob = @{
        name = $ficName
        issuer = "https://login.microsoftonline.com/$tenantId/v2.0"
        subject = $uamiPrincipalId
        audiences = @($FIC_EXCHANGE_AUDIENCE)
        description = "Secretless OBO: chat-api UAMI signs assertions for $BackendAppName"
    }
    $ficJson = ($ficBlob | ConvertTo-Json -Depth 5 -Compress)
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $ficJson -Encoding utf8
    az ad app federated-credential create --id $backendAppId --parameters "@$tmp"
    Remove-Item $tmp
    Write-Ok "FIC created for subject=$uamiPrincipalId"
}

# Service principal for backend (needed for admin consent later)
$backendSp = az ad sp show --id $backendAppId 2>$null | ConvertFrom-Json
if (-not $backendSp) { az ad sp create --id $backendAppId -o none; Write-Ok "Backend SP created" }
else { Write-Skip "Backend SP exists" }

# ── SPA app reg ──────────────────────────────────────────────────────────────
Write-Step "SPA app registration: $SpaAppName"
$spa = az ad app list --display-name $SpaAppName --query "[?displayName=='$SpaAppName']|[0]" 2>$null | ConvertFrom-Json
if ($spa) {
    Write-Skip "exists — appId=$($spa.appId)"
} else {
    $spa = az ad app create --display-name $SpaAppName --sign-in-audience AzureADMyOrg | ConvertFrom-Json
    Write-Ok "created — appId=$($spa.appId)"
}
$spaAppId    = $spa.appId
$spaObjectId = $spa.id

# SPA platform with redirect URIs
$desiredRedirects = @(
    "https://$ChatUiFqdn",
    "https://$ChatUiFqdn/",
    "http://localhost:5173",
    "http://localhost:5173/"
)
Write-Ok "Setting SPA redirect URIs"
$spaBlob = @{
    spa = @{ redirectUris = $desiredRedirects }
}
$spaJson = ($spaBlob | ConvertTo-Json -Depth 5 -Compress)
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $spaJson -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$spaObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Delegated permission on backend Chat.ReadWrite
Write-Ok "Setting SPA requiredResourceAccess to mpwflow-api/Chat.ReadWrite"
# Re-read backend to get the current scope id (might have just been added)
$backendNow = az ad app show --id $backendAppId | ConvertFrom-Json
$chatScopeMatch = @($backendNow.api.oauth2PermissionScopes | Where-Object { $_.value -eq "Chat.ReadWrite" })
if ($chatScopeMatch.Count -eq 0) {
    throw "Chat.ReadWrite scope was not exposed on backend (got $($backendNow.api.oauth2PermissionScopes.Count) scopes). Re-run."
}
$chatScopeId = $chatScopeMatch[0].id
$spaRraBlob = @{
    requiredResourceAccess = @(
        @{
            resourceAppId = $backendAppId
            resourceAccess = @(
                @{ id = $chatScopeId; type = "Scope" }
            )
        }
    )
}
$spaRraJson = ($spaRraBlob | ConvertTo-Json -Depth 10 -Compress)
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value $spaRraJson -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$spaObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Service principal for SPA (needed for admin consent later)
$spaSp = az ad sp show --id $spaAppId 2>$null | ConvertFrom-Json
if (-not $spaSp) { az ad sp create --id $spaAppId -o none; Write-Ok "SPA SP created" }
else { Write-Skip "SPA SP exists" }

# ── Output ───────────────────────────────────────────────────────────────────
Write-Step "Writing output to $OutputPath"
$out = [ordered]@{
    tenantId           = $tenantId
    chatUiFqdn         = $ChatUiFqdn
    uami = [ordered]@{
        name        = $UamiName
        principalId = $uamiPrincipalId
        clientId    = $uamiClientId
    }
    backend = [ordered]@{
        displayName = $BackendAppName
        appId       = $backendAppId
        objectId    = $backendObjectId
        identifierUri = $desiredIdUri
        exposedScope  = "Chat.ReadWrite"
        scopeId       = $chatScopeId
    }
    spa = [ordered]@{
        displayName = $SpaAppName
        appId       = $spaAppId
        objectId    = $spaObjectId
        redirectUris = $desiredRedirects
    }
    apiPermissions = [ordered]@{
        backendNeedsConsentFor = @(
            "Microsoft Graph / User.Read"
            "Azure AI / user_impersonation"
            "WorkIQ ($WORKIQ_APP_ID) / user_impersonation"
        )
        spaNeedsConsentFor = @(
            "$BackendAppName / Chat.ReadWrite"
        )
    }
}
$out | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Encoding utf8
Write-Ok "Output: $OutputPath"

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " Done. App regs created. Permissions set but NOT consented."        -ForegroundColor Cyan
Write-Host ""
Write-Host " Next: hand scripts/iter5-grant-consent.ps1 to a tenant admin."     -ForegroundColor Cyan
Write-Host ""
Write-Host " IDs for downstream wiring:"                                        -ForegroundColor Cyan
Write-Host "   tenantId          = $tenantId"
Write-Host "   backend (clientId)= $backendAppId"
Write-Host "   spa     (clientId)= $spaAppId"
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
