; Inno Setup Script для создания инсталлятора Dotmark_Setup.exe
; Официальный установщик Dotmark · Система маркировки (Честный ЗНАК)

#define MyAppName "Dotmark"
#define MyAppFullName "Dotmark · Система маркировки"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "Dotmark Team"
#define MyAppURL "https://github.com/Anndraq/ChestZn"
#define MyAppExeName "Dotmark.exe"

[Setup]
AppId={{9C52E523-2892-4217-B5E3-72C411082AA1}
AppName={#MyAppFullName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=Dotmark_Setup
SetupIconFile=app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked
Name: "firewallrule"; Description: "Разрешить сетевой обмен с Мастер-ПК (порт TCP 8765)"; GroupDescription: "Сетевые компоненты:"; Flags: checked

[Dirs]
Name: "{userappdata}\ChestZn"
Name: "{userappdata}\ChestZn\logs"
Name: "{userappdata}\ChestZn\backups"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "brand_header.png"; DestDir: "{app}"; Flags: ignoreversion
; Если в папку redist положен VC_redist.x64.exe — он будет установлен автоматически при необходимости
Source: "redist\VC_redist.x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion skipifsourcedoesntexist deleteafterinstall

[Icons]
Name: "{autoprograms}\{#MyAppFullName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"
Name: "{autodesktop}\{#MyAppFullName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{tmp}\VC_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Установка системных библиотек Microsoft Visual C++ Redistributable..."; Flags: waituntilterminated skipifdoesntexist; Check: NeedsVCRedist
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Dotmark Sync"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes"; Flags: runhidden skipifdoesntexist; Tasks: firewallrule; Check: IsAdminInstallMode
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppFullName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsVCRedist(): Boolean;
var
  Installed: Cardinal;
begin
  Result := True;
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
  begin
    if Installed = 1 then
      Result := False;
  end;
  if Result and RegQueryDWordValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
  begin
    if Installed = 1 then
      Result := False;
  end;
end;
