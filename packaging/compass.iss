; Установщик Windows. Собирается после PyInstaller: ISCC packaging\compass.iss
#define AppVersion "0.1.0"

[Setup]
AppId={{8B6E2A1C-4F0D-4C3A-9A71-6E5D0C8B1F44}
AppName=Compass
AppVersion={#AppVersion}
AppPublisher=Compass
DefaultDirName={autopf}\Compass
DefaultGroupName=Compass
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=CompassSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Compass.exe

[Files]
Source: "dist\Compass\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Compass"; Filename: "{app}\Compass.exe"
Name: "{autodesktop}\Compass"; Filename: "{app}\Compass.exe"

[Run]
Filename: "{app}\Compass.exe"; Description: "Запустить Compass"; Flags: nowait postinstall skipifsilent
