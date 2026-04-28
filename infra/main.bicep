// Resource-group scope. Provisions:
//   - User-assigned managed identity (UAMI) used by the MCP container app
//   - Log Analytics + App Insights
//   - Cosmos DB account, database, 3 containers, data-plane RBAC for the UAMI
//   - Azure Container Registry (UAMI granted AcrPull)
//   - Container Apps Environment
//   - MCP server Container App (placeholder image; swap after build/push)
//
// Deploy with:
//   az group create -n <rg> -l <region>
//   az deployment group create -g <rg> -f infra/main.bicep -p infra/main.parameters.json

targetScope = 'resourceGroup'

@description('Short base name; lowercase letters/numbers, 3-12 chars.')
@minLength(3)
@maxLength(12)
param baseName string

@description('Environment suffix (dev, test, prod).')
@allowed([ 'dev', 'test', 'prod' ])
param environmentName string = 'dev'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Container image for the MCP server. Defaults to a placeholder.')
param mcpImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Container image for the per-profile MCP container apps (mcp-server:0.2.0+). Falls back to mcpImage if empty.')
param mcpProfileImage string = ''

@description('Container image for the chat-api FastAPI service (chat-api:0.3.1+ uses Microsoft Agent Framework FoundryChatClient with hosted MCP tools and an explicit HITL approval flow).')
param chatApiImage string = 'acrmpwflowdeva3qzr7isqw476.azurecr.io/chat-api:0.3.1'

@description('Container image for the chat-ui SPA (chat-ui:0.1.0+). Empty disables the deployment.')
param chatUiImage string = 'chat-ui:0.1.0'

@description('Bypass Easy Auth on the chat-api Container App and accept ?as_user=. Set to "false" once Easy Auth is configured.')
param chatApiDevBypassAuth string = 'true'

@description('Entra tenant ID for end-user JWT validation + OBO+FIC.')
param entraTenantId string = ''

@description('Entra app ID for the backend (mpwflow-api). Used as JWT audience and OBO client_id.')
param entraBackendAppId string = ''

@description('Entra app ID for the SPA (mpwflow-spa). Plumbed into the chat-ui build via Vite env vars.')
param entraSpaAppId string = ''

@description('Azure region for the Foundry account. Defaults to the resource group location. Override if the chosen model is not available in the main region.')
param foundryLocation string = resourceGroup().location

@description('Chat model deployment name used by the Foundry agents.')
param foundryModelDeploymentName string = 'gpt-4o-mini'

@description('Chat model name.')
param foundryModelName string = 'gpt-4o-mini'

@description('Chat model version.')
param foundryModelVersion string = '2024-07-18'

@description('Chat model SKU (GlobalStandard is the cheapest pay-as-you-go SKU).')
param foundryModelSkuName string = 'GlobalStandard'

@description('Chat model capacity in thousands of TPM. 30 = 30K TPM.')
param foundryModelCapacity int = 30

@description('Secondary chat model deployment name (gpt-5.3 family). Empty disables.')
param foundrySecondaryModelDeploymentName string = ''

@description('Secondary chat model name.')
param foundrySecondaryModelName string = ''

@description('Secondary chat model version.')
param foundrySecondaryModelVersion string = ''

@description('Secondary chat model SKU.')
param foundrySecondaryModelSkuName string = 'GlobalStandard'

@description('Secondary chat model capacity in thousands of TPM.')
param foundrySecondaryModelCapacity int = 30

var nameSuffix = toLower('${baseName}-${environmentName}')
var nameSuffixCompact = toLower(replace('${baseName}${environmentName}', '-', ''))
var tags = {
  workload: 'multipersonworkflow'
  env: environmentName
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: 'id-${nameSuffix}-mcp'
    location: location
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    logAnalyticsName: 'log-${nameSuffix}'
    appInsightsName: 'appi-${nameSuffix}'
    location: location
    tags: tags
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    accountName: 'cosmos-${nameSuffix}-${uniqueString(resourceGroup().id)}'
    databaseName: 'workflow'
    location: location
    tags: tags
    dataPlanePrincipalId: identity.outputs.principalId
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    name: 'acr${nameSuffixCompact}${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
    acrPullPrincipalId: identity.outputs.principalId
  }
}

module containerEnv 'modules/container-env.bicep' = {
  name: 'container-env'
  params: {
    name: 'cae-${nameSuffix}'
    location: location
    tags: tags
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: monitoring.outputs.logAnalyticsSharedKey
  }
}

module mcpApp 'modules/mcp-app.bicep' = {
  name: 'mcp-app'
  params: {
    name: 'ca-${nameSuffix}-mcp'
    location: location
    tags: tags
    environmentId: containerEnv.outputs.environmentId
    image: mcpImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: identity.outputs.id
    userAssignedIdentityClientId: identity.outputs.clientId
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDatabase: cosmos.outputs.databaseName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// -------------------------------------------------------------------------
// Iteration 2 / Plan B — split MCP into 3 profile-filtered backends.
// One UAMI + one Container App per Foundry agent. All share the same image
// (per-profile via the AGENT_PROFILE env var).
//
// NOTE: the legacy single-app `mcpApp` above is intentionally left in place
// so we can verify in production before removing. After validation, delete
// the legacy app (and its identity, if not used elsewhere) by hand:
//     az containerapp delete -g rg-mpwflow-dev -n ca-mpwflow-dev-mcp -y
// -------------------------------------------------------------------------

var profileImage = empty(mcpProfileImage) ? mcpImage : mcpProfileImage

var profiles = [
  { key: 'submissions', appSuffix: 'submissions', profile: 'submissions' }
  { key: 'tax',         appSuffix: 'tax',         profile: 'tax_sme' }
  { key: 'legal',       appSuffix: 'legal',       profile: 'legal_sme' }
]

module profileIdentity 'modules/identity.bicep' = [for p in profiles: {
  name: 'identity-${p.key}'
  params: {
    name: 'id-${nameSuffix}-mcp-${p.appSuffix}'
    location: location
    tags: tags
  }
}]

module profileCosmosRbac 'modules/cosmos-role-assignment.bicep' = [for (p, i) in profiles: {
  name: 'cosmos-rbac-${p.key}'
  params: {
    cosmosAccountName: cosmos.outputs.accountName
    principalId: profileIdentity[i].outputs.principalId
    principalLabel: 'mcp-${p.appSuffix}'
  }
}]

module profileAcrRbac 'modules/acr-role-assignment.bicep' = [for (p, i) in profiles: {
  name: 'acr-rbac-${p.key}'
  params: {
    registryName: registry.outputs.name
    principalId: profileIdentity[i].outputs.principalId
    principalLabel: 'mcp-${p.appSuffix}'
  }
}]

module profileMcpApp 'modules/mcp-app.bicep' = [for (p, i) in profiles: {
  name: 'mcp-app-${p.key}'
  params: {
    name: 'ca-${nameSuffix}-mcp-${p.appSuffix}'
    location: location
    tags: tags
    environmentId: containerEnv.outputs.environmentId
    image: profileImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: profileIdentity[i].outputs.id
    userAssignedIdentityClientId: profileIdentity[i].outputs.clientId
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDatabase: cosmos.outputs.databaseName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    agentProfile: p.profile
  }
  dependsOn: [
    profileCosmosRbac[i]
    profileAcrRbac[i]
  ]
}]

// -------------------------------------------------------------------------
// Iteration 2 / Plan C1 — chat-api Container App.
// One UAMI (AcrPull only — chat-api hits the MCP backends over plain HTTP
// without auth, so no Cosmos role is needed).
// -------------------------------------------------------------------------

module chatApiIdentity 'modules/identity.bicep' = if (!empty(chatApiImage)) {
  name: 'identity-chat-api'
  params: {
    name: 'id-${nameSuffix}-chat-api'
    location: location
    tags: tags
  }
}

module chatApiAcrRbac 'modules/acr-role-assignment.bicep' = if (!empty(chatApiImage)) {
  name: 'acr-rbac-chat-api'
  params: {
    registryName: registry.outputs.name
    principalId: chatApiIdentity.outputs.principalId
    principalLabel: 'chat-api'
  }
}

module chatApiFoundryRbac 'modules/foundry-role-assignment.bicep' = if (!empty(chatApiImage)) {
  name: 'foundry-rbac-chat-api'
  params: {
    accountName: foundry.outputs.accountName
    principalId: chatApiIdentity.outputs.principalId
    principalLabel: 'chat-api'
  }
}

module chatApiApp 'modules/chat-api.bicep' = if (!empty(chatApiImage)) {
  name: 'chat-api-app'
  params: {
    name: 'ca-${nameSuffix}-chat-api'
    location: location
    tags: tags
    environmentId: containerEnv.outputs.environmentId
    image: chatApiImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: chatApiIdentity.outputs.id
    userAssignedIdentityClientId: chatApiIdentity.outputs.clientId
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    modelDeploymentName: empty(foundrySecondaryModelDeploymentName) ? foundryModelDeploymentName : foundrySecondaryModelDeploymentName
    submissionsMcpUrl: 'https://${profileMcpApp[0].outputs.fqdn}/mcp'
    taxMcpUrl: 'https://${profileMcpApp[1].outputs.fqdn}/mcp'
    legalMcpUrl: 'https://${profileMcpApp[2].outputs.fqdn}/mcp'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    devBypassAuth: chatApiDevBypassAuth
    entraTenantId: entraTenantId
    entraBackendClientId: entraBackendAppId
    entraManagedIdentityClientId: chatApiIdentity.outputs.clientId
  }
  dependsOn: [
    chatApiAcrRbac
    chatApiFoundryRbac
  ]
}

// -------------------------------------------------------------------------
// chat-api 0.3.1: FoundryChatClient + hosted MCP tools + HITL approval flow.
// The earlier Magentic group-chat-api parked attempt has been fully removed
// (folder deleted, bicep module removed). The Container App and UAMI are
// deleted out-of-band.
// -------------------------------------------------------------------------

// -------------------------------------------------------------------------
// chat-ui — React SPA served by nginx, proxies /api/* to the chat-api FQDN.
// UAMI gets AcrPull only (no Cosmos / Foundry access needed).
// -------------------------------------------------------------------------

module chatUiIdentity 'modules/identity.bicep' = if (!empty(chatUiImage)) {
  name: 'identity-chat-ui'
  params: {
    name: 'id-${nameSuffix}-chat-ui'
    location: location
    tags: tags
  }
}

module chatUiAcrRbac 'modules/acr-role-assignment.bicep' = if (!empty(chatUiImage)) {
  name: 'acr-rbac-chat-ui'
  params: {
    registryName: registry.outputs.name
    principalId: chatUiIdentity.outputs.principalId
    principalLabel: 'chat-ui'
  }
}

module chatUiApp 'modules/chat-ui.bicep' = if (!empty(chatUiImage)) {
  name: 'chat-ui-app'
  params: {
    name: 'ca-${nameSuffix}-chat-ui'
    location: location
    tags: tags
    environmentId: containerEnv.outputs.environmentId
    image: chatUiImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: chatUiIdentity.outputs.id
    backendBaseUrl: empty(chatApiImage) ? 'https://ca-mpwflow-dev-chat-api.icyground-4e2c6fde.eastus2.azurecontainerapps.io' : 'https://${chatApiApp.outputs.fqdn}'
  }
  dependsOn: [
    chatUiAcrRbac
  ]
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    accountName: 'aif-${nameSuffix}-${uniqueString(resourceGroup().id)}'
    projectName: 'proj-${nameSuffix}'
    location: foundryLocation
    tags: tags
    agentPrincipalId: identity.outputs.principalId
    modelDeploymentName: foundryModelDeploymentName
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    modelSkuName: foundryModelSkuName
    modelCapacity: foundryModelCapacity
    secondaryModelDeploymentName: foundrySecondaryModelDeploymentName
    secondaryModelName: foundrySecondaryModelName
    secondaryModelVersion: foundrySecondaryModelVersion
    secondaryModelSkuName: foundrySecondaryModelSkuName
    secondaryModelCapacity: foundrySecondaryModelCapacity
  }
}

output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabase string = cosmos.outputs.databaseName
output cosmosAccountName string = cosmos.outputs.accountName
output registryLoginServer string = registry.outputs.loginServer
output registryName string = registry.outputs.name
output mcpAppFqdn string = mcpApp.outputs.fqdn
output mcpAppName string = mcpApp.outputs.name
output mcpSubmissionsAppName string = profileMcpApp[0].outputs.name
output mcpSubmissionsAppFqdn string = profileMcpApp[0].outputs.fqdn
output mcpTaxAppName string = profileMcpApp[1].outputs.name
output mcpTaxAppFqdn string = profileMcpApp[1].outputs.fqdn
output mcpLegalAppName string = profileMcpApp[2].outputs.name
output mcpLegalAppFqdn string = profileMcpApp[2].outputs.fqdn
output userAssignedIdentityId string = identity.outputs.id
output userAssignedIdentityClientId string = identity.outputs.clientId
output userAssignedIdentityPrincipalId string = identity.outputs.principalId
output foundryAccountName string = foundry.outputs.accountName
output foundryHubName string = foundry.outputs.accountName
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output modelDeploymentName string = foundry.outputs.modelDeploymentName
output secondaryModelDeploymentName string = foundry.outputs.secondaryModelDeploymentName
output chatApiAppName string = empty(chatApiImage) ? '' : chatApiApp.outputs.name
output chatApiAppFqdn string = empty(chatApiImage) ? '' : chatApiApp.outputs.fqdn
output chatUiAppName string = empty(chatUiImage) ? '' : chatUiApp.outputs.name
output chatUiAppFqdn string = empty(chatUiImage) ? '' : chatUiApp.outputs.fqdn

// Iter5 — surface SPA app id so deployment scripts can confirm the right
// app reg is being used (the value is also baked into the chat-ui image at
// build time via VITE_SPA_CLIENT_ID).
output entraSpaAppId string = entraSpaAppId
output entraBackendAppId string = entraBackendAppId
output entraTenantId string = entraTenantId
