#ifndef Edition
  #define Edition "stock"
#endif
#ifndef AppVersion
  #error Pass /DAppVersion=<version>; build-windows.ps1 reads it from transcript_desk/__init__.py.
#endif

[Setup]
AppId={{0E663C1D-B405-4CB9-90C3-3A9102E2F6A4}
AppName=Marcadores de Paula
AppVersion={#AppVersion}
AppPublisher=Marcadores de Paula
DefaultDirName={localappdata}\Programs\Marcadores de Paula
DefaultGroupName=Marcadores de Paula
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=MarcadoresDePaula-Setup-{#AppVersion}-{#Edition}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MarcadoresDePaula.exe

[Files]
Source: "..\dist\MarcadoresDePaula\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
#if Edition != "stock"
Type: files; Name: "{app}\_internal\stock-default"
#endif
Type: files; Name: "{app}\TranscriptDesk.exe"
Type: files; Name: "{userprograms}\Transcript Desk\Transcript Desk.lnk"
Type: files; Name: "{userdesktop}\Transcript Desk.lnk"

[Icons]
Name: "{group}\Marcadores de Paula"; Filename: "{app}\MarcadoresDePaula.exe"
Name: "{autodesktop}\Marcadores de Paula"; Filename: "{app}\MarcadoresDePaula.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MarcadoresDePaula.exe"; Description: "Open Marcadores de Paula"; Flags: nowait postinstall skipifsilent
