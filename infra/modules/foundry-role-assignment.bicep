// Grants 'Azure AI Developer' + 'Cognitive Services User' to a principal at
// the scope of an existing Cognitive Services / AIServices (Foundry) account.
// Used so the group-chat-api UAMI can call FoundryChatClient + FoundryAgent.

@description('Foundry (Cognitive Services / AIServices) account name in the same RG.')
param accountName string

@description('Principal (object) ID granted the roles.')
param principalId string

@description('Stable label used to scope the role-assignment GUIDs (typically the UAMI name).')
param principalLabel string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

var azureAiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource aiDev 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, principalId, 'AzureAIDeveloper', principalLabel)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiDeveloperRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource cogUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, principalId, 'CognitiveServicesUser', principalLabel)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
