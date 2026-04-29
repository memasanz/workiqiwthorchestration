// Container App for the chat-ui SPA (nginx serving the React build).
// Same shape as chat-api.bicep but with a UI-specific env-var set.

@description('Container App name.')
param name string

@description('Azure region.')
param location string

@description('Tags.')
param tags object = {}

@description('Container Apps environment resource ID.')
param environmentId string

@description('Image reference (e.g. <acr>.azurecr.io/chat-ui:0.1.0).')
param image string

@description('ACR login server (e.g. <acr>.azurecr.io).')
param registryLoginServer string

@description('User-assigned managed identity resource ID.')
param userAssignedIdentityId string

@description('Backend base URL used by nginx reverse proxy for /api/*.')
param backendBaseUrl string

@description('Container CPU (cores).')
param cpu string = '0.25'

@description('Container memory (Gi).')
param memory string = '0.5Gi'

@description('Min replicas.')
param minReplicas int = 0

@description('Max replicas.')
param maxReplicas int = 2

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
          name: 'chat-ui'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'BACKEND_BASE_URL', value: backendBaseUrl }
            { name: 'PORT',             value: '8080' }
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
