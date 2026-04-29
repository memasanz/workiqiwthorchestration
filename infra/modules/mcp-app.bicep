// Azure Container App that hosts the FastMCP server.
//   - User-assigned managed identity for ACR pull + Cosmos data plane
//   - External HTTPS ingress on port 8080
//   - Scale 0 to 3 on HTTP concurrency
//   - Cosmos endpoint, database name, and App Insights connection string
//     injected as env vars (no keys / secrets)

@description('Container App name.')
param name string

@description('Azure region.')
param location string

@description('Tags.')
param tags object = {}

@description('Container Apps environment resource ID.')
param environmentId string

@description('Image reference (e.g. <acr>.azurecr.io/mcp-server:0.1.0). Placeholder allowed for first deploy.')
param image string

@description('ACR login server (e.g. <acr>.azurecr.io). Used for the registry block when image points to ACR.')
param registryLoginServer string

@description('User-assigned managed identity resource ID.')
param userAssignedIdentityId string

@description('User-assigned managed identity client ID (used by Azure SDK DefaultAzureCredential).')
param userAssignedIdentityClientId string

@description('Cosmos endpoint (https://<account>.documents.azure.com:443/).')
param cosmosEndpoint string

@description('Cosmos database name.')
param cosmosDatabase string

@description('App Insights connection string.')
param appInsightsConnectionString string

@description('Profile that gates which MCP tools this app exposes. Empty string keeps the legacy single-app behavior (no AGENT_PROFILE env var injected — not recommended).')
@allowed([ '', 'submissions', 'tax_sme', 'legal_sme' ])
param agentProfile string = ''

@description('Container CPU (cores).')
param cpu string = '0.5'

@description('Container memory (Gi).')
param memory string = '1.0Gi'

@description('Min replicas (0 enables scale-to-zero).')
param minReplicas int = 0

@description('Max replicas.')
param maxReplicas int = 3

// If the image is hosted in our ACR, register the registry pull identity.
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
          name: 'mcp-server'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat([
            {
              name: 'AZURE_CLIENT_ID'
              value: userAssignedIdentityClientId
            }
            {
              name: 'COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'COSMOS_DATABASE'
              value: cosmosDatabase
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'PORT'
              value: '8080'
            }
          ], empty(agentProfile) ? [] : [
            {
              name: 'AGENT_PROFILE'
              value: agentProfile
            }
          ])
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
