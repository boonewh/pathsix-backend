param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Runtime', 'Administrator')]
    [string]$Mode
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'PathSix\staging-database-access'
$fileName = if ($Mode -eq 'Runtime') { 'runtime-url.dpapi' } else { 'administrator-url.dpapi' }
$secureValue = ConvertTo-SecureString ((Get-Content -LiteralPath (Join-Path $stateDir $fileName) -Raw).Trim())
try {
    $connectionValue = [System.Net.NetworkCredential]::new('', $secureValue).Password
    $connectionUri = [Uri]$connectionValue
    $expectedUser = if ($Mode -eq 'Runtime') { 'pathsix_crm_staging_runtime' } else { 'pathsixsolutions_backend_staging' }
    if ($connectionUri.Host -notin @('pathsixsolutions-db-staging.flycast','pathsixsolutions-db-staging.internal') -or
        $connectionUri.AbsolutePath -ne '/pathsixsolutions_backend_staging' -or
        $connectionUri.UserInfo.Split(':')[0] -ne $expectedUser) {
        throw 'Refusing unexpected staging connection target'
    }
    $stagingConfig = Join-Path (Split-Path -Parent $PSScriptRoot) 'fly.staging.toml'
    "DATABASE_URL=$connectionValue" | flyctl secrets import --app pathsixsolutions-backend-staging --config $stagingConfig
    if ($LASTEXITCODE -ne 0) { throw 'Staging secret import did not complete successfully' }
    Write-Output "Staging database connection switched to $Mode; verify health and database identity."
} finally {
    $connectionValue = $null
    $connectionUri = $null
    $secureValue.Dispose()
}
