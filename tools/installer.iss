; Inno Setup script for Video Editor
; Built by the release pipeline, which passes the version from
; src\core\version.py:
;   .\tools\release.ps1
; By hand:
;   ISCC.exe /DMyAppVersion=<x.y.z> tools\installer.iss
;
; "Install for all users" puts the app under Program Files, which standard
; users can't write to; the app then keeps its config in
; %LOCALAPPDATA%\Video Editor\config (src\core\paths.get_config_dir).

#ifndef MyAppVersion
  #error Pass /DMyAppVersion=<x.y.z>; tools\release.ps1 reads it from src\core\version.py
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

; ── Upgrade hardening ──────────────────────────────────────────────────
; AppMutex matches main._is_already_running() so the installer detects a
; running app and prompts/closes it before overwriting Video Editor.exe.
; CloseApplications=force tells Inno Setup to close the running app
; gracefully (WM_CLOSE) instead of failing on the file lock.
; RestartApplications=yes re-launches it after install completes.
; UsePreviousAppDir=yes (default, explicit for clarity) means re-running
; the installer with the same AppId picks up the existing install dir.
AppMutex=Local\VideoEditor_Instance
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
UsePreviousLanguage=yes
UninstallRestartComputer=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Never ship runtime state (settings, logs, crash dumps) from a dist that was
; launched after the build; the app creates config\ itself.
Source: "..\dist\Video Editor\*"; DestDir: "{app}"; Excludes: "\config\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

; ──────────────────────────────────────────────────────────────────────────
; Pascal logic for upgrade/downgrade detection.
;
;   InitializeSetup     : refuses (with confirmation) downgrades over a
;                         newer existing install.
;   PrevInstallLocation : reads the registered install dir of the prior
;                         install (if any) so we can show it in the wizard
;                         and detect side-by-side install attempts.
;   NextButtonClick     : when the user accepts a Select-Destination dir
;                         that differs from the registered prior dir,
;                         warn that they're creating a parallel install.
; ──────────────────────────────────────────────────────────────────────────
[Code]

// IMPORTANT: the registry uninstall key uses SINGLE braces around the GUID
// (e.g. {C8A3F2E0-...}_is1), but Inno's preprocessor `{#MyAppId}` expands
// to the DOUBLE-braced form (`{{C8A3F2E0-...}`) because that's how the
// AppId is escaped in the [Setup] directive. Hardcoding the single-braced
// form here is the simplest way to keep the registry path correct.
// Keep this in sync with `MyAppId` above if the GUID is ever rotated.
const
  UninstallKeyPath = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{C8A3F2E0-7B4D-4A5C-9E1F-A3B5C7D9E0F1}_is1';

function GetPrevInstallReg(const ValueName: String): String;
var
  Value: String;
begin
  Result := '';
  Value := '';
  if RegQueryStringValue(HKCU, UninstallKeyPath, ValueName, Value) then begin
    Result := Value;
    Exit;
  end;
  if RegQueryStringValue(HKLM, UninstallKeyPath, ValueName, Value) then begin
    Result := Value;
  end;
end;

function GetPrevInstalledVersion: String;
begin
  Result := GetPrevInstallReg('DisplayVersion');
end;

function GetPrevInstallLocation: String;
begin
  Result := GetPrevInstallReg('InstallLocation');
end;

function CompareVersions(const A, B: String): Integer;
var
  SA, SB: String;
  PA, PB, NA, NB: Integer;
begin
  Result := 0;
  SA := A;
  SB := B;
  while (SA <> '') or (SB <> '') do begin
    PA := Pos('.', SA);
    if PA > 0 then begin
      NA := StrToIntDef(Copy(SA, 1, PA - 1), 0);
      Delete(SA, 1, PA);
    end else begin
      NA := StrToIntDef(SA, 0);
      SA := '';
    end;
    PB := Pos('.', SB);
    if PB > 0 then begin
      NB := StrToIntDef(Copy(SB, 1, PB - 1), 0);
      Delete(SB, 1, PB);
    end else begin
      NB := StrToIntDef(SB, 0);
      SB := '';
    end;
    if NA < NB then begin
      Result := -1;
      Exit;
    end else if NA > NB then begin
      Result := 1;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  PrevVer: String;
begin
  Result := True;
  PrevVer := GetPrevInstalledVersion();
  if PrevVer = '' then Exit;

  if CompareVersions(PrevVer, '{#MyAppVersion}') > 0 then begin
    if WizardSilent() then begin
      // Silent mode auto-answers MsgBox prompts as YES, which would
      // permit accidental downgrades. Refuse outright instead. The
      // exit-code-non-zero gives automation a clear signal.
      Log('Refusing downgrade from ' + PrevVer + ' to {#MyAppVersion} (silent mode)');
      Result := False;
      Exit;
    end;
    if MsgBox(
      'A newer version (' + PrevVer + ') of {#MyAppName} is already installed.' + #13#10 +
      'You are about to downgrade to {#MyAppVersion}.' + #13#10 + #13#10 +
      'Continue with the downgrade?',
      mbConfirmation, MB_YESNO) <> IDYES then begin
      Result := False;
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  PrevDir: String;
  ChosenDir: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then Exit;

  PrevDir := GetPrevInstallLocation();
  if PrevDir = '' then Exit;

  ChosenDir := WizardForm.DirEdit.Text;
  if (CompareText(ChosenDir, PrevDir) = 0) then Exit;

  if MsgBox(
    '{#MyAppName} is already installed at:' + #13#10 +
    '    ' + PrevDir + #13#10 + #13#10 +
    'Installing to a different location creates a parallel installation,' + #13#10 +
    'which can confuse file associations and update behavior.' + #13#10 + #13#10 +
    'Click No to install over the existing version (recommended).' + #13#10 +
    'Click Yes to install side-by-side anyway.',
    mbConfirmation, MB_YESNO) <> IDYES then begin
    WizardForm.DirEdit.Text := PrevDir;
    Result := False;
  end;
end;
