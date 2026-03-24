[CmdletBinding()]
param(
    [string]$IntuneWinAppUtilPath = ".\IntuneWinAppUtil.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$results = [ordered]@{}

$results.Python = [ordered]@{
    Installed = $null -ne (Get-Command python -ErrorAction SilentlyContinue)
    Detail = ''
}
if ($results.Python.Installed) {
    $results.Python.Detail = (& python --version 2>&1 | Out-String).Trim()
}

$results.Winget = [ordered]@{
    Installed = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
    Detail = ''
}
if ($results.Winget.Installed) {
    $results.Winget.Detail = (& winget --version 2>&1 | Out-String).Trim()
}

$results.PowerShellGet = [ordered]@{
    Installed = $null -ne (Get-Module -ListAvailable -Name PowerShellGet)
    Detail = ''
}

$requiredModules = @('Microsoft.Graph.Authentication','Microsoft.Graph.Applications','Microsoft.Graph.Groups','IntuneWin32App')
$moduleStates = @()
foreach ($module in $requiredModules) {
    $installed = Get-Module -ListAvailable -Name $module | Sort-Object Version -Descending | Select-Object -First 1
    $moduleStates += [ordered]@{
        Name = $module
        Installed = $null -ne $installed
        Version = if ($installed) { [string]$installed.Version } else { '' }
    }
}
$results.Modules = $moduleStates

$resolvedTool = Resolve-Path -LiteralPath $IntuneWinAppUtilPath -ErrorAction SilentlyContinue
$results.IntuneWinAppUtil = [ordered]@{
    Installed = $null -ne $resolvedTool
    Path = if ($resolvedTool) { [string]$resolvedTool } else { $IntuneWinAppUtilPath }
}

$results.BootstrapScript = [ordered]@{
    Installed = Test-Path -LiteralPath (Join-Path $PSScriptRoot 'bootstrap-intune-app-registration.ps1')
    Path = (Join-Path $PSScriptRoot 'bootstrap-intune-app-registration.ps1')
}

$results | ConvertTo-Json -Depth 6
