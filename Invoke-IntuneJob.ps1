[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$AppName,

    [string]$PackageId,

    [Parameter(Mandatory)]
    [string]$TenantId,

    [string]$OutputRoot = ".\output",

    [ValidateSet('x64','x86','arm64','neutral','any')]
    [string]$Architecture = 'x64',

    [ValidateSet('new','update')]
    [string]$DeploymentType,

    [ValidateSet('all','group')]
    [string]$NewAppAssignment = 'group',

    [string]$TargetGroupName,

    [string]$IntuneWinAppUtilPath = ".\IntuneWinAppUtil.exe",

    [string]$LocalInstallerPath,

    [string]$CustomDisplayName,

    [string]$CustomDescription,

    [string]$CustomPublisher,

    [string]$SupersedeAppId,

    [ValidateSet('Update','Replace')]
    [string]$SupersedenceType = 'Update',

    [switch]$UseIntuneGraphAuth,

    [string]$IntuneClientId,

    [string]$IntuneModuleVersion,

    [switch]$IntuneDeviceCode,

    [string]$OverrideConfigPath,

    [switch]$UseAzCopy,

    [switch]$WhatIf
)

$packageParams = @{
    AppName = $AppName
    OutputRoot = $OutputRoot
    Architecture = $Architecture
    IntuneWinAppUtilPath = $IntuneWinAppUtilPath
}
if ($PackageId) { $packageParams.PackageId = $PackageId }
if ($DeploymentType) { $packageParams.DeploymentType = $DeploymentType }
if ($LocalInstallerPath) { $packageParams.LocalInstallerPath = $LocalInstallerPath }
if ($OverrideConfigPath) { $packageParams.OverrideConfigPath = $OverrideConfigPath }

& "$PSScriptRoot\New-IntunePackage.ps1" @packageParams
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$matches = Get-ChildItem -Path $OutputRoot -Filter metadata.json -Recurse | Sort-Object LastWriteTime -Descending
if (-not $matches) { throw 'No metadata.json file was found after packaging.' }
$metadataPath = $matches[0].FullName

$publishParams = @{
    TenantId = $TenantId
    MetadataPath = $metadataPath
    InstallContext = 'system'
    NewAppAssignment = $NewAppAssignment
}
if ($TargetGroupName) { $publishParams.TargetGroupName = $TargetGroupName }
if ($UseIntuneGraphAuth) { $publishParams.UseIntuneGraphAuth = $true }
if ($IntuneClientId) { $publishParams.IntuneClientId = $IntuneClientId }
if ($IntuneModuleVersion) { $publishParams.IntuneModuleVersion = $IntuneModuleVersion }
if ($IntuneDeviceCode) { $publishParams.IntuneDeviceCode = $true }
if ($OverrideConfigPath) { $publishParams.OverrideConfigPath = $OverrideConfigPath }
if ($CustomDisplayName) { $publishParams.CustomDisplayName = $CustomDisplayName }
if ($CustomDescription) { $publishParams.CustomDescription = $CustomDescription }
if ($CustomPublisher) { $publishParams.CustomPublisher = $CustomPublisher }
if ($SupersedeAppId) { $publishParams.SupersedeAppId = $SupersedeAppId; $publishParams.SupersedenceType = $SupersedenceType }
if ($UseAzCopy) { $publishParams.UseAzCopy = $true }
if ($WhatIf) { $publishParams.WhatIf = $true }

& "$PSScriptRoot\Publish-IntuneWin32App.ps1" @publishParams
