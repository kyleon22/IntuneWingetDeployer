Set-Location -LiteralPath $PSScriptRoot
if (Get-Command python -ErrorAction SilentlyContinue) {
    python .\intune_desktop_app.py
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 .\intune_desktop_app.py
}
else {
    Write-Error 'Python was not found in PATH.'
}
