// Azure AI Foundry account (modern resource) + project + chat model deployment.
//
// Uses the modern Foundry shape:
//   - Microsoft.CognitiveServices/accounts kind=AIServices, allowProjectManagement=true
//   - Microsoft.CognitiveServices/accounts/projects (Foundry project)
//   - Microsoft.CognitiveServices/accounts/deployments (chat model)
//
// Grants the supplied user-assigned managed identity the
// "Azure AI Developer" role on the account so it can create and run agents
// (this role inherits down to projects), plus "Cognitive Services User" so
// it can call the model deployment with Entra-only auth.

@description('Foundry (Cognitive Services / AIServices) account name. Globally unique-ish; lowercased.')
@minLength(2)
@maxLength(64)
param accountName string

@description('Foundry project name (resource and friendly name).')
param projectName string

@description('Azure region for the Foundry account and project.')
param location string

@description('Tags.')
param tags object = {}

@description('Principal (object) ID of the user-assigned MI that will create and run agents.')
param agentPrincipalId string

@description('Chat model deployment name (used by the agents).')
param modelDeploymentName string = 'gpt-4o-mini'

@description('Model name.')
param modelName string = 'gpt-4o-mini'

@description('Model version.')
param modelVersion string = '2024-07-18'

@description('Model format / publisher.')
param modelFormat string = 'OpenAI'

@description('Model deployment SKU. GlobalStandard is the cheapest pay-as-you-go SKU for gpt-4o-mini.')
param modelSkuName string = 'GlobalStandard'

@description('Model deployment capacity in thousands of TPM. 30 = 30K TPM.')
param modelCapacity int = 30

@description('Secondary chat model deployment name (e.g. gpt-5.3 family). Set empty string to skip.')
param secondaryModelDeploymentName string = ''

@description('Secondary model name.')
param secondaryModelName string = ''

@description('Secondary model version.')
param secondaryModelVersion string = ''

@description('Secondary model format / publisher.')
param secondaryModelFormat string = 'OpenAI'

@description('Secondary model deployment SKU.')
param secondaryModelSkuName string = 'GlobalStandard'

@description('Secondary model deployment capacity in thousands of TPM.')
param secondaryModelCapacity int = 30

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'Foundry project for the multi-person workflow agents.'
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: modelDeploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: modelFormat
      name: modelName
      version: modelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  // Project must exist before deployments so the project sees the model.
  dependsOn: [
    project
  ]
}

resource secondaryModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!empty(secondaryModelDeploymentName)) {
  parent: foundry
  name: secondaryModelDeploymentName
  sku: {
    name: secondaryModelSkuName
    capacity: secondaryModelCapacity
  }
  properties: {
    model: {
      format: secondaryModelFormat
      name: secondaryModelName
      version: secondaryModelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    project
    modelDeployment
  ]
}

// Built-in role definitions
// Azure AI Developer: create and manage agents on Foundry projects
var azureAiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'
// Cognitive Services User: call data-plane (model deployments) with Entra
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource aiDevRoleAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, agentPrincipalId, azureAiDeveloperRoleId)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiDeveloperRoleId)
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource cogUserRoleAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, agentPrincipalId, cognitiveServicesUserRoleId)
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output accountName string = foundry.name
output accountId string = foundry.id
output accountEndpoint string = foundry.properties.endpoint
// Foundry project endpoint that the AIProjectClient SDK expects:
//   https://<account>.services.ai.azure.com/api/projects/<project>
output projectName string = project.name
output projectId string = project.id
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output modelDeploymentName string = modelDeployment.name
output modelName string = modelName
output secondaryModelDeploymentName string = empty(secondaryModelDeploymentName) ? '' : secondaryModelDeployment.name
