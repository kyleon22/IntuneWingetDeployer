[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$TenantId,

    [string]$DisplayName = 'IntuneWin32AppAutomation',

    [string]$RedirectUri = 'http://localhost',

    [switch]$GrantAdminConsent
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Section {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Ensure-Module {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Module -ListAvailable -Name $Name)) {
        Install-Module -Name $Name -Scope CurrentUser -Force -AllowClobber
    }
}

Write-Section 'Checking prerequisites'
Ensure-Module -Name Microsoft.Graph.Authentication
Ensure-Module -Name Microsoft.Graph.Applications

Import-Module Microsoft.Graph.Authentication
Import-Module Microsoft.Graph.Applications

Write-Section 'Connecting to Microsoft Graph'
Connect-MgGraph -TenantId $TenantId -Scopes @(
    'Application.ReadWrite.All',
    'AppRoleAssignment.ReadWrite.All',
    'DelegatedPermissionGrant.ReadWrite.All',
    'Directory.Read.All'
) | Out-Null

$graphSp = Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'"
if (-not $graphSp) {
    throw 'Unable to resolve Microsoft Graph service principal.'
}

$reusedExisting = $false
$existingApp = Get-MgApplication -Filter "displayName eq '$($DisplayName.Replace("'","''"))'" | Select-Object -First 1
if ($existingApp) {
    Write-Host "App registration already exists: $($existingApp.DisplayName) [$($existingApp.AppId)]" -ForegroundColor Yellow
    $app = $existingApp
    $reusedExisting = $true
}
else {
    Write-Section 'Creating app registration'
    $app = New-MgApplication -DisplayName $DisplayName -SignInAudience 'AzureADMyOrg' -PublicClient @{ RedirectUris = @($RedirectUri) }
    Write-Host "Created app registration: $($app.DisplayName) [$($app.AppId)]" -ForegroundColor Green
}

$sp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" | Select-Object -First 1
if (-not $sp) {
    Write-Section 'Creating service principal'
    $sp = New-MgServicePrincipal -AppId $app.AppId
}

$requiredScopes = @(
    'DeviceManagementApps.ReadWrite.All',
    'Group.Read.All',
    'offline_access'
)

Write-Section 'Assigning minimum delegated Microsoft Graph permissions'
$resourceAccess = @()
foreach ($scope in $requiredScopes) {
    $permission = $graphSp.Oauth2PermissionScopes | Where-Object { $_.Value -eq $scope } | Select-Object -First 1
    if (-not $permission) {
        throw "Unable to resolve Microsoft Graph delegated scope: $scope"
    }

    $resourceAccess += @{
        Id = $permission.Id
        Type = 'Scope'
    }
}

Update-MgApplication -ApplicationId $app.Id -RequiredResourceAccess @(
    @{
        ResourceAppId = '00000003-0000-0000-c000-000000000000'
        ResourceAccess = $resourceAccess
    }
)

Write-Host 'Configured delegated scopes:' -ForegroundColor Green
$requiredScopes | ForEach-Object { Write-Host "- $_" }

if ($GrantAdminConsent) {
    Write-Section 'Granting tenant-wide admin consent'
    $existingGrant = Get-MgOauth2PermissionGrant -Filter "clientId eq '$($sp.Id)' and resourceId eq '$($graphSp.Id)' and consentType eq 'AllPrincipals'" | Select-Object -First 1
    $scopeString = ($requiredScopes -join ' ')

    if ($existingGrant) {
        Update-MgOauth2PermissionGrant -OAuth2PermissionGrantId $existingGrant.Id -Scope $scopeString | Out-Null
        Write-Host 'Updated existing admin consent grant.' -ForegroundColor Green
    }
    else {
        New-MgOauth2PermissionGrant -ClientId $sp.Id -ConsentType 'AllPrincipals' -ResourceId $graphSp.Id -Scope $scopeString | Out-Null
        Write-Host 'Granted admin consent.' -ForegroundColor Green
    }
}
else {
    Write-Host 'Admin consent not granted automatically. Grant it in Entra admin center or rerun with -GrantAdminConsent.' -ForegroundColor Yellow
}

Write-Section 'Completed'
Write-Host "Client ID: $($app.AppId)" -ForegroundColor Green
Write-Host "Tenant ID: $TenantId" -ForegroundColor Green
Write-Host "Redirect URI: $RedirectUri" -ForegroundColor Green
Write-Host "Reused Existing App Registration: $reusedExisting" -ForegroundColor Green
Write-Host ''
Write-Host 'Use this with the deployer script:' -ForegroundColor Cyan
Write-Host ".\intune-winget-deployer.ps1 -AppName \"foxit pdf\" -TenantId \"$TenantId\" -DeploymentType new -NewAppAssignment all -Architecture x64 -IntuneWinAppUtilPath \"C:\Intune Prep Tool\IntuneWinAppUtil.exe\" -LocalInstallerPath \"C:\Users\babat\Downloads\FoxitPDFReader20253_L10N_Setup_x64.exe\" -UseIntuneGraphAuth -IntuneClientId \"$($app.AppId)\""
