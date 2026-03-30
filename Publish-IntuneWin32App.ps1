[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$TenantId,

    [Parameter(Mandatory)]
    [string]$MetadataPath,

    [ValidateSet('system','user')]
    [string]$InstallContext = 'system',

    [ValidateSet('all','group')]
    [string]$NewAppAssignment = 'group',

    [string]$TargetGroupName,

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

    [switch]$UseDelegatedAuth,

    [string]$OverrideConfigPath,

    [string]$LogPath,

    [switch]$SkipModuleInstall,

    [switch]$UseAzCopy,

    [switch]$WhatIf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $LogPath) {
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $LogPath = Join-Path $PWD "publish-intune-win32app-$timestamp.log"
}

$script:Session = [ordered]@{ LogPath = $LogPath; LatestUploadDiagnostics = @() }

function Write-Log { param([string]$Message,[ValidateSet('INFO','WARN','ERROR','SUCCESS')][string]$Level='INFO'); $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'; $line = "[$timestamp] [$Level] $Message"; Add-Content -LiteralPath $script:Session.LogPath -Value $line; $color = switch ($Level) { 'WARN' {'Yellow'} 'ERROR' {'Red'} 'SUCCESS' {'Green'} default {'Gray'} }; Write-Host $line -ForegroundColor $color }
function Write-Section { param([string]$Message) Write-Host "`n=== $Message ===" -ForegroundColor Cyan; Write-Log -Message "=== $Message ===" }
function Stop-WithError { param([string]$Message) Write-Log -Message $Message -Level ERROR; throw $Message }
function Test-CommandExists { param([string]$Name) return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }
function Read-OverrideConfig { param([string]$Path) if (-not $Path) { return @{} }; if (-not (Test-Path -LiteralPath $Path)) { Stop-WithError "Override config file not found: $Path" }; Write-Section 'Loading override config'; return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable) }
function Get-OverrideValue { param([hashtable]$Config,[string]$PackageId,[string]$PropertyName) if (-not $Config -or -not $Config.ContainsKey($PackageId)) { return $null }; $packageConfig=$Config[$PackageId]; if ($packageConfig -is [hashtable] -and $packageConfig.ContainsKey($PropertyName)) { return $packageConfig[$PropertyName] }; return $null }
function Add-UploadDiagnostic { param([string]$Message) $script:Session.LatestUploadDiagnostics += $Message; Write-Log -Message $Message -Level WARN }
function Get-UploadDiagnosticSummary { $lines=@(); if ($UseAzCopy) { $lines += 'Upload mode: AzCopy-backed transfer.' } else { $lines += 'Upload mode: native IntuneWin32App transfer.' }; $lines += 'Likely causes: unstable network, proxy / SSL inspection, VPN, firewall egress filtering, or Microsoft service-side upload disruption.'; $lines += "Review the log file for earlier warnings: $($script:Session.LogPath)"; if ($script:Session.LatestUploadDiagnostics.Count -gt 0) { $lines += 'Recent upload diagnostics:'; $lines += ($script:Session.LatestUploadDiagnostics | Select-Object -Last 6) }; return ($lines -join ' ') }
function Ensure-Prereqs { Write-Section 'Checking prerequisites'; foreach ($module in @('Microsoft.Graph.Authentication','Microsoft.Graph.Groups','IntuneWin32App')) { if (-not (Get-Module -ListAvailable -Name $module)) { if ($SkipModuleInstall) { Stop-WithError "Required PowerShell module missing: $module" }; Write-Log -Message "Installing PowerShell module: $module" -Level WARN; Install-Module -Name $module -Scope CurrentUser -Force -AllowClobber } }; if ($IntuneModuleVersion) { $specific = Get-Module -ListAvailable -Name IntuneWin32App | Where-Object { $_.Version -eq [version]$IntuneModuleVersion }; if (-not $specific) { Stop-WithError "Requested IntuneWin32App version '$IntuneModuleVersion' is not installed." } } }
function Import-PreferredIntuneModule { if ($IntuneModuleVersion) { Write-Log -Message "Importing IntuneWin32App version $IntuneModuleVersion"; Import-Module IntuneWin32App -RequiredVersion $IntuneModuleVersion -Force -ErrorAction Stop } else { Import-Module IntuneWin32App -ErrorAction Stop }; $loaded = Get-Module IntuneWin32App | Select-Object -First 1; if ($loaded) { Write-Log -Message "Loaded IntuneWin32App module version $($loaded.Version) from $($loaded.Path)" } }
function Connect-IntuneGraph { if ($UseIntuneGraphAuth -and $IntuneClientId) { Write-Section 'Connecting with IntuneWin32App auth (app registration)'; Remove-Module IntuneWin32App -ErrorAction SilentlyContinue; Import-PreferredIntuneModule; $authParams=@{ TenantID=$TenantId; ClientID=$IntuneClientId }; if ($IntuneDeviceCode) { $authParams.DeviceCode = $true } else { $authParams.Interactive = $true }; Connect-MSIntuneGraph @authParams | Out-Null; return }; Write-Section 'Connecting with delegated auth (Microsoft Graph)'; $rawToken = $null; if ($env:GRAPH_ACCESS_TOKEN) { Write-Log -Message 'Using pre-acquired access token.' -Level SUCCESS; $rawToken = $env:GRAPH_ACCESS_TOKEN; $env:GRAPH_ACCESS_TOKEN = $null; $secureToken = ConvertTo-SecureString $rawToken -AsPlainText -Force; Connect-MgGraph -AccessToken $secureToken -NoWelcome | Out-Null } else { $scopes = @('DeviceManagementApps.ReadWrite.All','DeviceManagementConfiguration.ReadWrite.All','Group.Read.All'); if ($IntuneDeviceCode) { Connect-MgGraph -TenantId $TenantId -Scopes $scopes -UseDeviceCode -NoWelcome | Out-Null } else { try { [Microsoft.Graph.PowerShell.Authentication.GraphSession]::Instance.GraphOption.EnableWAMForMSGraph = $false } catch { }; try { Connect-MgGraph -TenantId $TenantId -Scopes $scopes -NoWelcome | Out-Null } catch { Write-Log -Message "Interactive auth failed: $($_.Exception.Message). Trying device code." -Level WARN; Connect-MgGraph -TenantId $TenantId -Scopes $scopes -UseDeviceCode -NoWelcome | Out-Null } } }; Remove-Module IntuneWin32App -ErrorAction SilentlyContinue; Import-PreferredIntuneModule; if ($rawToken) { $tokenObj = [PSCustomObject]@{ access_token = $rawToken; AccessToken = $rawToken; ExpiresOn = [DateTimeOffset]::UtcNow.AddHours(1); Scopes = @('DeviceManagementApps.ReadWrite.All','DeviceManagementConfiguration.ReadWrite.All','Group.Read.All'); RefreshToken = $null }; $Global:AccessToken = $tokenObj; $Global:AccessTokenTenantID = $TenantId; $Global:AuthenticationHeader = @{ 'Content-Type' = 'application/json'; 'Authorization' = "Bearer $rawToken"; 'ExpiresOn' = $tokenObj.ExpiresOn.UtcDateTime }; Write-Log -Message 'IntuneWin32App module auth state configured.' -Level SUCCESS } }
function Resolve-Group { param([string]$GroupName) if (-not $GroupName) { $GroupName = Read-Host 'Enter part of the Entra ID group display name' }; $safeGroupName = $GroupName.Replace("'","''"); $groups = Get-MgGroup -Filter "startswith(displayName,'$safeGroupName')" -ConsistencyLevel eventual -CountVariable count -All; if (-not $groups) { Stop-WithError "No Entra ID groups matched '$GroupName'" }; if ($groups.Count -eq 1) { return $groups[0] }; for ($i=0; $i -lt $groups.Count; $i++) { Write-Host ("[{0}] {1} ({2})" -f ($i+1),$groups[$i].DisplayName,$groups[$i].Id) }; while ($true) { $selection = Read-Host 'Select group number'; $parsed=0; if ([int]::TryParse($selection,[ref]$parsed) -and $parsed -ge 1 -and $parsed -le $groups.Count) { return $groups[$parsed-1] } } }
function Wait-ForAppReady { param([string]$AppId,[int]$TimeoutMinutes = 20) Write-Section 'Waiting for Intune app readiness'; $deadline = (Get-Date).AddMinutes($TimeoutMinutes); do { Start-Sleep -Seconds 15; $appState = $null; try { $appState = Get-IntuneWin32App -ID $AppId } catch { Write-Log -Message "Readiness check warning: $($_.Exception.Message)" -Level WARN }; if ($null -eq $appState) { Write-Log -Message 'Readiness check: app lookup returned no object yet.' -Level WARN; continue }; $publishingState = [string]$appState.publishingState; $uploadState = [string]$appState.uploadState; $contentVersion = [string]$appState.committedContentVersion; Write-Log -Message "Readiness check: publishingState=$publishingState uploadState=$uploadState committedContentVersion=$contentVersion"; if ($publishingState -match 'published|ready' -or ($contentVersion -and $contentVersion -ne '0')) { return $appState } } while ((Get-Date) -lt $deadline); Stop-WithError 'Timed out waiting for the Win32 app to become ready in Intune. The app object may exist, but content processing did not complete in time.' }
function Set-AppSupersedence { param([string]$NewAppId,[string]$TargetAppId,[string]$RelationType='Update') if (-not $TargetAppId) { return }; Write-Section 'Configuring supersedence'; try { $sup = New-IntuneWin32AppSupersedence -ID $TargetAppId -SupersedenceType $RelationType; Add-IntuneWin32AppSupersedence -ID $NewAppId -Supersedence $sup | Out-Null; Write-Log -Message "Configured supersedence: new app $NewAppId => $RelationType $TargetAppId" -Level SUCCESS } catch { Stop-WithError "Failed to configure supersedence. $($_.Exception.Message)" } }
function Publish-ToIntune { param([pscustomobject]$Package,[pscustomobject]$Metadata,[string]$IntuneWinPath,[string]$InstallScriptName,[string]$DetectScriptPath,[string]$RequirementScriptPath,[bool]$IsUpdate,[hashtable]$Overrides,[string]$Architecture) Write-Section 'Uploading Win32 app to Intune'; $displayName=(Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'DisplayName'); if (-not $displayName) { $displayName = if ($Metadata.PackageName) { $Metadata.PackageName } else { $Package.Name } }; if ($script:CustomDisplayName) { $displayName = $script:CustomDisplayName }; $publisher=(Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'Publisher'); if (-not $publisher) { $publisher = if ($Metadata.Publisher) { $Metadata.Publisher } else { 'Unknown Publisher' } }; if ($script:CustomPublisher) { $publisher = $script:CustomPublisher }; $description=(Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'Description'); if (-not $description) { $description = if ($Metadata.Description) { $Metadata.Description } else { "Packaged from winget package $($Package.Id)" } }; if ($script:CustomDescription) { $description = $script:CustomDescription }; $uninstallCommand=(Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'UninstallCommand'); if (-not $uninstallCommand) { $uninstallCommand = 'cmd.exe /c exit 0' }; $detectionRule = New-IntuneWin32AppDetectionRuleScript -ScriptFile $DetectScriptPath -EnforceSignatureCheck $false -RunAs32Bit $false; $architectureMap=@{ 'x64'='x64'; 'x86'='x86'; 'arm64'='arm64'; 'neutral'='AllWithARM64'; 'any'='AllWithARM64' }; $baseRequirementRule = New-IntuneWin32AppRequirementRule -Architecture $architectureMap[$Architecture] -MinimumSupportedWindowsRelease 'W10_1607'; $scriptRequirementRule = New-IntuneWin32AppRequirementRuleScript -ScriptFile $RequirementScriptPath -ScriptContext system -BooleanOutputDataType -BooleanComparisonOperator equal -BooleanValue True -RunAs32BitOn64System $false -EnforceSignatureCheck $false; $installCommand = "powershell.exe -ExecutionPolicy Bypass -File $InstallScriptName"; if ($WhatIf) { Write-Log -Message "WhatIf: would create Intune app '$displayName' from $IntuneWinPath"; return [pscustomobject]@{ id='WHATIF'; displayName=$displayName } }; $addParams=@{ FilePath=$IntuneWinPath; DisplayName=$displayName; Description=$description; Publisher=$publisher; InstallExperience=$InstallContext; RestartBehavior='suppress'; DetectionRule=$detectionRule; RequirementRule=$baseRequirementRule; AdditionalRequirementRule=$scriptRequirementRule; InstallCommandLine=$installCommand; UninstallCommandLine=$uninstallCommand }; if ($UseAzCopy) { $addParams.UseAzCopy=$true; Write-Log -Message 'Using AzCopy-backed upload for Intune content transfer.' -Level WARN }; try { $app = Add-IntuneWin32App @addParams 3>&1 4>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.WarningRecord]) { Add-UploadDiagnostic -Message ("Module warning: {0}" -f $_.Message); return }; if ($_ -is [System.Management.Automation.VerboseRecord]) { $verboseMessage=[string]$_.Message; if ($verboseMessage -match 'upload|chunk|azure|blob|azcopy|retry|network|connection|transfer') { Add-UploadDiagnostic -Message ("Module verbose: {0}" -f $verboseMessage) }; return }; $_ } } catch { $summary=Get-UploadDiagnosticSummary; Stop-WithError "Add-IntuneWin32App failed: $($_.Exception.Message) $summary" }; if ($null -eq $app) { $summary=Get-UploadDiagnosticSummary; Stop-WithError "Add-IntuneWin32App returned no object. $summary" }; if (-not ($app.PSObject.Properties.Name -contains 'id') -or [string]::IsNullOrWhiteSpace([string]$app.id)) { $appPreview = try { $app | ConvertTo-Json -Depth 6 -Compress } catch { [string]$app }; $summary=Get-UploadDiagnosticSummary; Stop-WithError "Add-IntuneWin32App did not return a valid app object with an id. Returned: $appPreview $summary" }; $readyApp = Wait-ForAppReady -AppId $app.id
if ($IsUpdate -and $SupersedeAppId) { Set-AppSupersedence -NewAppId $app.id -TargetAppId $SupersedeAppId -RelationType $SupersedenceType }
if ($IsUpdate) { Write-Log -Message 'Update deployment selected: assigning to All Devices with requirement rule limiting install to already-present installs.' -Level WARN; Add-IntuneWin32AppAssignmentAllDevices -Intent required -ID $app.id } else { if ($NewAppAssignment -eq 'group') { $group = Resolve-Group -GroupName $TargetGroupName; Add-IntuneWin32AppAssignmentGroup -Include -Intent required -ID $app.id -GroupID $group.Id -Notification showAll; Write-Log -Message "Assigned '$displayName' to group $($group.DisplayName)" -Level SUCCESS } else { Add-IntuneWin32AppAssignmentAllDevices -Intent required -ID $app.id; Write-Log -Message "Assigned '$displayName' to all devices" -Level SUCCESS } }; return $readyApp }

try {
    New-Item -ItemType File -Path $script:Session.LogPath -Force | Out-Null
    Write-Section 'Starting publish pipeline'
    Ensure-Prereqs
    Connect-IntuneGraph
    if (-not (Test-Path -LiteralPath $MetadataPath)) { Stop-WithError "Metadata path not found: $MetadataPath" }
    $snapshot = Get-Content -LiteralPath $MetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $overrides = Read-OverrideConfig -Path $OverrideConfigPath
    $package = $snapshot.Package
    $metadata = $snapshot.Metadata
    $intuneWinPath = $snapshot.IntuneWinPath
    $detectScriptPath = $snapshot.DetectScriptPath
    $requirementScriptPath = $snapshot.RequirementScriptPath
    $installScriptPath = $snapshot.InstallScriptPath
    $architecture = if ($snapshot.Architecture) { [string]$snapshot.Architecture } elseif ($metadata.Architecture) { [string]$metadata.Architecture } else { 'x64' }
    if (-not (Test-Path -LiteralPath $intuneWinPath)) { Stop-WithError "IntuneWin package not found: $intuneWinPath" }
    foreach ($path in @($detectScriptPath,$requirementScriptPath,$installScriptPath)) { if (-not (Test-Path -LiteralPath $path)) { Stop-WithError "Required staging script not found: $path" } }
    $isUpdate = [string]$snapshot.DeploymentType -eq 'update'
    $app = Publish-ToIntune -Package $package -Metadata $metadata -IntuneWinPath $intuneWinPath -InstallScriptName (Split-Path $installScriptPath -Leaf) -DetectScriptPath $detectScriptPath -RequirementScriptPath $requirementScriptPath -IsUpdate:$isUpdate -Overrides $overrides -Architecture $architecture
    $resolvedDisplayName = if ($app.PSObject.Properties.Name -contains 'displayName') { [string]$app.displayName } elseif ($script:CustomDisplayName) { $script:CustomDisplayName } elseif ($metadata.PackageName) { [string]$metadata.PackageName } elseif ($package.Name) { [string]$package.Name } else { [string]$package.Id }
    $resolvedDescription = if ($app.PSObject.Properties.Name -contains 'description') { [string]$app.description } elseif ($script:CustomDescription) { $script:CustomDescription } elseif ($metadata.Description) { [string]$metadata.Description } else { "Packaged from winget package $($package.Id)" }
    $resolvedPublisher = if ($app.PSObject.Properties.Name -contains 'publisher') { [string]$app.publisher } elseif ($script:CustomPublisher) { $script:CustomPublisher } elseif ($metadata.Publisher) { [string]$metadata.Publisher } else { 'Unknown Publisher' }
    $publishPath = Join-Path (Split-Path $MetadataPath -Parent) 'publish.json'
    [ordered]@{ Timestamp=(Get-Date).ToString('s'); AppId=$app.id; DisplayName=$resolvedDisplayName; Description=$resolvedDescription; Publisher=$resolvedPublisher; TenantId=$TenantId; Assignment=$NewAppAssignment; TargetGroupName=$TargetGroupName; LogPath=$script:Session.LogPath } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $publishPath -Encoding UTF8
    Write-Section 'Publish completed'
    Write-Log -Message "Created Intune app: $resolvedDisplayName ($($app.id))" -Level SUCCESS
    Write-Host "Publish metadata: $publishPath" -ForegroundColor Green
}
catch {
    Write-Log -Message $_.Exception.Message -Level ERROR
    throw
}
