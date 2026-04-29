# azd postdeploy hook — copies the just-deployed mcp-server image from the
# submissions Container App (the one azd deploys to) to the tax + legal
# apps. All 3 MCP apps run the exact same image, just with different
# AGENT_PROFILE env vars set at provision time.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }

# Read azd env (camelCase preserved from bicep outputs).
$rawEnv = & azd env get-values 2>$null
$envMap = @{}
foreach ($line in $rawEnv) {
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$') {
        $envMap[$matches[1]] = $matches[2].Trim('"').Trim("'")
    }
}
function Get-AzdValue($name) {
    if (-not $envMap.ContainsKey($name) -or -not $envMap[$name]) {
        throw "azd env value '$name' is missing or empty."
    }
    return $envMap[$name]
}

$rg               = Get-AzdValue "AZURE_RESOURCE_GROUP"
$submissionsApp   = Get-AzdValue "mcpSubmissionsAppName"
$taxApp           = Get-AzdValue "mcpTaxAppName"
$legalApp         = Get-AzdValue "mcpLegalAppName"

Write-Step "Reading current image from $submissionsApp"
$image = az containerapp show -g $rg -n $submissionsApp --query "properties.template.containers[0].image" -o tsv
if (-not $image) { throw "Could not read image from $submissionsApp." }
Write-Ok "image: $image"

foreach ($target in @($taxApp, $legalApp)) {
    Write-Step "Updating $target -> $image"
    az containerapp update -g $rg -n $target `
        --image $image `
        --revision-suffix "v$(Get-Date -Format HHmmss)" `
        -o none
    Write-Ok "$target updated"
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " ✓ mcp-server image fanned out to tax + legal apps."                 -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan

Write-Step "Seeding Cosmos routing queues (tax + legal)"
$seedScript = Join-Path $PSScriptRoot '..\admin\seed_routing.py'
$uv = Get-Command uv -ErrorAction SilentlyContinue
try {
    if ($uv) {
        # uv reads PEP 723 inline metadata at the top of the script and
        # provisions a throwaway venv with azure-cosmos + azure-identity.
        & uv run $seedScript
        if ($LASTEXITCODE -ne 0) { throw "uv run exited $LASTEXITCODE" }
    } else {
        Write-Warning "uv not found — falling back to system python (must have azure-cosmos + azure-identity installed)."
        & python $seedScript
        if ($LASTEXITCODE -ne 0) { throw "python exited $LASTEXITCODE" }
    }
    Write-Ok "routing seeded"
} catch {
    Write-Warning "Seed step failed: $($_.Exception.Message)"
    Write-Warning "Run 'uv run ./scripts/admin/seed_routing.py' (or install azure-cosmos+azure-identity and run with python) after granting yourself Cosmos Data Contributor."
}
