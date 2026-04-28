// AcrPull role assignment for an additional principal against an existing
// Azure Container Registry. Used to grant pull access to the extra UAMIs
// deployed for the per-profile MCP container apps without modifying
// registry.bicep.

@description('ACR resource name (must already exist in the same RG).')
param registryName string

@description('Principal (object) ID granted AcrPull.')
param principalId string

@description('Stable name suffix to scope the role assignment GUID (e.g. the UAMI name).')
param principalLabel string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, principalId, 'AcrPull', principalLabel)
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
