// Azure Container Apps environment wired to a Log Analytics workspace.

@description('Container Apps environment name.')
param name string

@description('Azure region.')
param location string

@description('Tags.')
param tags object = {}

@description('Log Analytics customer (workspace) ID.')
param logAnalyticsCustomerId string

@description('Log Analytics primary shared key.')
@secure()
param logAnalyticsSharedKey string

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    zoneRedundant: false
  }
}

output environmentId string = env.id
output environmentName string = env.name
