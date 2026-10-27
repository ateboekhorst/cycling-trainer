; Inno Setup script: wraps dist\Cycling Trainer\ into a normal Windows installer.
; Build with build_installer.ps1, which passes the version: ISCC /DAppVersion=x.y.z installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Cycling Trainer"
#define AppExe "Cycling Trainer.exe"

[Setup]
AppId={{7F3B6C1E-4D2A-4B8E-9C61-2E5A8D0F4B17}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without admin rights; the wizard offers "all users" too.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=CyclingTrainer-Setup-{#AppVersion}
SetupIconFile=..\trainer\resources\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Rides, workouts, the plan and settings (Documents\CyclingTrainer, %APPDATA%\CyclingTrainer)
; are left in place on uninstall.
