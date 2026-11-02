#ifndef AppVersion
  #define AppVersion "1.68.1"
#endif
[Setup]
AppId={{3846C087-2D0A-4D6B-8565-C0440479351D}
AppName=PA Agent Local
AppVersion={#AppVersion}
AppPublisher=PA Agent contributors
AppPublisherURL=https://github.com/williamjjj/PA_Agent
DefaultDirName={localappdata}\Programs\PA Agent
DefaultGroupName=PA Agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=PA-Agent-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=yes
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\PA-Agent.exe

[Files]
Source: "..\dist\PA-Agent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "portable.flag,data\*"

[Icons]
Name: "{group}\PA Agent"; Filename: "{app}\PA-Agent.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\PA Agent"; Filename: "{app}\PA-Agent.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 PA Agent"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\pa-agent-cli.exe"; Parameters: "integrate --target all"; Flags: runhidden waituntilterminated
Filename: "{app}\PA-Agent.exe"; Description: "启动 PA Agent"; Flags: nowait postinstall skipifsilent
