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
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
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
