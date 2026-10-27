; Inno Setup script for LocalFlow (per-user install, no admin rights needed).
;   ISCC.exe packaging\LocalFlow.iss            (after the PyInstaller build)
#define AppName "LocalFlow"
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppPublisher "Partharsid"
#define AppURL "https://github.com/Partharsid/local-flow"

[Setup]
AppId={{6B0E2F1A-8C3D-4E59-9A7B-2D4C1F0E8B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\build\installer
OutputBaseFilename=LocalFlow-Setup-{#AppVersion}
SetupIconFile=..\assets\localflow.ico
UninstallDisplayIcon={app}\LocalFlow.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
; Ask to close a running LocalFlow (it holds this mutex).
AppMutex=Local\LocalFlowSingleInstance
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start LocalFlow when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\build\dist\LocalFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\LocalFlow.exe"; Comment: "Private offline voice dictation"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LocalFlow.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\LocalFlow.exe"; Tasks: startup

[Run]
Filename: "{app}\LocalFlow.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent
; Self-update: LocalFlow runs the installer with /SILENT /relaunch=1 and quits.
Filename: "{app}\LocalFlow.exe"; Flags: nowait; Check: IsRelaunch

[Code]
function IsRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\LocalFlow');
    if DirExists(DataDir) and not UninstallSilent() then
      if MsgBox('Also delete your LocalFlow settings, history and downloaded AI models (about 4-5 GB)?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
