; PDF Küçült — Windows kurulum dosyası (Inno Setup 6)
; Yönetici yetkisi istemez; kullanıcının kendi klasörüne kurulur.
#define Surum GetEnv("SURUM")
#if Surum == ""
  #define Surum "1.0.0"
#endif

[Setup]
AppId={{6C1F0E52-4E9B-4B1E-9A7C-5D2F1A0C8B11}
AppName=PDF Küçült
AppVersion={#Surum}
AppPublisher=PDF Küçült
DefaultDirName={localappdata}\Programs\PDF Kucult
DefaultGroupName=PDF Küçült
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\derleme-win\kurulum
OutputBaseFilename=PDF-Kucult-Kurulum
SetupIconFile=..\derleme-win\simge.ico
UninstallDisplayIcon={app}\PDF Kucult.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Files]
Source: "..\derleme-win\dist\PDF Kucult\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\PDF Küçült Zotero.xpi"; DestDir: "{app}\Zotero eklentisi"; Flags: ignoreversion
Source: "..\Kurulum Rehberi.html"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\PDF Küçült"; Filename: "{app}\PDF Kucult.exe"
Name: "{group}\PDF Küçült"; Filename: "{app}\PDF Kucult.exe"
Name: "{group}\Zotero eklentisi klasörü"; Filename: "{app}\Zotero eklentisi"
Name: "{group}\Kurulum Rehberi"; Filename: "{app}\Kurulum Rehberi.html"
; Sağ tık › Gönder › PDF Küçült
Name: "{usersendto}\PDF Küçült"; Filename: "{app}\PDF Kucult.exe"

[Run]
Filename: "{app}\PDF Kucult.exe"; Description: "PDF Küçült'ü şimdi aç"; Flags: nowait postinstall skipifsilent
