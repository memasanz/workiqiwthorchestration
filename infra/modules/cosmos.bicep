// Cosmos DB (NoSQL API) with one database and three containers:
//   - projects        pk /projectId
//   - questions       pk /projectId
//   - routing         pk /category
//
// Grants Built-in Data Contributor data-plane RBAC to dataPlanePrincipalId
// (the MCP server's user-assigned managed identity). Account keys are NOT
// used by the MCP server.

@description('Cosmos DB account name (lowercase letters, numbers, hyphens; 3-44 chars).')
param accountName string

@description('Database name.')
param databaseName string = 'workflow'

@description('Azure region for the account write region.')
param location string

@description('Tags.')
param tags object = {}

@description('Principal (object) ID of the managed identity that should get data-plane Contributor access.')
param dataPlanePrincipalId string

@description('Throughput mode. autoscale uses an autoscale max RU; serverless ignores RU.')
@allowed([ 'serverless', 'autoscale' ])
param throughputMode string = 'serverless'

@description('Autoscale max RU (only used when throughputMode = autoscale). Per-database shared throughput.')
param autoscaleMaxRu int = 1000

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: throughputMode == 'serverless'
      ? [ { name: 'EnableServerless' } ]
      : []
    disableLocalAuth: true // forces RBAC; no master key auth
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous7Days'
      }
    }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
    options: throughputMode == 'autoscale' ? {
      autoscaleSettings: {
        maxThroughput: autoscaleMaxRu
      }
    } : {}
  }
}

var containers = [
  {
    name: 'projects'
    partitionKey: '/projectId'
  }
  {
    name: 'questions'
    partitionKey: '/projectId'
  }
  {
    name: 'routing'
    partitionKey: '/category'
  }
]

resource sqlContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for c in containers: {
  parent: database
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: {
        paths: [ c.partitionKey ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [ { path: '/*' } ]
        excludedPaths: [ { path: '/"_etag"/?' } ]
      }
    }
  }
}]

// Built-in data-plane role: Cosmos DB Built-in Data Contributor
var dataContributorRoleDefinitionId = '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, dataPlanePrincipalId, 'data-contributor')
  properties: {
    roleDefinitionId: dataContributorRoleDefinitionId
    principalId: dataPlanePrincipalId
    scope: account.id
  }
}

output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output accountName string = account.name
output accountId string = account.id
