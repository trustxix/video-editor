$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("$env:USERPROFILE\Desktop\Video Editor.lnk")
$shortcut.TargetPath = Join-Path $projectRoot "Video Editor.bat"
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,178"
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Host "Shortcut created on desktop -> $projectRoot\Video Editor.bat"
