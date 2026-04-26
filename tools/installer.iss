; Inno Setup script for Video Editor
; Build with:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" tools\installer.iss
; Or via the release pipeline:
;   .\tools\release.ps1 -Version 0.1.0

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName        "Video Editor"
#define MyAppPublisher   "Trust"
#define MyAppExeName     "Video Editor.exe"
#define MyAppId          "{{C8A3F2E0-7B4D-4A5C-9E1F-A3B5C7D9E0F1}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=VideoEditor-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "associate_mp4"; Description: "Associate .mp4 files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked
Name: "associate_mov"; Description: "Associate .mov files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked
Name: "associate_mkv"; Description: "Associate .mkv files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked
Name: "associate_webm"; Description: "Associate .webm files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
Source: "..\dist\Video Editor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; ── File associations (HKCU only — works without admin rights) ──────────
[Registry]
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithProgids";          ValueType: string; ValueName: "VideoEditor.mp4"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mp4";               ValueType: string; ValueName: ""; ValueData: "Video Editor MP4 File"; Flags: uninsdeletekey; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mp4\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate_mp4
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mp4\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_mp4

Root: HKCU; Subkey: "Software\Classes\.mov\OpenWithProgids";          ValueType: string; ValueName: "VideoEditor.mov"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_mov
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mov";               ValueType: string; ValueName: ""; ValueData: "Video Editor MOV File"; Flags: uninsdeletekey; Tasks: associate_mov
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mov\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate_mov
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mov\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_mov

Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithProgids";          ValueType: string; ValueName: "VideoEditor.mkv"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_mkv
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mkv";               ValueType: string; ValueName: ""; ValueData: "Video Editor MKV File"; Flags: uninsdeletekey; Tasks: associate_mkv
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mkv\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate_mkv
Root: HKCU; Subkey: "Software\Classes\VideoEditor.mkv\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_mkv

Root: HKCU; Subkey: "Software\Classes\.webm\OpenWithProgids";          ValueType: string; ValueName: "VideoEditor.webm"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate_webm
Root: HKCU; Subkey: "Software\Classes\VideoEditor.webm";               ValueType: string; ValueName: ""; ValueData: "Video Editor WebM File"; Flags: uninsdeletekey; Tasks: associate_webm
Root: HKCU; Subkey: "Software\Classes\VideoEditor.webm\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate_webm
Root: HKCU; Subkey: "Software\Classes\VideoEditor.webm\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_webm

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
