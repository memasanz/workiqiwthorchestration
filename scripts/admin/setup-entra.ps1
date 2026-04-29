# Admin script — create / update the two Entra app registrations and
# (optionally) the federated identity credential for the chat-api UAMI.
#
# Workflow:
#   First run (before `azd up`):
#     ./scripts/admin/setup-entra.ps1
#     -> Creates `mpwflow-api` + `mpwflow-spa` app regs and prints
#        the appIds you need to set in .env (ENTRA_BACKEND_APP_ID,
#        ENTRA_SPA_APP_ID).
#
#   After `azd up` (chat-api UAMI now exists):
#     ./scripts/admin/setup-entra.ps1 `
#       -UamiPrincipalId <id> `
#       -ChatUiFqdn <fqdn> `
#       -EnvSuffix dev
#     -> Adds a federated identity credential so chat-api can mint OBO
#        tokens for `mpwflow-api` without a client secret. Updates the
#        SPA redirect URIs to include the deployed chat-ui FQDN.
#
# Idempotent. Re-run per region (each region has its own UAMI -> FIC).
#
# Prereqs:
#   - az login (your account)
#   - PowerShell 7+
#   - Permission to create / modify app registrations in the tenant.
#     (Most tenants allow this for any user; some restrict to admins.)

[CmdletBinding()]
param(
    [string]$Prefix         = "",
    [string]$BackendAppName = "",
    [string]$SpaAppName     = "",
    [string]$ChatUiFqdn     = "",
    [string]$UamiPrincipalId = "",
    [string]$EnvSuffix       = "",
    [string]$EnvFile         = "$PSScriptRoot/../../.env",
    [string]$OutputPath      = "$PSScriptRoot/.app-reg-output.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Read .env (if present) so -Prefix / -EnvSuffix can default from it.
$envValues = @{}
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$' -and $_ -notmatch '^\s*#') {
            $envValues[$matches[1]] = $matches[2].Trim('"').Trim("'")
        }
    }
    Write-Host "Loaded $($envValues.Count) values from $EnvFile" -ForegroundColor DarkGray
}

if (-not $Prefix)         { $Prefix         = if ($envValues.ContainsKey('AZURE_BASE_NAME') -and $envValues['AZURE_BASE_NAME']) { $envValues['AZURE_BASE_NAME'] } else { "mpwflow" } }
if (-not $EnvSuffix)      { $EnvSuffix      = if ($envValues.ContainsKey('AZURE_ENV_NAME')  -and $envValues['AZURE_ENV_NAME'])  { $envValues['AZURE_ENV_NAME']  } else { "dev" } }
if (-not $BackendAppName) { $BackendAppName = "$Prefix-api" }
if (-not $SpaAppName)     { $SpaAppName     = "$Prefix-spa" }

function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "  ↷ $msg" -ForegroundColor DarkYellow }

# Well-known first-party app IDs (stable across tenants)
$GRAPH_APP_ID            = "00000003-0000-0000-c000-000000000000"
$AZURE_AI_APP_ID         = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
$WORKIQ_APP_ID           = "ea9ffc3e-8a23-4a7d-836d-234d7c7565c1"
$FIC_EXCHANGE_AUDIENCE   = "api://AzureADTokenExchange"

# ── Tenant lookup ───────────────────────────────────────────────────────────
Write-Step "Looking up tenant"
$tenantId = az account show --query tenantId -o tsv
if (-not $tenantId) { throw "Could not resolve tenant. Run 'az login' first." }
Write-Ok "tenant=$tenantId"

# ── Resolve delegated-scope IDs from first-party SPs ────────────────────────
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
} else {
    $candidate = @($workIqSp.oauth2PermissionScopes | Where-Object { $_.value -match "Me\." })
    if ($candidate.Count -eq 0) { $candidate = @($workIqSp.oauth2PermissionScopes) }
    $workIqUserImpersonationId = $candidate[0].id
    $workIqScopeName = $candidate[0].value
    Write-Skip "Falling back to scope '$workIqScopeName'"
}
Write-Ok "WorkIQ $workIqScopeName = $workIqUserImpersonationId"

# ── Backend app reg ─────────────────────────────────────────────────────────
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
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value (($apiBlob | ConvertTo-Json -Depth 10 -Compress)) -Encoding utf8
    az rest --method PATCH `
        --url "https://graph.microsoft.com/v1.0/applications/$backendObjectId" `
        --headers "Content-Type=application/json" `
        --body "@$tmp"
    Remove-Item $tmp
} else {
    Write-Skip "Chat.ReadWrite already exposed"
}

# Required API permissions on backend
Write-Ok "Setting requiredResourceAccess (Graph + Azure AI + WorkIQ)"
$rraBlob = @{
    requiredResourceAccess = @(
        @{ resourceAppId = $GRAPH_APP_ID;    resourceAccess = @( @{ id = $graphUserReadId; type = "Scope" } ) },
        @{ resourceAppId = $AZURE_AI_APP_ID; resourceAccess = @( @{ id = $azureAiUserImpersonationId; type = "Scope" } ) },
        @{ resourceAppId = $WORKIQ_APP_ID;   resourceAccess = @( @{ id = $workIqUserImpersonationId; type = "Scope" } ) }
    )
}
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value (($rraBlob | ConvertTo-Json -Depth 10 -Compress)) -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$backendObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Service principal for backend
$backendSp = az ad sp show --id $backendAppId 2>$null | ConvertFrom-Json
if (-not $backendSp) { az ad sp create --id $backendAppId -o none; Write-Ok "Backend SP created" }
else { Write-Skip "Backend SP exists" }

# ── Backend FIC (only when -UamiPrincipalId is supplied) ────────────────────
if ($UamiPrincipalId) {
    Write-Step "Federated identity credential for chat-api UAMI ($EnvSuffix)"
    $ficName = "chat-api-$EnvSuffix-uami-fic"
    $existingFic = az ad app federated-credential list --id $backendAppId 2>$null | ConvertFrom-Json
    $matchFic = $existingFic | Where-Object { $_.subject -eq $UamiPrincipalId }
    if ($matchFic) {
        Write-Skip "FIC already exists for subject=$UamiPrincipalId (name=$($matchFic.name))"
    } else {
        $ficBlob = @{
            name        = $ficName
            issuer      = "https://login.microsoftonline.com/$tenantId/v2.0"
            subject     = $UamiPrincipalId
            audiences   = @($FIC_EXCHANGE_AUDIENCE)
            description = "Secretless OBO: chat-api UAMI ($EnvSuffix) signs assertions for $BackendAppName"
        }
        $tmp = New-TemporaryFile
        Set-Content -Path $tmp -Value (($ficBlob | ConvertTo-Json -Depth 5 -Compress)) -Encoding utf8
        az ad app federated-credential create --id $backendAppId --parameters "@$tmp"
        Remove-Item $tmp
        Write-Ok "FIC '$ficName' created for subject=$UamiPrincipalId"
    }
} else {
    Write-Skip "No -UamiPrincipalId provided. Skipping FIC. Re-run after `azd up`."
}

# ── SPA app reg ─────────────────────────────────────────────────────────────
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
$desiredRedirects = @("http://localhost:5173","http://localhost:5173/")
if ($ChatUiFqdn) {
    $desiredRedirects += "https://$ChatUiFqdn"
    $desiredRedirects += "https://$ChatUiFqdn/"
}
Write-Ok "Setting SPA redirect URIs: $($desiredRedirects -join ', ')"
$spaBlob = @{ spa = @{ redirectUris = $desiredRedirects } }
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value (($spaBlob | ConvertTo-Json -Depth 5 -Compress)) -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$spaObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Delegated permission on backend Chat.ReadWrite
Write-Ok "Setting SPA requiredResourceAccess to $BackendAppName/Chat.ReadWrite"
$backendNow = az ad app show --id $backendAppId | ConvertFrom-Json
$chatScopeMatch = @($backendNow.api.oauth2PermissionScopes | Where-Object { $_.value -eq "Chat.ReadWrite" })
if ($chatScopeMatch.Count -eq 0) {
    throw "Chat.ReadWrite scope was not exposed on backend. Re-run."
}
$chatScopeId = $chatScopeMatch[0].id
$spaRraBlob = @{
    requiredResourceAccess = @(
        @{ resourceAppId = $backendAppId; resourceAccess = @( @{ id = $chatScopeId; type = "Scope" } ) }
    )
}
$tmp = New-TemporaryFile
Set-Content -Path $tmp -Value (($spaRraBlob | ConvertTo-Json -Depth 10 -Compress)) -Encoding utf8
az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$spaObjectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp"
Remove-Item $tmp

# Service principal for SPA
$spaSp = az ad sp show --id $spaAppId 2>$null | ConvertFrom-Json
if (-not $spaSp) { az ad sp create --id $spaAppId -o none; Write-Ok "SPA SP created" }
else { Write-Skip "SPA SP exists" }

# ── Output ──────────────────────────────────────────────────────────────────
Write-Step "Writing output to $OutputPath"
$out = [ordered]@{
    tenantId   = $tenantId
    chatUiFqdn = $ChatUiFqdn
    backend = [ordered]@{
        displayName   = $BackendAppName
        appId         = $backendAppId
        objectId      = $backendObjectId
        identifierUri = $desiredIdUri
        exposedScope  = "Chat.ReadWrite"
        scopeId       = $chatScopeId
    }
    spa = [ordered]@{
        displayName  = $SpaAppName
        appId        = $spaAppId
        objectId     = $spaObjectId
        redirectUris = $desiredRedirects
    }
    apiPermissions = [ordered]@{
        backendNeedsConsentFor = @(
            "Microsoft Graph / User.Read"
            "Azure AI / user_impersonation"
            "WorkIQ ($WORKIQ_APP_ID) / $workIqScopeName"
        )
        spaNeedsConsentFor = @( "$BackendAppName / Chat.ReadWrite" )
    }
}
$out | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Encoding utf8
Write-Ok "Output: $OutputPath"

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " ✓ App regs ready. Permissions set but NOT consented."                -ForegroundColor Cyan
Write-Host ""
Write-Host " IDs to put in your .env:"                                            -ForegroundColor Cyan
Write-Host "   ENTRA_TENANT_ID      = $tenantId"
Write-Host "   ENTRA_BACKEND_APP_ID = $backendAppId"
Write-Host "   ENTRA_SPA_APP_ID     = $spaAppId"
Write-Host ""
Write-Host " Then run: azd up"                                                    -ForegroundColor Cyan
Write-Host " Then re-run this script with -UamiPrincipalId + -ChatUiFqdn."        -ForegroundColor Cyan
Write-Host " Finally, an admin runs: ./scripts/admin/grant-consent.ps1"           -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
