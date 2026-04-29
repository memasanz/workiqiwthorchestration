// azd-driven parameters. Reads from `azd env` (set via .env at first run).
// Image params are intentionally omitted — Bicep defaults to a placeholder
// image, and `azd deploy` swaps each Container App to the just-built image.

using './main.bicep'

param baseName = readEnvironmentVariable('AZURE_BASE_NAME', 'mpwflow')
param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'dev')
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')

param foundryProjectEndpoint = readEnvironmentVariable('FOUNDRY_PROJECT_ENDPOINT')
param foundryModelDeploymentName = readEnvironmentVariable('FOUNDRY_MODEL_DEPLOYMENT_NAME', 'gpt-4o-mini')

param entraTenantId = readEnvironmentVariable('ENTRA_TENANT_ID')
param entraBackendAppId = readEnvironmentVariable('ENTRA_BACKEND_APP_ID')
param entraSpaAppId = readEnvironmentVariable('ENTRA_SPA_APP_ID')

param chatApiDevBypassAuth = readEnvironmentVariable('CHAT_API_DEV_BYPASS_AUTH', 'false')

param adminPrincipalId = readEnvironmentVariable('ADMIN_PRINCIPAL_ID', '')
