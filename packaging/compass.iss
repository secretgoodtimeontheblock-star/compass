; Inno Setup: установщик без прав администратора, ярлыки в меню «Пуск» и на рабочем столе.
; Данные пользователя (%APPDATA%\Compass) при удалении программы не трогаются.
#ifndef AppVersion
  #define AppVersion "0.0.1"
#endif

[Setup]
AppId={{6D3A0C1E-5F52-4B7B-9A0E-C0A55A5E0001}
AppName=Compass
AppVersion={#AppVersion}
DefaultDirName={autopf}\Compass
DefaultGroupName=Compass
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Compass-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Compass.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "..\dist\Compass\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Compass"; Filename: "{app}\Compass.exe"
Name: "{autodesktop}\Compass"; Filename: "{app}\Compass.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Compass.exe"; Description: "Запустить Compass"; Flags: nowait postinstall skipifsilent
