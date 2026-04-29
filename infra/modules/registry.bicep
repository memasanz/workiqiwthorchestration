// Azure Container Registry (Basic) with admin disabled.
// Grants AcrPull to the supplied managed identity.

@description('ACR name (lowercase alphanumeric, 5-50 chars, globally unique).')
@minLength(5)
@maxLength(50)
param name string

@description('Azure region.')
param location string

@description('Tags.')
param tags object = {}

@description('Optional principal granted inline AcrPull. Empty string skips. Other principals get AcrPull via acr-role-assignment.bicep.')
param acrPullPrincipalId string = ''

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

// Built-in role: AcrPull
var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(acrPullPrincipalId)) {
  scope: acr
  name: guid(acr.id, acrPullPrincipalId, 'AcrPull')
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: acrPullPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
