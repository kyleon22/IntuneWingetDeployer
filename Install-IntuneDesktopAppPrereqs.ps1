[CmdletBinding()]
param(
    [string]$IntuneWinAppUtilSourcePath,
    [string]$IntuneWinAppUtilDestinationPath = ".\IntuneWinAppUtil.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Ensure-ModuleInstalled {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Module -ListAvailable -Name $Name)) {
        Install-Module -Name $Name -Scope CurrentUser -Force -AllowClobber
    }
}

$requiredModules = @(
    'Microsoft.Graph.Authentication',
    'Microsoft.Graph.Applications',
    'Microsoft.Graph.Groups',
    'IntuneWin32App'
)

foreach ($module in $requiredModules) {
    Write-Host "Ensuring module: $module"
    Ensure-ModuleInstalled -Name $module
}

if ($IntuneWinAppUtilSourcePath -and (Test-Path -LiteralPath $IntuneWinAppUtilSourcePath)) {
    Copy-Item -LiteralPath $IntuneWinAppUtilSourcePath -Destination $IntuneWinAppUtilDestinationPath -Force
    Write-Host "Copied IntuneWinAppUtil.exe to: $IntuneWinAppUtilDestinationPath"
}
elseif (-not (Test-Path -LiteralPath $IntuneWinAppUtilDestinationPath)) {
    Write-Warning "IntuneWinAppUtil.exe is still missing. Provide -IntuneWinAppUtilSourcePath or copy it manually."
}

Write-Host 'Dependency installation completed.' -ForegroundColor Green
