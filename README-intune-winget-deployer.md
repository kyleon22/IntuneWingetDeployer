# Intune Winget Deployer

PowerShell tooling to:
- search winget
- package apps for Intune as `.intunewin`
- publish packaged Win32 apps to Intune
- keep packaging separate from publishing so failed uploads do not force a rebuild

## Files

- `Invoke-IntuneJob.ps1` - convenience wrapper: package, then publish
- `New-IntunePackage.ps1` - package only
- `Publish-IntuneWin32App.ps1` - publish an existing package
- `bootstrap-intune-app-registration.ps1` - creates an Entra app registration (legacy auth)
- `intune-winget-overrides.sample.json` - sample override config
- `intune_desktop_app.py` - desktop GUI app

## Why the split matters

This is the main architecture lesson from IntuneGet-style tooling.

Packaging and publishing should be separate stages.

That means:
- if packaging succeeds, you keep the `.intunewin`
- if upload fails, you rerun **publish only**
- if auth fails, you do not rebuild the package
- if assignment fails, you still keep the package and publish metadata

## Stage 1: package only

Use this to:
- search/select the winget package
- resolve installer metadata
- use a local installer or download one
- build install/detection/requirement scripts
- wrap the content into `.intunewin`
- save a `metadata.json` snapshot for the publish stage

Example:

```powershell
.\New-IntunePackage.ps1 `
  -AppName "foxit pdf" `
  -DeploymentType new `
  -Architecture x64 `
  -IntuneWinAppUtilPath "C:\Intune Prep Tool\IntuneWinAppUtil.exe" `
  -LocalInstallerPath "C:\Users\babat\Downloads\FoxitPDFReader20253_L10N_Setup_x64.exe"
```

Output is stored under:
- `output\<PackageId>\`

Important files:
- `output\<PackageId>\metadata.json`
- `output\<PackageId>\intunewin\Install.intunewin`
- `output\<PackageId>\staging\Install.ps1`
- `output\<PackageId>\staging\Detect.ps1`
- `output\<PackageId>\staging\Requirement.ps1`

## Stage 2: publish only

Use this to publish a package that already exists.

### Delegated auth (default, recommended)

No app registration needed. Opens a browser for interactive sign-in using
the Microsoft Graph PowerShell public client. Uses raw OAuth2 Authorization
Code + PKCE under the hood so it works in PowerShell 5.1 without
.NET EventSource crashes.

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Foxit.FoxitReader\metadata.json" `
  -NewAppAssignment all
```

For device code auth (headless / remote sessions):

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Foxit.FoxitReader\metadata.json" `
  -NewAppAssignment all `
  -IntuneDeviceCode
```

### App registration auth (legacy)

If you have an existing Entra app registration you can still use it:

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Foxit.FoxitReader\metadata.json" `
  -NewAppAssignment all `
  -UseIntuneGraphAuth `
  -IntuneClientId "<app-registration-client-id>" `
  -UseAzCopy
```

## Wrapper: package then publish

If you still want one command, use:

### Delegated auth (default)

```powershell
.\Invoke-IntuneJob.ps1 `
  -AppName "foxit pdf" `
  -TenantId "<tenant-id>" `
  -DeploymentType new `
  -NewAppAssignment all `
  -Architecture x64 `
  -IntuneWinAppUtilPath "C:\Intune Prep Tool\IntuneWinAppUtil.exe"
```

### App registration auth (legacy)

```powershell
.\Invoke-IntuneJob.ps1 `
  -AppName "foxit pdf" `
  -TenantId "<tenant-id>" `
  -DeploymentType new `
  -NewAppAssignment all `
  -Architecture x64 `
  -IntuneWinAppUtilPath "C:\Intune Prep Tool\IntuneWinAppUtil.exe" `
  -UseIntuneGraphAuth `
  -IntuneClientId "<app-registration-client-id>" `
  -UseAzCopy
```

This runs the package stage first, then the publish stage using the generated `metadata.json`.

## Auth

### Delegated auth (default)

The default authentication uses the Microsoft Graph PowerShell public client
(`14d82eec-204b-4c2f-b7e8-296a70dab67e`). No app registration is needed.

When you run a publish command without `-UseIntuneGraphAuth`, a browser window
opens for interactive sign-in (Authorization Code + PKCE). For headless or
remote sessions, add `-IntuneDeviceCode` to use the device code flow instead.

The token is acquired via raw OAuth2 REST calls so it works reliably in
PowerShell 5.1 without .NET EventSource or WAM issues.

Required delegated permissions (consent will be prompted on first sign-in):
- `DeviceManagementApps.ReadWrite.All`
- `DeviceManagementConfiguration.ReadWrite.All`
- `Group.Read.All`

### App registration auth (legacy)

If you prefer an Entra app registration, pass `-UseIntuneGraphAuth` and
`-IntuneClientId`:

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\...\metadata.json" `
  -UseIntuneGraphAuth `
  -IntuneClientId "<app-registration-client-id>"
```

Bootstrap helper:

```powershell
.\bootstrap-intune-app-registration.ps1 `
  -TenantId "<tenant-id>" `
  -DisplayName "IntuneWin32AppAutomation" `
  -GrantAdminConsent
```

## Upload reliability

If native upload is flaky, use:
- `-UseAzCopy`

If upload still fails, publish-stage errors now include:
- upload mode used
- likely network/proxy/TLS causes
- recent module warnings
- log file location

## Recommended workflow for real life

For unreliable networks, do this:

1. run `New-IntunePackage.ps1`
2. confirm `metadata.json` and `.intunewin` were created
3. run `Publish-IntuneWin32App.ps1`
4. rerun only publish if upload/auth/assignment fails

That is much saner than rebuilding the package every time.
