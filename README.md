# Intune Winget Deployer

Automate packaging and deploying Windows applications to Microsoft Intune using [winget](https://github.com/microsoft/winget-cli) as the source catalog. Includes PowerShell scripts for CLI usage and a Python desktop GUI for point-and-click operation.

---

## Table of Contents

- [Why This Exists](#why-this-exists)
- [Architecture](#architecture)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Usage — PowerShell CLI](#usage--powershell-cli)
  - [Stage 1: Package](#stage-1-package)
  - [Stage 2: Publish](#stage-2-publish)
  - [Combined: Package + Publish](#combined-package--publish)
- [Usage — Desktop App (GUI)](#usage--desktop-app-gui)
- [Authentication Setup](#authentication-setup)
- [Override Configuration](#override-configuration)
- [Output Structure](#output-structure)
- [Deployment Types](#deployment-types)
- [Upload Reliability](#upload-reliability)
- [Troubleshooting](#troubleshooting)
- [File Reference](#file-reference)
- [License](#license)

---

## Why This Exists

Most Intune packaging tools combine downloading, wrapping, uploading, and assigning into a single monolithic script. When anything fails — a flaky VPN, an expired token, a Graph API hiccup — you start the entire process from scratch.

**Intune Winget Deployer separates packaging from publishing.** If packaging succeeds, you keep the `.intunewin` file. If the upload fails, you retry only the publish step. If auth fails, you don't rebuild the package. This separation makes deployments resilient in real-world enterprise networks.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    USER INTERFACE LAYER                      │
├─────────────────────────────────────────────────────────────┤
│  intune_desktop_app.py      Tkinter GUI with tabbed UI      │
│  Launch-IntuneDesktopApp    .cmd / .ps1 launchers            │
├─────────────────────────────────────────────────────────────┤
│                    STAGE 1 — PACKAGING                       │
├─────────────────────────────────────────────────────────────┤
│  New-IntunePackage.ps1      Search winget, resolve metadata, │
│                             download installer, generate     │
│                             scripts, wrap into .intunewin    │
├─────────────────────────────────────────────────────────────┤
│                    STAGE 2 — PUBLISHING                      │
├─────────────────────────────────────────────────────────────┤
│  Publish-IntuneWin32App.ps1 Auth to Intune, upload package,  │
│                             configure detection/requirements,│
│                             set supersedence, assign          │
├─────────────────────────────────────────────────────────────┤
│                    WRAPPERS & BOOTSTRAP                      │
├─────────────────────────────────────────────────────────────┤
│  Invoke-IntuneJob.ps1       Runs both stages sequentially    │
│  bootstrap-intune-app-      Creates Entra ID app registration│
│    registration.ps1                                          │
├─────────────────────────────────────────────────────────────┤
│                    SUPPORT SCRIPTS                           │
├─────────────────────────────────────────────────────────────┤
│  Test-IntuneDesktopApp      Validates all prerequisites      │
│    Prereqs.ps1                                               │
│  Install-IntuneDesktopApp   Installs missing dependencies    │
│    Prereqs.ps1                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Features

- **Winget-sourced packaging** — search the winget catalog, resolve installer metadata, download MSI/EXE/MSIX installers, and wrap them into `.intunewin` format
- **Auto-generated scripts** — creates `Install.ps1`, `Detect.ps1`, and `Requirement.ps1` tailored to each installer type
- **Two-stage workflow** — package and publish independently for maximum reliability
- **Multiple auth methods** — interactive browser, device code flow, or custom app registration via Microsoft Graph
- **Supersedence support** — update deployments automatically configure app replacement relationships in Intune
- **Assignment flexibility** — assign to all devices or a specific Entra ID group
- **Override system** — JSON-based overrides for display names, install commands, detection scripts, and more
- **Desktop GUI** — Tkinter app with Package, Publish, Package+Publish, Profiles, Bulk Deploy, Override Editor, and Logs tabs
- **Profile system** — save and switch between tenant/client/tool configurations for multi-tenant environments
- **Deployment history** — track and reuse prior deployments
- **Bulk deploy** — load a JSON config to deploy multiple apps sequentially
- **AzCopy support** — optional AzCopy-backed uploads for unreliable network connections

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Windows 10/11** | Required OS |
| **PowerShell 5.1+** | Ships with Windows |
| **winget** | [Windows Package Manager](https://github.com/microsoft/winget-cli) |
| **IntuneWinAppUtil.exe** | [Microsoft Win32 Content Prep Tool](https://github.com/Microsoft/Microsoft-Win32-Content-Prep-Tool) — place in this folder or specify the path |
| **Python 3.x** | Only required for the desktop GUI |
| **Entra ID permissions** | App registration with delegated Graph permissions (see [Authentication Setup](#authentication-setup)) |

### Required PowerShell Modules

| Module | Purpose |
|---|---|
| `IntuneWin32App` | Upload and manage Win32 apps in Intune |
| `Microsoft.Graph.Authentication` | Microsoft Graph auth |
| `Microsoft.Graph.Applications` | App registration management |
| `Microsoft.Graph.Groups` | Group-based assignment |

Install all prerequisites automatically:

```powershell
.\Install-IntuneDesktopAppPrereqs.ps1
```

Or validate what's already installed:

```powershell
.\Test-IntuneDesktopAppPrereqs.ps1
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/IntuneWingetDeployer.git
cd IntuneWingetDeployer
```

### 2. Place IntuneWinAppUtil.exe

Download [Microsoft Win32 Content Prep Tool](https://github.com/Microsoft/Microsoft-Win32-Content-Prep-Tool) and copy `IntuneWinAppUtil.exe` into this folder.

### 3. Install dependencies

```powershell
.\Install-IntuneDesktopAppPrereqs.ps1
```

### 4. Create an Entra ID app registration

```powershell
.\bootstrap-intune-app-registration.ps1 `
  -TenantId "<your-tenant-id>" `
  -DisplayName "IntuneWin32AppAutomation" `
  -GrantAdminConsent
```

Save the **Client ID** and **Tenant ID** from the output.

### 5. Package an app

```powershell
.\New-IntunePackage.ps1 `
  -AppName "Google Chrome" `
  -DeploymentType new `
  -Architecture x64 `
  -IntuneWinAppUtilPath ".\IntuneWinAppUtil.exe"
```

### 6. Publish to Intune

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<your-tenant-id>" `
  -MetadataPath ".\output\Google.Chrome\metadata.json" `
  -NewAppAssignment all `
  -UseIntuneGraphAuth `
  -IntuneClientId "<your-client-id>"
```

---

## Usage — PowerShell CLI

### Stage 1: Package

`New-IntunePackage.ps1` searches winget, resolves installer metadata, generates deployment scripts, and wraps everything into `.intunewin` format.

```powershell
.\New-IntunePackage.ps1 `
  -AppName "foxit pdf" `
  -DeploymentType new `
  -Architecture x64 `
  -IntuneWinAppUtilPath ".\IntuneWinAppUtil.exe"
```

**With a local installer** (skip download):

```powershell
.\New-IntunePackage.ps1 `
  -AppName "foxit pdf" `
  -DeploymentType new `
  -Architecture x64 `
  -IntuneWinAppUtilPath ".\IntuneWinAppUtil.exe" `
  -LocalInstallerPath "C:\Downloads\FoxitReader_Setup.exe"
```

**For update deployments** (with supersedence):

```powershell
.\New-IntunePackage.ps1 `
  -AppName "Google Chrome" `
  -DeploymentType update `
  -Architecture x64 `
  -IntuneWinAppUtilPath ".\IntuneWinAppUtil.exe"
```

### Stage 2: Publish

`Publish-IntuneWin32App.ps1` reads the `metadata.json` from Stage 1 and uploads the package to Intune.

**Interactive auth:**

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Google.Chrome\metadata.json" `
  -NewAppAssignment all `
  -UseIntuneGraphAuth `
  -IntuneClientId "<client-id>"
```

**Device code auth** (for headless/remote sessions):

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Google.Chrome\metadata.json" `
  -NewAppAssignment all `
  -UseIntuneGraphAuth `
  -IntuneClientId "<client-id>" `
  -IntuneDeviceCode
```

**Assign to a specific group:**

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Google.Chrome\metadata.json" `
  -NewAppAssignment group `
  -GroupName "All Workstations" `
  -UseIntuneGraphAuth `
  -IntuneClientId "<client-id>"
```

### Combined: Package + Publish

`Invoke-IntuneJob.ps1` runs both stages sequentially in a single command:

```powershell
.\Invoke-IntuneJob.ps1 `
  -AppName "Google Chrome" `
  -TenantId "<tenant-id>" `
  -DeploymentType new `
  -NewAppAssignment all `
  -Architecture x64 `
  -IntuneWinAppUtilPath ".\IntuneWinAppUtil.exe" `
  -UseIntuneGraphAuth `
  -IntuneClientId "<client-id>"
```

> **Recommended:** Use the two-stage workflow for production. If the upload fails, you can retry `Publish-IntuneWin32App.ps1` without rebuilding the package.

---

## Usage — Desktop App (GUI)

The desktop app provides a Tkinter-based GUI that wraps the PowerShell workflow.

### Launch

```powershell
# PowerShell
.\Launch-IntuneDesktopApp.ps1

# or Command Prompt
Launch-IntuneDesktopApp.cmd

# or directly
python intune_desktop_app.py
```

### Tabs

| Tab | Purpose |
|---|---|
| **Package** | Search winget, select a package, configure architecture/deployment type, and build the `.intunewin` |
| **Publish** | Select an existing `metadata.json`, authenticate, upload to Intune, and assign |
| **Package + Publish** | Combined one-click flow with all settings in one place |
| **Profiles + History** | Save/load tenant profiles, browse previous deployments, reuse metadata for retries |
| **Bulk Deploy** | Load a JSON config file to deploy multiple apps sequentially |
| **Override Editor** | Edit JSON overrides for app metadata, install commands, and detection scripts |
| **Logs** | Live output from the PowerShell scripts |

The app persists settings in the `.intune_desktop_app/` folder (profiles, UI state, bootstrap history).

---

## Authentication Setup

### Create an App Registration

Use the included bootstrap script:

```powershell
.\bootstrap-intune-app-registration.ps1 `
  -TenantId "<your-tenant-id>" `
  -DisplayName "IntuneWin32AppAutomation" `
  -GrantAdminConsent
```

| Parameter | Default | Description |
|---|---|---|
| `-TenantId` | *(required)* | Your Azure AD / Entra ID tenant ID |
| `-DisplayName` | `IntuneWin32AppAutomation` | Name for the app registration |
| `-RedirectUri` | `http://localhost` | Redirect URI for auth |
| `-GrantAdminConsent` | `$false` | Auto-grant tenant-wide admin consent |

### Required Microsoft Graph Permissions (Delegated)

| Permission | Purpose |
|---|---|
| `DeviceManagementApps.ReadWrite.All` | Create and manage Intune Win32 apps |
| `Group.Read.All` | Look up Entra ID groups for assignment |
| `User.Read` | Basic auth |
| `offline_access` | Refresh tokens for long-running sessions |

---

## Override Configuration

Create an `intune-winget-overrides.json` file (see `intune-winget-overrides.sample.json`) to customize app metadata, install commands, and detection scripts per package:

```json
{
  "Google.Chrome": {
    "DisplayName": "Google Chrome Enterprise",
    "Publisher": "Google",
    "Description": "Managed browser deployment",
    "SilentCommand": "/silent /install",
    "UninstallCommand": "\"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" --uninstall --force-uninstall",
    "DetectionScript": "<PowerShell detection script>",
    "RequirementScript": "<PowerShell requirement script>"
  }
}
```

**Available override fields:**

| Field | Description |
|---|---|
| `DisplayName` | App display name in Intune |
| `Publisher` | Publisher name |
| `Description` | App description |
| `SilentCommand` | Custom silent install arguments |
| `InstallCommand` | Full custom install command |
| `UninstallCommand` | Custom uninstall command |
| `DetectionScript` | PowerShell detection script body |
| `RequirementScript` | PowerShell requirement script body |

---

## Output Structure

Each packaged app produces the following under `output/<PackageId>/`:

```
output/
  Google.Chrome/
    metadata.json           # Deployment snapshot (used by publish stage)
    publish.json            # Post-publish record (app ID, assignment, tenant)
    download/
      installer.msi         # Cached installer
    staging/
      Install.ps1           # Installation script
      Detect.ps1            # Detection script (registry-based)
      Requirement.ps1       # Requirement script (version check, update only)
      installer.msi         # Installer copy for wrapping
    intunewin/
      Install.intunewin     # Packaged file for Intune upload
```

- **metadata.json** — contains the package ID, version, installer type, URLs, architecture, deployment type, and paths to all generated artifacts. This file is the handoff between Stage 1 and Stage 2.
- **publish.json** — created after a successful publish with the Intune app ID, display name, tenant ID, and assignment details.

---

## Deployment Types

| Type | Behavior |
|---|---|
| `new` | Fresh deployment. App is uploaded and assigned with standard detection. |
| `update` | Update deployment. Generates a `Requirement.ps1` that checks the installed version against the target version. Configures supersedence in Intune so the new version replaces the old one. |

For update deployments, the generated `Requirement.ps1` returns `True` only if the currently installed version is older than the target version, ensuring the update is applied only where needed.

---

## Upload Reliability

If native uploads are unreliable (VPN, proxy, flaky connections), use the `-UseAzCopy` flag:

```powershell
.\Publish-IntuneWin32App.ps1 `
  -TenantId "<tenant-id>" `
  -MetadataPath ".\output\Google.Chrome\metadata.json" `
  -NewAppAssignment all `
  -UseIntuneGraphAuth `
  -IntuneClientId "<client-id>" `
  -UseAzCopy
```

If the upload still fails, the error output includes:
- Upload mode used
- Likely network/proxy/TLS causes
- Recent module warnings
- Log file location

Since packaging and publishing are separate, you can **retry the publish step** without rebuilding the package.

---

## Troubleshooting

| Issue | Resolution |
|---|---|
| `IntuneWinAppUtil.exe` not found | Download from [Microsoft's repo](https://github.com/Microsoft/Microsoft-Win32-Content-Prep-Tool) and place in this folder or pass `-IntuneWinAppUtilPath` |
| winget not found | Install from [winget-cli releases](https://github.com/microsoft/winget-cli/releases) or the Microsoft Store |
| Module version issues | Pin `IntuneWin32App` to version `1.4.3` via the GUI or install with `Install-Module IntuneWin32App -RequiredVersion 1.4.3` |
| Auth failures | Ensure your app registration has the required Graph permissions and admin consent has been granted |
| Upload timeout | Use `-UseAzCopy` for more resilient uploads; retry the publish step only |
| Detection script false positives | Use the Override Editor to customize the detection script for the specific app |

---

## File Reference

| File | Description |
|---|---|
| `New-IntunePackage.ps1` | Stage 1 — Package an app from winget into `.intunewin` |
| `Publish-IntuneWin32App.ps1` | Stage 2 — Upload and assign a packaged app to Intune |
| `Invoke-IntuneJob.ps1` | Convenience wrapper that runs both stages sequentially |
| `intune_desktop_app.py` | Tkinter desktop GUI for the full workflow |
| `Launch-IntuneDesktopApp.ps1` | PowerShell launcher for the GUI |
| `Launch-IntuneDesktopApp.cmd` | CMD launcher for the GUI |
| `bootstrap-intune-app-registration.ps1` | Creates an Entra ID app registration with required permissions |
| `Install-IntuneDesktopAppPrereqs.ps1` | Installs required PowerShell modules and dependencies |
| `Test-IntuneDesktopAppPrereqs.ps1` | Validates all prerequisites and outputs JSON status |
| `intune-winget-overrides.sample.json` | Sample override configuration file |
| `BUNDLE-NOTES.txt` | Notes for portable/standalone bundle setup |

---

## License

This project is provided as-is for IT administration use. See [LICENSE](LICENSE) for details.
