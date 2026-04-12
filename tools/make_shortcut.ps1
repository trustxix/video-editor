$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Video Editor.lnk")
$shortcut.TargetPath = Join-Path $projectRoot "Video Editor.bat"
$shortcut.WorkingDirectory = $projectRoot
$iconPath = Join-Path $projectRoot "assets\icon.ico"
if (Test-Path $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
} else {
    # Fall back to a stock Windows icon if the custom one hasn't been
    # generated yet (run: python tools\generate_icon.py).
    $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,178"
}
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Host "Shortcut created on desktop -> $projectRoot\Video Editor.bat"
