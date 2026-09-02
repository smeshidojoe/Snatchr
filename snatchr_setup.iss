; ─── Snatchr Installer Script ──────────────────────────────────
; Создано для Inno Setup 6.x

#define MyAppName "Snatchr"
; Версию НЕ дублируем: берём её из уже собранного exe, а туда она попадает из
; core/constants.py (см. Snatchr.spec). Поднять версию = поправить одну строку
; в constants.py и пересобрать.
#define MyAppExe "dist\Snatchr.exe"
#if !FileExists(AddBackslash(SourcePath) + MyAppExe)
  #error Сначала соберите exe: pyinstaller --clean --noconfirm Snatchr.spec
#endif
#define MyAppVersion GetStringFileInfo(AddBackslash(SourcePath) + MyAppExe, PRODUCT_VERSION)
#if MyAppVersion == ""
  #error В exe нет ресурса версии. Пересоберите его текущей Snatchr.spec.
#endif
#define MyAppExeName "Snatchr.exe"
#define MyAppPublisher "SmeshidoJoe"

[Setup]
AppId={{72713E8B-B190-4DDE-8545-47025B4A4703}} 
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Путь по умолчанию — папка программ ТЕКУЩЕГО пользователя. При
; PrivilegesRequired=lowest это %LOCALAPPDATA%\Programs: туда можно писать без
; прав администратора, а это обязательное условие — Snatchr обновляет себя сам,
; подменяя exe на месте. В Program Files подмена молча не пройдёт.
; Раньше здесь был жёстко прописан D:\Programs — на машине без диска D установка
; спотыкалась на первом же шаге.
DefaultDirName={autopf}\{#MyAppName}
; Страницу выбора папки показываем всегда. По умолчанию (auto) она пряталась при
; установке поверх уже существующей.
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Snatchr-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Code]
// Проверяем, что в выбранную папку можно писать БЕЗ прав администратора.
// Без этого пользователь выберет Program Files, установка пройдёт (или упадёт с
// невнятной ошибкой), а самообновление потом будет молча отказывать.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Probe: string;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  ForceDirectories(WizardDirValue);
  Probe := AddBackslash(WizardDirValue) + 'snatchr_write_test.tmp';
  if SaveStringToFile(Probe, 'x', False) then
    DeleteFile(Probe)
  else begin
    Result := False;
    MsgBox(ExpandConstant('{cm:DirNotWritable}'), mbError, MB_OK);
  end;
end;

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
english.DirNotWritable=This folder cannot be written to without administrator rights.%n%nSnatchr updates itself and needs write access to its own folder, so please pick another location — for example the default one.
russian.DirNotWritable=В эту папку нельзя записывать без прав администратора.%n%nSnatchr обновляется самостоятельно и должен иметь доступ на запись в свою папку, поэтому выберите другое место — например, предложенное по умолчанию.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent