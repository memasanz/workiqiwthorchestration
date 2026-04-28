// Cosmos DB Built-in Data Contributor role assignment for an additional principal
// against an existing Cosmos account. Used to grant data-plane access to the
// extra UAMIs deployed for the per-profile MCP container apps without
// modifying cosmos.bicep.

@description('Cosmos DB account name (must already exist in the same RG).')
param cosmosAccountName string

@description('Principal (object) ID granted Cosmos Built-in Data Contributor.')
param principalId string

@description('Stable name suffix to scope the role assignment GUID (e.g. the UAMI name).')
param principalLabel string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

var dataContributorRoleDefinitionId = '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, principalId, 'data-contributor', principalLabel)
  properties: {
    roleDefinitionId: dataContributorRoleDefinitionId
    principalId: principalId
    scope: account.id
  }
}
