// Resource-group-scope deployment for the multi-person workflow.
//
// Provisions:
//   - Log Analytics + App Insights
//   - Cosmos DB (account, db, 3 containers; data-plane RBAC granted to each
//     per-profile UAMI)
//   - Azure Container Registry (Basic, AcrPull granted per-app UAMI)
//   - Container Apps Environment
//   - 3 per-profile MCP Container Apps (submissions, tax, legal)
//   - chat-api Container App (FastAPI orchestrator, OBO+FIC enabled)
//   - chat-ui Container App (React SPA, MSAL.js)
//
// Foundry account/project + WorkIQ catalog connections are PREREQUISITES
// (manual M365/Foundry portal steps). Pass the existing project endpoint
// + model deployment via .env / azd env. Entra app regs are also
// prerequisites — created by scripts/admin/setup-entra.ps1.
//
// Deploy via `azd up` (preferred) or:
//   az deployment group create -g <rg> -f infra/main.bicep -p infra/main.bicepparam

targetScope = 'resourceGroup'

@description('Short base name; lowercase letters/numbers, 3-12 chars.')
@minLength(3)
@maxLength(12)
param baseName string

@description('Environment suffix (dev, test, prod, ncus).')
param environmentName string = 'dev'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Container image for the per-profile MCP container apps. azd swaps this on `azd deploy`.')
param mcpProfileImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Container image for the chat-api FastAPI service. azd swaps this on `azd deploy`.')
param chatApiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Container image for the chat-ui SPA. azd swaps this on `azd deploy`.')
param chatUiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Bypass Easy Auth on the chat-api Container App and accept ?as_user=. Set to "false" outside dev.')
param chatApiDevBypassAuth string = 'false'

@description('Foundry project endpoint URL (https://<aif>.services.ai.azure.com/api/projects/<proj>). REQUIRED — Foundry is a manual prerequisite.')
param foundryProjectEndpoint string

@description('Chat model deployment name on the Foundry project (e.g. gpt-4o-mini).')
param foundryModelDeploymentName string

@description('Entra tenant ID for end-user JWT validation + OBO+FIC.')
param entraTenantId string

@description('Entra app ID for the backend (mpwflow-api). Used as JWT audience and OBO client_id.')
param entraBackendAppId string

@description('Entra app ID for the SPA (mpwflow-spa). Plumbed into the chat-ui build via Vite env vars.')
param entraSpaAppId string

var envSuffix = startsWith(toLower(environmentName), toLower('${baseName}-')) ? substring(environmentName, length(baseName) + 1) : environmentName
var nameSuffix = toLower('${baseName}-${envSuffix}')
var nameSuffixCompact = toLower(replace('${baseName}${envSuffix}', '-', ''))
var tags = {
  workload: 'multipersonworkflow'
  env: envSuffix
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
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    name: 'acr${nameSuffixCompact}${uniqueString(resourceGroup().id)}'
    location: location
    tags: tags
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

// -------------------------------------------------------------------------
// Per-profile MCP backends — one UAMI + one Container App per Foundry agent.
// All share the same image (per-profile via the AGENT_PROFILE env var).
// -------------------------------------------------------------------------

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
    // Tag only the first (submissions) app as the azd service. The
    // postdeploy hook (scripts/post-deploy/fanout_mcp_image.ps1) copies
    // the same image to the tax + legal apps after `azd deploy`.
    tags: i == 0 ? union(tags, { 'azd-service-name': 'mcp-server' }) : tags
    environmentId: containerEnv.outputs.environmentId
    image: mcpProfileImage
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
// chat-api Container App (FastAPI orchestrator).
// UAMI gets AcrPull only — the chat-api hits the MCP backends over plain
// HTTP without auth, and uses OBO+FIC to call Foundry as the user.
// -------------------------------------------------------------------------

module chatApiIdentity 'modules/identity.bicep' = {
  name: 'identity-chat-api'
  params: {
    name: 'id-${nameSuffix}-chat-api'
    location: location
    tags: tags
  }
}

module chatApiAcrRbac 'modules/acr-role-assignment.bicep' = {
  name: 'acr-rbac-chat-api'
  params: {
    registryName: registry.outputs.name
    principalId: chatApiIdentity.outputs.principalId
    principalLabel: 'chat-api'
  }
}

module chatApiApp 'modules/chat-api.bicep' = {
  name: 'chat-api-app'
  params: {
    name: 'ca-${nameSuffix}-chat-api'
    location: location
    tags: union(tags, { 'azd-service-name': 'chat-api' })
    environmentId: containerEnv.outputs.environmentId
    image: chatApiImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: chatApiIdentity.outputs.id
    userAssignedIdentityClientId: chatApiIdentity.outputs.clientId
    foundryProjectEndpoint: foundryProjectEndpoint
    modelDeploymentName: foundryModelDeploymentName
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
  ]
}

// -------------------------------------------------------------------------
// chat-ui — React SPA served by nginx, proxies /api/* to the chat-api FQDN.
// UAMI gets AcrPull only.
// -------------------------------------------------------------------------

module chatUiIdentity 'modules/identity.bicep' = {
  name: 'identity-chat-ui'
  params: {
    name: 'id-${nameSuffix}-chat-ui'
    location: location
    tags: tags
  }
}

module chatUiAcrRbac 'modules/acr-role-assignment.bicep' = {
  name: 'acr-rbac-chat-ui'
  params: {
    registryName: registry.outputs.name
    principalId: chatUiIdentity.outputs.principalId
    principalLabel: 'chat-ui'
  }
}

module chatUiApp 'modules/chat-ui.bicep' = {
  name: 'chat-ui-app'
  params: {
    name: 'ca-${nameSuffix}-chat-ui'
    location: location
    tags: union(tags, { 'azd-service-name': 'chat-ui' })
    environmentId: containerEnv.outputs.environmentId
    image: chatUiImage
    registryLoginServer: registry.outputs.loginServer
    userAssignedIdentityId: chatUiIdentity.outputs.id
    backendBaseUrl: 'https://${chatApiApp.outputs.fqdn}'
  }
  dependsOn: [
    chatUiAcrRbac
  ]
}

// -------------------------------------------------------------------------
// Outputs (consumed by azd hooks: postprovision agent registration etc.)
// -------------------------------------------------------------------------

output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabase string = cosmos.outputs.databaseName
output cosmosAccountName string = cosmos.outputs.accountName
output registryLoginServer string = registry.outputs.loginServer
output registryName string = registry.outputs.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output mcpSubmissionsAppName string = profileMcpApp[0].outputs.name
output mcpSubmissionsAppFqdn string = profileMcpApp[0].outputs.fqdn
output mcpTaxAppName string = profileMcpApp[1].outputs.name
output mcpTaxAppFqdn string = profileMcpApp[1].outputs.fqdn
output mcpLegalAppName string = profileMcpApp[2].outputs.name
output mcpLegalAppFqdn string = profileMcpApp[2].outputs.fqdn
output chatApiUamiPrincipalId string = chatApiIdentity.outputs.principalId
output chatApiUamiClientId string = chatApiIdentity.outputs.clientId
output chatApiAppName string = chatApiApp.outputs.name
output chatApiAppFqdn string = chatApiApp.outputs.fqdn
output chatUiAppName string = chatUiApp.outputs.name
output chatUiAppFqdn string = chatUiApp.outputs.fqdn
output foundryProjectEndpoint string = foundryProjectEndpoint
output modelDeploymentName string = foundryModelDeploymentName
output entraSpaAppId string = entraSpaAppId
output entraBackendAppId string = entraBackendAppId
output entraTenantId string = entraTenantId
