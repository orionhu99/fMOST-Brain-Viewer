#ifndef MyAppVersion
  #error MyAppVersion must be supplied by build/build_release.ps1
#endif

#define MyAppName "fMOST Brain Viewer"
#define MyAppPublisher "Orion HU; Li Bo Lab, Westlake University"
#define MyAppURL "https://github.com/orionhu99/fMOST-Brain-Viewer"
#define MyAppExeName "fMOST Brain Viewer.exe"

[Setup]
#ifdef TestInstall
AppId={{927E33B2-5A30-4389-B401-987E7B8B814E}
CreateUninstallRegKey=no
UsePreviousAppDir=no
#else
AppId={{6B53B932-627A-4A3E-8B1E-EA8F15AA0A47}
#endif
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsedUserAreasWarning=no
#ifdef TestInstall
OutputDir=..\artifacts\installer-test
OutputBaseFilename=fMOST-Brain-Viewer-Installer-Test-{#MyAppVersion}
#else
OutputDir=..\release
OutputBaseFilename=fMOST-Brain-Viewer-Setup-{#MyAppVersion}-win64
#endif
SetupIconFile=..\assets\fmost_brain_viewer.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\fMOST Brain Viewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifndef TestInstall
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; Check: IsRegularInstall
; Unlike the normal optional launch, an accepted update resumes even in silent mode.
Filename: "{app}\{#MyAppExeName}"; Parameters: "{code:GetUpdateResumeParameters}"; Flags: nowait runasoriginaluser; Check: IsUpdateInstall

[Code]
const
  ProcessSynchronize = $00100000;
  ProcessQueryLimitedInformation = $1000;
  WaitObjectSignaled = 0;
  WaitTimeout = 258;
  ErrorInvalidParameter = 87;
  UpdateWaitMilliseconds = 60000;

var
  UpdateSession: String;
  UpdatePID: Integer;
  UpdatingApplication: Boolean;

function OpenProcess(Access: DWORD; InheritHandle: BOOL; ProcessID: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall setuponly';
function QueryFullProcessImageName(ProcessHandle: THandle; Flags: DWORD;
  ImageName: String; var Size: DWORD): BOOL;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall setuponly';
function WaitForSingleObject(Handle: THandle; Milliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall setuponly';
function CloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall setuponly';

function IsUpdateInstall: Boolean;
begin
  Result := UpdatingApplication;
end;

function IsRegularInstall: Boolean;
begin
  Result := not UpdatingApplication;
end;

function GetUpdateResumeParameters(Param: String): String;
begin
  { InitializeSetup permits no quotes or control characters in this path. }
  Result := '--resume-update "' + UpdateSession + '"';
end;

function IsValidUpdateSession(const Value: String): Boolean;
var
  Index: Integer;
  Name: String;
  ExpectedDirectory: String;
begin
  Result := False;
  if (Length(Value) < 4) or (Length(Value) > 4096) then
    Exit;
  { Require an ordinary absolute drive path, not a UNC, device or relative path. }
  if (Value[2] <> ':') or (Value[3] <> '\') then
    Exit;
  for Index := 1 to Length(Value) do
    if (Ord(Value[Index]) < 32) or (Value[Index] = '"') or
       ((Value[Index] = ':') and (Index <> 2)) then
      Exit;
  ExpectedDirectory := ExpandConstant('{localappdata}\fMOST Brain Viewer\updates');
  if CompareText(ExtractFileDir(ExpandFileName(Value)), ExpectedDirectory) <> 0 then
    Exit;
  { A direct child with the same name is also checked by --resume-update. }
  Name := ExtractFileName(Value);
  if (Length(Name) <> 58) or (Copy(Name, 1, 7) <> 'resume-') or
     (Copy(Name, 40, 19) <> '.fmost-session.json') then
    Exit;
  for Index := 8 to 39 do
    if Pos(Name[Index], '0123456789abcdef') = 0 then
      Exit;
  Result := FileExists(Value);
end;

function InitializeSetup: Boolean;
var
  PIDText: String;
  Index: Integer;
begin
  Result := True;
  UpdateSession := ExpandConstant('{param:UPDATESESSION|}');
  PIDText := ExpandConstant('{param:UPDATEPID|}');
  UpdatingApplication := (UpdateSession <> '') or (PIDText <> '');
  if not UpdatingApplication then
    Exit;
  Result := IsValidUpdateSession(UpdateSession) and (PIDText <> '');
  for Index := 1 to Length(PIDText) do
    if Pos(PIDText[Index], '0123456789') = 0 then
      Result := False;
  UpdatePID := StrToIntDef(PIDText, 0);
  Result := Result and (UpdatePID > 0);
  if not Result then
    SuppressibleMsgBox('The update recovery parameters are invalid. Reopen the viewer and try the update again.',
      mbError, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProcessHandle: THandle;
  ProcessPath: String;
  PathLength: DWORD;
  WaitResult: DWORD;
  Elapsed: Integer;
begin
  Result := '';
  if not UpdatingApplication then
    Exit;
  if not IsValidUpdateSession(UpdateSession) then
  begin
    Result := 'The saved update session is unavailable. Reopen the viewer and try again.';
    Exit;
  end;
  { Hold a handle, so a later reuse of the PID cannot change which process is waited for. }
  ProcessHandle := OpenProcess(ProcessSynchronize or ProcessQueryLimitedInformation,
    False, UpdatePID);
  if ProcessHandle = 0 then
  begin
    if DLLGetLastError = ErrorInvalidParameter then
      Log('The viewer already exited before update preparation.')
    else
      Result := 'Cannot verify the running viewer. Close it and run the update again.';
    Exit;
  end;
  try
    PathLength := 32768;
    SetLength(ProcessPath, PathLength);
    if not QueryFullProcessImageName(ProcessHandle, 0, ProcessPath, PathLength) then
    begin
      { The viewer may have exited between opening the handle and reading its path. }
      if WaitForSingleObject(ProcessHandle, 0) <> WaitObjectSignaled then
        Result := 'Cannot verify the running viewer. Close it and run the update again.';
      Exit;
    end;
    SetLength(ProcessPath, PathLength);
    if CompareText(ExpandFileName(ProcessPath), ExpandConstant('{app}\{#MyAppExeName}')) <> 0 then
    begin
      Result := 'The update process does not match this installation. Reopen the installed viewer and try again.';
      Exit;
    end;
    Log('Waiting for the viewer to finish saving and close naturally.');
    Elapsed := 0;
    repeat
      WaitResult := WaitForSingleObject(ProcessHandle, 250);
      Elapsed := Elapsed + 250;
      WizardForm.Update;
    until (WaitResult <> WaitTimeout) or (Elapsed >= UpdateWaitMilliseconds);
    if WaitResult = WaitTimeout then
      Result := 'The viewer is still open. The update was not applied. Close the viewer and try again.'
    else if WaitResult <> WaitObjectSignaled then
      Result := 'Could not wait for the viewer to close. The update was not applied.';
  finally
    CloseHandle(ProcessHandle);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Target: String;
  ExistingVersion: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Target := ExpandConstant('{app}');
    if (CompareText(Target, ExpandConstant('{win}')) = 0) or
       (CompareText(Target, ExpandConstant('{sys}')) = 0) or
       (CompareText(Target, ExpandConstant('{autopf}')) = 0) or
       (Length(Target) <= 3) then
    begin
      MsgBox('Choose a dedicated application folder, not a system or drive root.', mbError, MB_OK);
      Result := False;
    end;
    if FileExists(Target + '\{#MyAppExeName}') and
       not GetVersionNumbersString(Target + '\{#MyAppExeName}', ExistingVersion) then
    begin
      { Legacy releases may lack version metadata; no folder-wide deletion is used. }
      Log('Existing application has no version metadata; replacing packaged files only.');
    end;
  end;
end;
