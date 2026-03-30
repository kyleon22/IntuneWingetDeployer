# Intune Desktop App

A local desktop app that orchestrates the PowerShell-based Intune packaging and publish workflow.

## Why this app exists

The workflow outgrew a single script.

This app gives you:
- a local UI
- package / publish separation
- built-in winget package search and picker
- easier path selection
- command generation without copy/paste pain
- visible logs in one place
- editable app metadata before publish, with sensible autofill defaults from the selected package
- optional IntuneWin32App module version pinning (defaulting to 1.4.3 in the app UI)
- assignment controls in the app
- delegated auth with browser sign-in (no app registration needed) or device code fallback
- saved profiles for tenant/module/tool defaults in multi-tenant environments
- deployment history from prior package/publish runs
- bulk deployment from JSON config files
- a built-in override editor for JSON-based app overrides
- optional supersedence fields for update deployments
- pre-run validation for required auth fields
- a cleaner progress / stage status strip
- the ability to publish an existing package without rebuilding it

## File

- `intune_desktop_app.py`

## Requirements

- Python 3.x
- Windows
- the PowerShell scripts in the same workspace:
  - `New-IntunePackage.ps1`
  - `Publish-IntuneWin32App.ps1`
  - `Invoke-IntuneJob.ps1`

## Run it

```powershell
cd C:\Users\babat\.openclaw\workspace
python .\intune_desktop_app.py
```

## Tabs

### Package
Use this to:
- search/select winget package
- handle broader winget search queries and package IDs with special characters like `notepad`, `notepad++`, `chrome`, `pdf`, etc.
- package an app into `.intunewin`
- save `metadata.json`

### Publish
Use this to:
- choose an existing `metadata.json`
- authenticate via browser sign-in (default) or device code
- optionally use legacy app registration auth
- pin the IntuneWin32App module version if needed
- optionally configure supersedence for update deployments
- upload to Intune
- assign to all or a group

### Package + Publish
Use this when you still want the one-button flow.

This tab has its own winget package picker, metadata fields, and supersedence fields so you can choose the exact application from the UI before running the full flow.

### Profiles + History
Use this to:
- save current tenant/client/module/tool defaults as a profile
- load a saved profile back into the UI
- see previously generated deployment outputs
- reuse an older `metadata.json` for publish-only retries

### Bulk Deploy
Use this to:
- load a JSON file containing multiple app deployment definitions
- preview the config
- run deployments sequentially through the existing PowerShell workflow

### Override Editor
Use this to:
- load an override JSON file
- edit detection/install/metadata overrides
- save the file back from inside the app

### Logs
Shows the live output of the PowerShell scripts.

## Recommended workflow

For unreliable upload paths:
1. use **Package** first
2. confirm `metadata.json` exists
3. use **Publish** separately
4. retry publish without rebuilding

That is the whole point.
