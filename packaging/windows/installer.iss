; Inno Setup script for SubbyAI.
; Build after PyInstaller:  iscc packaging\windows\installer.iss
; Produces dist\SubbyAI-Setup-<version>.exe

#define AppName "SubbyAI"
; CI passes the single-source version from pyproject.toml:
;   iscc /DAppVersion=x.y.z packaging\windows\installer.iss
; The fallback keeps local builds working between releases.
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#define AppPublisher "SubbyAI contributors"
#define AppURL "https://github.com/ChanJianHao/SubbyAI"
#define AppExeName "SubbyAI.exe"

[Setup]
AppId={{2F7C4A16-9E3B-4D58-B0A1-6C21E5F84D33}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; Per-user install: no admin prompt, clean uninstall.
PrivilegesRequired=lowest
LicenseFile=..\..\LICENSE
OutputBaseFilename=SubbyAI-Setup-{#AppVersion}
OutputDir=..\..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\src\subbyai\resources\icon.ico
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
DisableProgramGroupPage=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "..\..\dist\SubbyAI\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Settings, transcripts and downloaded engines live under %APPDATA% and are
; deliberately kept, so reinstalling restores the user's setup.
