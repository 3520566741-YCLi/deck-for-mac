; Deck for Windows 安装包（Inno Setup 6，免费开源）。
; 先用 windows/deck.spec 打出 dist\Deck\，再：  iscc /DAppVersion=0.1.0 windows\installer.iss
; 简体中文界面文件 lang\ChineseSimplified.isl 来自 Inno Setup 官方仓库（jrsoftware/issrc，Kira 维护），固定一份放仓库里。
; 装在当前用户下（%LOCALAPPDATA%\Programs\Deck），不要管理员权限，配置文件也写得进去。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{938EC4EA-DB15-455C-AE78-566AE939E871}
AppName=Deck
AppVersion={#AppVersion}
AppPublisher=Deck
DefaultDirName={localappdata}\Programs\Deck
DefaultGroupName=Deck
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Deck-Setup-{#AppVersion}
SetupIconFile=build\deck.ico
UninstallDisplayIcon={app}\Deck.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
CloseApplications=yes

[Languages]
Name: "chs"; MessagesFile: "lang\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chs.DeskIcon=在桌面放一个 Deck 图标
en.DeskIcon=Put a Deck icon on the desktop
chs.AutoStart=开机自动启动（只放在右下角托盘）
en.AutoStart=Start with Windows (tray only)
chs.RunNow=现在打开 Deck
en.RunNow=Open Deck now

[Tasks]
Name: "desktopicon"; Description: "{cm:DeskIcon}"; Flags: unchecked
Name: "autostart"; Description: "{cm:AutoStart}"

[Files]
Source: "..\dist\Deck\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Deck"; Filename: "{app}\Deck.exe"
Name: "{userdesktop}\Deck"; Filename: "{app}\Deck.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Deck"; \
  ValueData: """{app}\Deck.exe"" --hidden"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Deck.exe"; Description: "{cm:RunNow}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM Deck.exe /F"; Flags: runhidden; RunOnceId: "KillDeck"
