; Inno Setup script for AutoCut Pro.
; Compiled by build.ps1 with ISCC.exe; packages dist\AutoCutPro into a normal
; "click Next to install" Windows setup with Desktop / Start Menu shortcuts.

#define MyAppName "AutoCut Pro"
#define MyAppVersion "1.2"
#define MyAppPublisher "Kapoo"
#define MyAppExe "AutoCutPro.exe"

[Setup]
AppId={{8F3B2C10-AC07-4E2B-9F2A-AUTOCUTPRO01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AutoCut Pro
DefaultGroupName=AutoCut Pro
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=AutoCutPro-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}

; Clear any OTA update overlay so a fresh install always runs the bundled
; version (otherwise a previously downloaded patch would still load).
[InstallDelete]
Type: filesandordirs; Name: "{localappdata}\AutoCutPro\app_update"

[Files]
Source: "..\dist\AutoCutPro\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\AutoCut Pro"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\ถอนการติดตั้ง AutoCut Pro"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AutoCut Pro"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "สร้างไอคอนบนเดสก์ท็อป (Create a desktop shortcut)"; GroupDescription: "ทางลัด:"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "เปิด AutoCut Pro ทันที (Launch AutoCut Pro)"; Flags: nowait postinstall skipifsilent
