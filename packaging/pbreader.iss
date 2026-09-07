; Установщик PbReader (Inno Setup 6).
;
; Собирается ПОСЛЕ PyInstaller: берёт готовый каталог dist\PbReader и
; заворачивает его в обычный setup.exe — с ярлыком в меню «Пуск», записью в
; «Программы и компоненты» и удалением.
;
; Сборка целиком: powershell -File packaging\build.ps1

#define AppName      "PbReader"
#define AppPublisher "PrintBox"
#define AppExe       "PbReader.exe"
#define AppVersion   GetEnv('PBREADER_VERSION')
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif

[Setup]
; AppId менять нельзя: по нему Windows опознаёт установленную программу и
; обновляет её поверх, а не ставит второй копией.
AppId={{CF30C9D4-B088-592C-B907-A1B9B95E790A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=PbReader-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
; Программа 64-разрядная: PDFium и pywin32 ставятся под разрядность Python.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Права администратора нужны только для записи в Program Files.
PrivilegesRequired=admin
WizardStyle=modern
DisableProgramGroupPage=yes
SetupIconFile=pbreader.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"; GroupDescription: "Дополнительно:"
; По умолчанию ВЫКЛЮЧЕНО: обычно сервис поднимает основной проект сам, и второй
; экземпляр занял бы порт. Включать только для отдельно стоящего аппарата.
Name: "autostart"; Description: "Запускать сервис предпросмотра при входе в систему"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\PbReader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "pbreader.example.json"; DestDir: "{app}"; DestName: "pbreader.example.json"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Журнал PbReader"; Filename: "{localappdata}\PbReader"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; Автозапуск идёт через оконный exe: у него нет консоли, и чёрное окно не
; мелькнёт поверх интерфейса киоска при входе в систему.
Name: "{userstartup}\{#AppName} (сервис)"; Filename: "{app}\{#AppExe}"; Parameters: "serve"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Открыть {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Настройки и журнал пользователя не трогаем — только то, что создали сами.
Type: filesandordirs; Name: "{app}\_internal"
