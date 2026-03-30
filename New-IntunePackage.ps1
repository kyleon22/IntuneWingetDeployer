[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$AppName,

    [string]$PackageId,

    [string]$OutputRoot = ".\output",

    [string]$WingetSource = "winget",

    [ValidateSet('x64','x86','arm64','neutral','any')]
    [string]$Architecture = 'x64',

    [ValidateSet('new','update')]
    [string]$DeploymentType,

    [string]$IntuneWinAppUtilPath = ".\IntuneWinAppUtil.exe",

    [string]$LocalInstallerPath,

    [string]$OverrideConfigPath,

    [string]$LogPath,

    [switch]$SkipModuleInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $LogPath) {
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $LogPath = Join-Path $PWD "new-intune-package-$timestamp.log"
}

$script:Session = [ordered]@{ LogPath = $LogPath }

function Get-SafeString { param([AllowNull()][object]$Value) if ($null -eq $Value) { return '' }; return [string]$Value }
function Write-Log { param([string]$Message,[ValidateSet('INFO','WARN','ERROR','SUCCESS')][string]$Level='INFO'); $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'; $line = "[$timestamp] [$Level] $Message"; Add-Content -LiteralPath $script:Session.LogPath -Value $line; $color = switch ($Level) { 'WARN' {'Yellow'} 'ERROR' {'Red'} 'SUCCESS' {'Green'} default {'Gray'} }; Write-Host $line -ForegroundColor $color }
function Write-Section { param([string]$Message) Write-Host "`n=== $Message ===" -ForegroundColor Cyan; Write-Log -Message "=== $Message ===" }
function Stop-WithError { param([string]$Message) Write-Log -Message $Message -Level ERROR; throw $Message }
function Test-CommandExists { param([string]$Name) return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }
function Read-OverrideConfig { param([string]$Path) if (-not $Path) { return @{} }; if (-not (Test-Path -LiteralPath $Path)) { Stop-WithError "Override config file not found: $Path" }; Write-Section 'Loading override config'; return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable) }
function Get-OverrideValue { param([hashtable]$Config,[string]$PackageId,[string]$PropertyName) if (-not $Config -or -not $Config.ContainsKey($PackageId)) { return $null }; $packageConfig = $Config[$PackageId]; if ($packageConfig -is [hashtable] -and $packageConfig.ContainsKey($PropertyName)) { return $packageConfig[$PropertyName] }; return $null }
function Ensure-Prereqs { Write-Section 'Checking prerequisites'; foreach ($cmd in @('winget')) { if (-not (Test-CommandExists -Name $cmd)) { Stop-WithError "Required command '$cmd' was not found in PATH." } }; if (-not (Test-Path -LiteralPath $IntuneWinAppUtilPath)) { Stop-WithError "IntuneWinAppUtil.exe not found at '$IntuneWinAppUtilPath'." }; foreach ($module in @('Microsoft.Graph.Authentication','Microsoft.Graph.Groups','IntuneWin32App')) { if (-not (Get-Module -ListAvailable -Name $module)) { if ($SkipModuleInstall) { Stop-WithError "Required PowerShell module missing: $module" }; Write-Log -Message "Installing PowerShell module: $module" -Level WARN; Install-Module -Name $module -Scope CurrentUser -Force -AllowClobber } } }
function Search-WingetPackages { param([string]$Query,[string]$Source='winget'); Write-Section "Searching winget for '$Query'"; $lines = & winget search --source $Source --query $Query --accept-source-agreements 2>$null; if (-not $lines) { Stop-WithError 'No results returned from winget.' }; $dataLines = $lines | Where-Object { $_ -and $_ -notmatch '^Name\s+Id\s+Version' -and $_ -notmatch '^[- ]+$' -and $_ -notmatch '^No package found matching input criteria\.' }; $results = foreach ($line in $dataLines) { if ($line -match '^(?<Name>.+?)\s{2,}(?<Id>[A-Za-z0-9_.-]+)\s{2,}(?<Version>\S+)') { [pscustomobject]@{ Name=$matches.Name.Trim(); Id=$matches.Id.Trim(); Version=$matches.Version.Trim(); Raw=$line } } }; $unique = $results | Group-Object Id | ForEach-Object { $_.Group | Select-Object -First 1 }; if (-not $unique) { Stop-WithError "No parseable winget package matches found for '$Query'." }; return @($unique) }
function Select-WingetPackage { param([array]$Packages) if ($Packages.Count -eq 1) { Write-Log -Message "Only one winget package match found: $($Packages[0].Id)"; return $Packages[0] }; Write-Host "`nMatching packages:" -ForegroundColor Green; for ($i=0; $i -lt $Packages.Count; $i++) { $pkg=$Packages[$i]; Write-Host ("[{0}] {1} ({2}) - {3}" -f ($i+1),$pkg.Name,$pkg.Id,$pkg.Version) }; while ($true) { $selection=Read-Host 'Select package number'; $parsed=0; if ([int]::TryParse($selection,[ref]$parsed) -and $parsed -ge 1 -and $parsed -le $Packages.Count) { return $Packages[$parsed-1] }; Write-Host 'Invalid selection. Try again.' -ForegroundColor Yellow } }
function Get-WingetManifestUrls { param([string]$PackageId) $segments=$PackageId.Split('.'); if ($segments.Count -lt 2) { Stop-WithError "Unexpected winget package id format: $PackageId" }; $firstChar=$segments[0].Substring(0,1).ToLowerInvariant(); $path=($segments -join '/'); $base="https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/$firstChar/$path"; $headers=@{'User-Agent'='cyberbtee-intune-winget-deployer'}; $versions=Invoke-RestMethod -Uri $base -Headers $headers -Method Get; if (-not $versions) { Stop-WithError "Unable to enumerate winget manifests for $PackageId" }; $latestVersionFolder=($versions|Sort-Object name -Descending|Select-Object -First 1).name; $files=Invoke-RestMethod -Uri "$base/$latestVersionFolder" -Headers $headers -Method Get; $installerManifest=$files|Where-Object{$_.name -like '*.installer.yaml'}|Select-Object -First 1; if (-not $installerManifest) { Stop-WithError "No installer manifest found for $PackageId" }; $defaultLocale=$files|Where-Object{$_.name -like '*.defaultLocale.yaml'}|Select-Object -First 1; $localeManifest=$files|Where-Object{$_.name -like '*.locale.en-US.yaml'}|Select-Object -First 1; if (-not $localeManifest) { $localeManifest=$files|Where-Object{$_.name -like '*.locale*.yaml'}|Select-Object -First 1 }; [pscustomobject]@{ VersionFolder=$latestVersionFolder; InstallerManifestUrl=$installerManifest.download_url; DefaultLocaleUrl=$defaultLocale.download_url; LocaleManifestUrl=$localeManifest.download_url } }
function Parse-SimpleYamlValue { param([string]$Content,[string]$Key) $pattern="(?m)^$([regex]::Escape($Key))\s*:\s*(.+)$"; $match=[regex]::Match($Content,$pattern); if ($match.Success) { return $match.Groups[1].Value.Trim().Trim("'").Trim('"') }; return $null }
function Get-YamlListBlocks { param([string]$Content,[string]$RootKey) $lines=$Content -split "`r?`n"; $items=@(); $inBlock=$false; $current=[ordered]@{}; foreach ($line in $lines) { if (-not $inBlock) { if ($line -match "^$([regex]::Escape($RootKey))\s*:\s*$") { $inBlock=$true }; continue }; if ($line -match '^[A-Za-z].*:\s*$') { break }; if ($line -match '^\s*-\s*(.+)$') { if ($current.Count -gt 0) { $items += [pscustomobject]$current; $current=[ordered]@{} }; $rest=$matches[1]; if ($rest -match '^(?<key>[A-Za-z0-9]+)\s*:\s*(?<value>.+)$') { $current[$matches.key]=$matches.value.Trim().Trim("'").Trim('"') }; continue }; if ($line -match '^\s{2,}(?<key>[A-Za-z0-9]+)\s*:\s*(?<value>.+)$') { $current[$matches.key]=$matches.value.Trim().Trim("'").Trim('"') } }; if ($current.Count -gt 0) { $items += [pscustomobject]$current }; return @($items) }
function Select-InstallerEntry { param([array]$Installers,[string]$PreferredArchitecture) if (-not $Installers -or $Installers.Count -eq 0) { Stop-WithError 'No installer entries found in manifest.' }; $ordered=@(); if ($PreferredArchitecture -and $PreferredArchitecture -ne 'any') { $ordered += $Installers|Where-Object{$_.Architecture -eq $PreferredArchitecture} }; $ordered += $Installers|Where-Object{$_.Architecture -eq 'neutral'}; $ordered += $Installers|Where-Object{$_.Architecture -eq 'x64'}; $ordered += $Installers|Where-Object{$_.Architecture -eq 'x86'}; $ordered += $Installers|Where-Object{$_.Architecture -eq 'arm64'}; $ordered += $Installers; return $ordered | Group-Object InstallerUrl | ForEach-Object { $_.Group | Select-Object -First 1 } | Select-Object -First 1 }
function Get-PackageMetadataFromWingetManifest { param([string]$PackageId,[ValidateSet('x64','x86','arm64','neutral','any')][string]$PreferredArchitecture='x64'); Write-Section 'Resolving installer metadata'; try { $showLines=& winget show --id $PackageId --source winget --accept-source-agreements 2>$null; if ($showLines) { $joined=($showLines -join "`n"); $installerUrl=if ($joined -match '(?m)^\s*Installer Url:\s*(.+)$') { $matches[1].Trim() } else { $null }; $installerType=if ($joined -match '(?m)^\s*Installer Type:\s*(.+)$') { $matches[1].Trim() } else { $null }; $publisher=if ($joined -match '(?m)^Publisher:\s*(.+)$') { $matches[1].Trim() } else { $null }; $packageName=if ($joined -match '(?m)^Found\s+(.+?)\s+\[') { $matches[1].Trim() } else { $PackageId }; $description=if ($joined -match '(?m)^Description:\s*(.+)$') { $matches[1].Trim() } else { $null }; $version=if ($joined -match '(?m)^Version:\s*(.+)$') { $matches[1].Trim() } else { $null }; if ($installerUrl) { return [pscustomobject]@{ InstallerUrl=$installerUrl; SilentCommand=$null; InstallerType=$installerType; ProductCode=$null; Scope=$null; Publisher=$publisher; PackageName=$packageName; Description=$description; VersionFolder=$version; Architecture=$PreferredArchitecture; PackageVersion=$version } } } } catch { Write-Log -Message "winget show lookup failed for $PackageId, falling back to GitHub manifest lookup" -Level WARN }; Write-Log -Message 'Falling back to GitHub manifest lookup' -Level WARN; $manifestUrls=Get-WingetManifestUrls -PackageId $PackageId; $headers=@{'User-Agent'='cyberbtee-intune-winget-deployer'}; $installerYaml=Invoke-RestMethod -Uri $manifestUrls.InstallerManifestUrl -Headers $headers; $localeYaml=$null; if ($manifestUrls.DefaultLocaleUrl) { $localeYaml=Invoke-RestMethod -Uri $manifestUrls.DefaultLocaleUrl -Headers $headers } elseif ($manifestUrls.LocaleManifestUrl) { $localeYaml=Invoke-RestMethod -Uri $manifestUrls.LocaleManifestUrl -Headers $headers }; $installerEntries=Get-YamlListBlocks -Content $installerYaml -RootKey 'Installers'; $selectedInstaller=Select-InstallerEntry -Installers $installerEntries -PreferredArchitecture $PreferredArchitecture; $installerUrl=if ($selectedInstaller.InstallerUrl) { $selectedInstaller.InstallerUrl } else { Parse-SimpleYamlValue -Content $installerYaml -Key 'InstallerUrl' }; $silentSwitch=if ($selectedInstaller.InstallerSwitchesSilent) { $selectedInstaller.InstallerSwitchesSilent } else { $null }; $silentWithProgress=if ($selectedInstaller.InstallerSwitchesSilentWithProgress) { $selectedInstaller.InstallerSwitchesSilentWithProgress } else { $null }; if (-not $silentSwitch) { $silentSwitch=Parse-SimpleYamlValue -Content $installerYaml -Key 'Silent' }; if (-not $silentWithProgress) { $silentWithProgress=Parse-SimpleYamlValue -Content $installerYaml -Key 'SilentWithProgress' }; $installerType=if ($selectedInstaller.InstallerType) { $selectedInstaller.InstallerType } else { Parse-SimpleYamlValue -Content $installerYaml -Key 'InstallerType' }; $productCode=if ($selectedInstaller.ProductCode) { $selectedInstaller.ProductCode } else { Parse-SimpleYamlValue -Content $installerYaml -Key 'ProductCode' }; $scope=if ($selectedInstaller.Scope) { $selectedInstaller.Scope } else { Parse-SimpleYamlValue -Content $installerYaml -Key 'Scope' }; $packageVersion=if ($selectedInstaller.InstallerVersion) { $selectedInstaller.InstallerVersion } else { Parse-SimpleYamlValue -Content $installerYaml -Key 'PackageVersion' }; $publisher=if ($localeYaml) { Parse-SimpleYamlValue -Content $localeYaml -Key 'Publisher' } else { $null }; $packageName=if ($localeYaml) { Parse-SimpleYamlValue -Content $localeYaml -Key 'PackageName' } else { $null }; $shortDescription=if ($localeYaml) { Parse-SimpleYamlValue -Content $localeYaml -Key 'ShortDescription' } else { $null }; if (-not $installerUrl) { Stop-WithError "Unable to find InstallerUrl in winget metadata for $PackageId" }; [pscustomobject]@{ InstallerUrl=$installerUrl; SilentCommand=if ($silentWithProgress) { $silentWithProgress } elseif ($silentSwitch) { $silentSwitch } else { $null }; InstallerType=$installerType; ProductCode=$productCode; Scope=$scope; Publisher=$publisher; PackageName=$packageName; Description=$shortDescription; VersionFolder=$manifestUrls.VersionFolder; Architecture=if ($selectedInstaller.Architecture) { $selectedInstaller.Architecture } else { $PreferredArchitecture }; PackageVersion=$packageVersion } }
function New-WorkingLayout { param([pscustomobject]$Package,[string]$Root) $safeName=($Package.Id -replace '[^A-Za-z0-9_.-]','_'); $base=Join-Path $Root $safeName; $downloadDir=Join-Path $base 'download'; $stagingDir=Join-Path $base 'staging'; $outputDir=Join-Path $base 'intunewin'; foreach ($dir in @($downloadDir,$stagingDir,$outputDir)) { $null=New-Item -ItemType Directory -Path $dir -Force }; [pscustomobject]@{ BaseDir=$base; DownloadDir=$downloadDir; StagingDir=$stagingDir; OutputDir=$outputDir; InstallScriptPath=Join-Path $stagingDir 'Install.ps1'; DetectScriptPath=Join-Path $stagingDir 'Detect.ps1'; RequirementScriptPath=Join-Path $stagingDir 'Requirement.ps1'; MetadataPath=Join-Path $base 'metadata.json' } }
function Save-Installer { param([string]$Url,[string]$Destination,[string]$SourcePath) if ($SourcePath) { if (-not (Test-Path -LiteralPath $SourcePath)) { Stop-WithError "Local installer path not found: $SourcePath" }; Write-Section 'Using local installer'; Copy-Item -LiteralPath $SourcePath -Destination $Destination -Force; Write-Log -Message "Installer copied from local path to $Destination" -Level SUCCESS; return }; if (-not $Url) { Stop-WithError 'No installer URL or local installer path was provided.' }; Write-Section 'Downloading installer'; Write-Log -Message "Installer URL: $Url"; try { Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' } catch { Write-Log -Message "Invoke-WebRequest failed: $($_.Exception.Message). Retrying with WebClient." -Level WARN; $wc = New-Object System.Net.WebClient; $wc.Headers.Add('User-Agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64)'); $wc.DownloadFile($Url, $Destination) }; Write-Log -Message "Installer downloaded to $Destination" -Level SUCCESS }
function Get-DefaultSilentArgs { param([string]$InstallerType) switch ((Get-SafeString $InstallerType).ToLowerInvariant()) { 'msi' {'/qn /norestart'} 'wix' {'/qn /norestart'} 'burn' {'/quiet /norestart'} 'inno' {'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'} 'nullsoft' {'/S'} default {'/quiet /norestart'} } }
function New-InstallScript { param([string]$Path,[string]$InstallerFileName,[pscustomobject]$Metadata,[hashtable]$Overrides) $overrideSilent=Get-OverrideValue -Config $Overrides -PackageId $script:Session.PackageId -PropertyName 'SilentCommand'; $silentArg=if ($overrideSilent) { $overrideSilent } elseif ($Metadata.SilentCommand) { $Metadata.SilentCommand } else { Get-DefaultSilentArgs -InstallerType $Metadata.InstallerType }; $customInstallCommand=Get-OverrideValue -Config $Overrides -PackageId $script:Session.PackageId -PropertyName 'InstallCommand'; if ($customInstallCommand) { Set-Content -LiteralPath $Path -Value $customInstallCommand -Encoding UTF8; return }; $isMsi=$InstallerFileName -like '*.msi' -or ((Get-SafeString $Metadata.InstallerType).ToLowerInvariant() -in @('msi','wix')); $script = if ($isMsi) { @"
`$msi = Join-Path `$PSScriptRoot '$InstallerFileName'
`$args = "/i `"`$msi`" $silentArg"
`$p = Start-Process msiexec.exe -ArgumentList `$args -Wait -PassThru -NoNewWindow
if (`$p.ExitCode -notin 0,3010,1641) { exit `$p.ExitCode }
exit 0
"@ } else { @"
`$installer = Join-Path `$PSScriptRoot '$InstallerFileName'
`$p = Start-Process -FilePath `$installer -ArgumentList '$silentArg' -Wait -PassThru -NoNewWindow
if (`$p.ExitCode -notin 0,3010,1641) { exit `$p.ExitCode }
exit 0
"@ }; Set-Content -LiteralPath $Path -Value $script -Encoding UTF8 }
function New-DetectionScript { param([string]$Path,[pscustomobject]$Package,[pscustomobject]$Metadata,[hashtable]$Overrides) $customScript=Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'DetectionScript'; if ($customScript) { Set-Content -LiteralPath $Path -Value $customScript -Encoding UTF8; return }; $displayName=(Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'DisplayNameDetect'); if (-not $displayName) { $displayName=if ($Metadata.PackageName) { $Metadata.PackageName } else { $Package.Name } }; $productCode=$Metadata.ProductCode; $script=@"
`$targets = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
`$apps = foreach (`$path in `$targets) { Get-ItemProperty -Path `$path -ErrorAction SilentlyContinue }
`$match = `$apps | Where-Object { (`$_.DisplayName -like '*$displayName*')$(if ($productCode) { " -or (`$_.PSChildName -eq '$productCode') -or (`$_.ProductCode -eq '$productCode')" }) } | Select-Object -First 1
if (`$match) { Write-Output 'Detected'; exit 0 }
exit 1
"@; Set-Content -LiteralPath $Path -Value $script -Encoding UTF8 }
function New-RequirementScript { param([string]$Path,[pscustomobject]$Package,[pscustomobject]$Metadata,[bool]$IsUpdate,[hashtable]$Overrides) $customScript=Get-OverrideValue -Config $Overrides -PackageId $Package.Id -PropertyName 'RequirementScript'; if ($customScript) { Set-Content -LiteralPath $Path -Value $customScript -Encoding UTF8; return }; if (-not $IsUpdate) { Set-Content -LiteralPath $Path -Value "Write-Output 'True'`nexit 0`n" -Encoding UTF8; return }; $displayName=if ($Metadata.PackageName) { $Metadata.PackageName } else { $Package.Name }; $productCode=$Metadata.ProductCode; $targetVersion=Get-SafeString $Metadata.PackageVersion; $script=@"
`$targets = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
`$apps = foreach (`$path in `$targets) { Get-ItemProperty -Path `$path -ErrorAction SilentlyContinue }
`$match = `$apps | Where-Object { (`$_.DisplayName -like '*$displayName*')$(if ($productCode) { " -or (`$_.PSChildName -eq '$productCode') -or (`$_.ProductCode -eq '$productCode')" }) } | Select-Object -First 1
if (-not `$match) {
    Write-Output 'False'
    exit 0
}
if (-not '$targetVersion') {
    Write-Output 'True'
    exit 0
}
`$installedVersionRaw = [string]`$match.DisplayVersion
if (-not `$installedVersionRaw) {
    Write-Output 'True'
    exit 0
}
try {
    `$installedVersion = [version]((`$installedVersionRaw -replace '[^0-9\.]',''))
    `$targetVersionObj = [version]((('$targetVersion') -replace '[^0-9\.]',''))
    if (`$installedVersion -lt `$targetVersionObj) {
        Write-Output 'True'
    }
    else {
        Write-Output 'False'
    }
}
catch {
    if (`$installedVersionRaw -ne '$targetVersion') {
        Write-Output 'True'
    }
    else {
        Write-Output 'False'
    }
}
exit 0
"@; Set-Content -LiteralPath $Path -Value $script -Encoding UTF8 }
function Invoke-IntuneWrap { param([pscustomobject]$Layout,[string]$InstallerFileName) Write-Section 'Wrapping as .intunewin'; & $IntuneWinAppUtilPath -c $Layout.StagingDir -s $InstallerFileName -o $Layout.OutputDir -q | Out-Null; $intuneWin=Get-ChildItem -Path $Layout.OutputDir -Filter '*.intunewin' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if (-not $intuneWin) { Stop-WithError 'Intune wrapping failed; no .intunewin file was produced.' }; Write-Log -Message "Wrapped package created: $($intuneWin.FullName)" -Level SUCCESS; return $intuneWin.FullName }
function Save-MetadataSnapshot { param([string]$Path,[pscustomobject]$Package,[pscustomobject]$Metadata,[bool]$IsUpdate,[string]$InstallerPath,[string]$IntuneWinPath,[pscustomobject]$Layout) $snapshot=[ordered]@{ Timestamp=(Get-Date).ToString('s'); Package=$Package; Metadata=$Metadata; DeploymentType=if ($IsUpdate) {'update'} else {'new'}; InstallerPath=$InstallerPath; IntuneWinPath=$IntuneWinPath; Architecture=$Metadata.Architecture; LogPath=$script:Session.LogPath; StagingDir=$Layout.StagingDir; DetectScriptPath=$Layout.DetectScriptPath; RequirementScriptPath=$Layout.RequirementScriptPath; InstallScriptPath=$Layout.InstallScriptPath; BaseDir=$Layout.BaseDir }; $snapshot | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8 }

try {
    New-Item -ItemType File -Path $script:Session.LogPath -Force | Out-Null
    Write-Section 'Starting package build pipeline'
    $overrides = Read-OverrideConfig -Path $OverrideConfigPath
    Ensure-Prereqs
    if ($PackageId) {
        Write-Section "Using explicit winget package id '$PackageId'"
        $selectedPackage = [pscustomobject]@{ Name = $AppName; Id = $PackageId; Version = '' }
    }
    else {
        $matches = @(Search-WingetPackages -Query $AppName -Source $WingetSource)
        $selectedPackage = Select-WingetPackage -Packages $matches
    }
    $script:Session.PackageId = $selectedPackage.Id
    Write-Log -Message "Selected package: $($selectedPackage.Name) [$($selectedPackage.Id)]" -Level SUCCESS
    if (-not $DeploymentType) { $DeploymentType = Read-Host 'Is this a new app or an update? [new/update]' }
    $isUpdate = $DeploymentType -eq 'update'
    $metadata = Get-PackageMetadataFromWingetManifest -PackageId $selectedPackage.Id -PreferredArchitecture $Architecture
    $layout = New-WorkingLayout -Package $selectedPackage -Root $OutputRoot
    $installerExtension = [System.IO.Path]::GetExtension(($metadata.InstallerUrl.Split('?')[0]))
    if (-not $installerExtension) { $installerExtension = if (((Get-SafeString $metadata.InstallerType).ToLowerInvariant() -in @('msi','wix'))) { '.msi' } else { '.exe' } }
    if ($LocalInstallerPath) { $installerExtension = [System.IO.Path]::GetExtension($LocalInstallerPath); if (-not $installerExtension) { $installerExtension = if (((Get-SafeString $metadata.InstallerType).ToLowerInvariant() -in @('msi','wix'))) { '.msi' } else { '.exe' } } }
    $installerPath = Join-Path $layout.DownloadDir ("installer$installerExtension")
    Save-Installer -Url $metadata.InstallerUrl -Destination $installerPath -SourcePath $LocalInstallerPath
    Copy-Item -LiteralPath $installerPath -Destination (Join-Path $layout.StagingDir (Split-Path $installerPath -Leaf)) -Force
    New-InstallScript -Path $layout.InstallScriptPath -InstallerFileName (Split-Path $installerPath -Leaf) -Metadata $metadata -Overrides $overrides
    New-DetectionScript -Path $layout.DetectScriptPath -Package $selectedPackage -Metadata $metadata -Overrides $overrides
    New-RequirementScript -Path $layout.RequirementScriptPath -Package $selectedPackage -Metadata $metadata -IsUpdate:$isUpdate -Overrides $overrides
    $intuneWinPath = Invoke-IntuneWrap -Layout $layout -InstallerFileName 'Install.ps1'
    Save-MetadataSnapshot -Path $layout.MetadataPath -Package $selectedPackage -Metadata $metadata -IsUpdate:$isUpdate -InstallerPath $installerPath -IntuneWinPath $intuneWinPath -Layout $layout
    Write-Section 'Packaging completed'
    Write-Host "Package root: $($layout.BaseDir)" -ForegroundColor Green
    Write-Host "IntuneWin package: $intuneWinPath" -ForegroundColor Green
    Write-Host "Metadata file: $($layout.MetadataPath)" -ForegroundColor Green
}
catch {
    Write-Log -Message $_.Exception.Message -Level ERROR
    throw
}
