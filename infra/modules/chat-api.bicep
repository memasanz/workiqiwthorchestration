// Container App for the chat-api FastAPI service.
// Same shape as mcp-app.bicep but with a distinct env-var set.

@description('Container App name.')
param name string

@description('Azure region.')
param location string

@description('Tags.')
param tags object = {}

@description('Container Apps environment resource ID.')
param environmentId string

@description('Image reference (e.g. <acr>.azurecr.io/chat-api:0.1.0).')
param image string

@description('ACR login server (e.g. <acr>.azurecr.io).')
param registryLoginServer string

@description('User-assigned managed identity resource ID.')
param userAssignedIdentityId string

@description('User-assigned managed identity client ID.')
param userAssignedIdentityClientId string

@description('Foundry project endpoint URL.')
param foundryProjectEndpoint string

@description('Model deployment name used by the Foundry agents.')
param modelDeploymentName string

@description('Submissions MCP backend URL (must include /mcp suffix).')
param submissionsMcpUrl string

@description('Tax SME MCP backend URL (must include /mcp suffix).')
param taxMcpUrl string

@description('Legal SME MCP backend URL (must include /mcp suffix).')
param legalMcpUrl string

@description('App Insights connection string.')
param appInsightsConnectionString string

@description('Bypass Easy Auth header check and accept ?as_user=. Set false in production.')
param devBypassAuth string = 'true'

@description('Entra tenant ID. Empty disables JWT validation (dev only).')
param entraTenantId string = ''

@description('Entra app ID for the backend (mpwflow-api). Used as JWT audience.')
param entraBackendClientId string = ''

@description('Required scope on the bearer token. Defaults to Chat.ReadWrite.')
param entraRequiredScope string = 'Chat.ReadWrite'

@description('Client ID of the user-assigned managed identity that holds the federated identity credential (FIC) for the OBO exchange.')
param entraManagedIdentityClientId string = ''

@description('Registered Foundry PromptAgent name for the submissions participant (chat-api 0.3.0 connects via FoundryAgent).')
param submissionsFoundryAgentName string = 'submissions-agent'

@description('Registered Foundry PromptAgent version for the submissions participant.')
param submissionsFoundryAgentVersion string = '3'

@description('Registered Foundry PromptAgent name for the tax participant.')
param taxFoundryAgentName string = 'tax-sme-agent'

@description('Registered Foundry PromptAgent version for the tax participant.')
param taxFoundryAgentVersion string = '4'

@description('Registered Foundry PromptAgent name for the legal participant.')
param legalFoundryAgentName string = 'legal-sme-agent'

@description('Registered Foundry PromptAgent version for the legal participant.')
param legalFoundryAgentVersion string = '4'

@description('Container CPU (cores).')
param cpu string = '0.5'

@description('Container memory (Gi).')
param memory string = '1.0Gi'

@description('Min replicas.')
param minReplicas int = 0

@description('Max replicas.')
param maxReplicas int = 3

var imageInAcr = contains(image, registryLoginServer)

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: registryLoginServer
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'chat-api'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'AZURE_CLIENT_ID',                       value: userAssignedIdentityClientId }
            { name: 'FOUNDRY_PROJECT_ENDPOINT',              value: foundryProjectEndpoint }
            { name: 'MODEL_DEPLOYMENT_NAME',                 value: modelDeploymentName }
            { name: 'SUBMISSIONS_MCP_URL',                   value: submissionsMcpUrl }
            { name: 'TAX_MCP_URL',                           value: taxMcpUrl }
            { name: 'LEGAL_MCP_URL',                         value: legalMcpUrl }
            { name: 'DEV_BYPASS_AUTH',                       value: devBypassAuth }
            { name: 'ENTRA_TENANT_ID',                       value: entraTenantId }
            { name: 'ENTRA_BACKEND_CLIENT_ID',               value: entraBackendClientId }
            { name: 'ENTRA_REQUIRED_SCOPE',                  value: entraRequiredScope }
            { name: 'MANAGED_IDENTITY_CLIENT_ID',            value: empty(entraManagedIdentityClientId) ? userAssignedIdentityClientId : entraManagedIdentityClientId }
            { name: 'SUBMISSIONS_FOUNDRY_AGENT_NAME',        value: submissionsFoundryAgentName }
            { name: 'SUBMISSIONS_FOUNDRY_AGENT_VERSION',     value: submissionsFoundryAgentVersion }
            { name: 'TAX_FOUNDRY_AGENT_NAME',                value: taxFoundryAgentName }
            { name: 'TAX_FOUNDRY_AGENT_VERSION',             value: taxFoundryAgentVersion }
            { name: 'LEGAL_FOUNDRY_AGENT_NAME',              value: legalFoundryAgentName }
            { name: 'LEGAL_FOUNDRY_AGENT_VERSION',           value: legalFoundryAgentVersion }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'PORT',                                  value: '8080' }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output name string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
output id string = app.id
