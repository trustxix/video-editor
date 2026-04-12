# Video Editor launcher with smart single-instance handling.
#
# Flow:
#   1. Detect any pythonw processes that match this project's main.py.
#   2. If at least one has a visible main window (MainWindowHandle != 0),
#      it's a HEALTHY instance — bring it to the foreground and exit.
#      Critically, we do NOT kill it. The user may have unsaved trim
#      state, in-progress export, or queued clips. Double-clicking the
#      shortcut while the editor is already open must be a no-op
#      (or at worst, a focus-the-existing-window op).
#   3. If matches exist but none have a window, they're WEDGED (Python
#      alive, Qt crashed). These hold the single-instance mutex and
#      must be cleaned up before a new launch can succeed. Kill them
#      along with any ffmpeg children they left behind.
#   4. Purge stale __pycache__ — same reason as before (pythonw has
#      historically served old .pyc files after .py edits).
#   5. Launch a fresh pythonw.
#
# This replaces the old Video Editor.bat cleanup sweep, which would
# blindly kill any matching pythonw on every launch — that was a
# regression waiting to happen, and as soon as the sweep actually
# started matching instances, it *did* regress.

$ErrorActionPreference = 'Continue'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$MainPy      = Join-Path $ProjectRoot 'main.py'

# Win32 interop for window foregrounding. SetForegroundWindow alone
# can't restore a minimized window — you need ShowWindow(SW_RESTORE)
# first, gated on IsIconic. These three calls together are the
# standard "focus an existing app window" dance on Windows.
if (-not ('Win32Native' -as [type])) {
    Add-Type -Namespace '' -Name 'Win32Native' -MemberDefinition @'
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(System.IntPtr hWnd);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool IsIconic(System.IntPtr hWnd);
'@
}

# Find pythonw processes running THIS project's main.py. Match on the
# absolute path so a second copy of the project in a different folder
# doesn't collide with ours.
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($MainPy) }

# Step 1: healthy instance wins. Focus it and exit without touching
# anything else — no sweep, no pycache purge, no new launch.
foreach ($m in $procs) {
    $p = Get-Process -Id $m.ProcessId -ErrorAction SilentlyContinue
    if ($p -and $p.MainWindowHandle -ne [System.IntPtr]::Zero) {
        $hwnd = $p.MainWindowHandle
        if ([Win32Native]::IsIconic($hwnd)) {
            [void][Win32Native]::ShowWindow($hwnd, 9)  # SW_RESTORE
        }
        [void][Win32Native]::SetForegroundWindow($hwnd)
        exit 0
    }
}

# Step 2: surviving matches are wedged (PID alive, no window). Kill them
# and their ffmpeg children so the mutex is released.
foreach ($m in $procs) {
    Stop-Process -Id $m.ProcessId -Force -ErrorAction SilentlyContinue
}

Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $parent = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$($_.ParentProcessId)" `
            -ErrorAction SilentlyContinue
        $parent -and $parent.Name -eq 'pythonw.exe'
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Step 3: clear stale bytecode so code edits always take effect.
Get-ChildItem -Path (Join-Path $ProjectRoot 'src') `
    -Directory -Recurse -Filter '__pycache__' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

$rootCache = Join-Path $ProjectRoot '__pycache__'
if (Test-Path $rootCache) {
    Remove-Item $rootCache -Recurse -Force -ErrorAction SilentlyContinue
}

# Step 4: launch a fresh instance. PowerShell 5.x does NOT auto-quote
# ArgumentList entries that contain spaces — passing the path as an
# array or bare string would cause pythonw to see the path split on
# every space ("C:\Users\trust\Desktop\Claude" as script name, rest as
# extra args), fail to find the script, and exit silently. Quote the
# path manually so CreateProcess gets the intact argument.
# Do NOT pass -WindowStyle Hidden here. It maps to SW_HIDE for the
# process's initial show-window state, and that permanently breaks
# Process.MainWindowHandle detection on the resulting pythonw — even
# after our own Qt window is visible. Without MainWindowHandle, the
# next launcher run can't tell "healthy instance" from "wedged
# instance" and will incorrectly kill the live window. pythonw is a
# GUI-subsystem binary so there's no console to hide anyway.
$quotedPath = '"' + $MainPy + '"'
Start-Process -FilePath 'pythonw' `
    -ArgumentList $quotedPath `
    -WorkingDirectory $ProjectRoot
