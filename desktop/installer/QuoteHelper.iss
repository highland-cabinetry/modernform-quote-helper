; Modernform Quote Helper Windows installer (Inno Setup 6).
;
; Built by .github/workflows/desktop.yml:
;   ISCC /DAppVersion=3.1.0 /DSourceDir=<build\desktop> /DOutputDir=<build\installer> QuoteHelper.iss
;
; Code signing (optional): add /DSign "/Sqhsign=<signtool command ending in $f>". The installer and
; its uninstaller are then signed while they are built (docs/DEPLOYMENT.md, "Code signing").
;
; Later versions are normally installed with an update package (ModernformQuoteHelperUpdate-<version>.zip,
; applied by the launcher without running any installer). This installer is for the first
; installation and for versions that change the bundled Python runtime.
;
; Installs per user (no administrator rights) into %LOCALAPPDATA%\Programs\Modernform Quote Helper.
; Each update replaces that folder's program files only. The data folder (database, imported
; documents, backups) and %LOCALAPPDATA%\ModernformQuoteHelper\config.json are never changed by an
; update or removed by uninstalling.

#ifndef AppVersion
  #error Define AppVersion, for example /DAppVersion=3.1.0
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\desktop"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\installer"
#endif

[Setup]
; Never change AppId: it is how an update finds the installed copy.
AppId={{F790B45B-66A3-4094-B723-F2534323B0D7}
AppName=Modernform Quote Helper
AppVersion={#AppVersion}
AppVerName=Modernform Quote Helper {#AppVersion}
AppPublisher=ModernForm Cabinets & Closet
DefaultDirName={autopf}\Modernform Quote Helper
DefaultGroupName=Modernform Quote Helper
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=ModernformQuoteHelperSetup-{#AppVersion}
SetupIconFile=..\quote-helper.ico
UninstallDisplayIcon={app}\desktop\quote-helper.ico
UninstallDisplayName=Modernform Quote Helper
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
InfoAfterFile=after-install.txt
SetupLogging=yes
#ifdef Sign
SignTool=qhsign
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Remove the previous version's program files first, so no stale code is left behind.
; Only these program folders are cleared; the data folder is elsewhere.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\desktop"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\bridge"
; Left by update packages: the version an update replaced (its runtime may differ from this one), and scratch.
Type: filesandordirs; Name: "{app}\previous"
Type: filesandordirs; Name: "{app}\update-work"

[Dirs]
; Where update packages can be dropped. Kept when uninstalling, like config.json next to it.
Name: "{localappdata}\ModernformQuoteHelper\updates"; Flags: uninsneveruninstall

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Modernform Quote Helper"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"; Comment: "Freight quotes and pallet plans"
Name: "{autodesktop}\Modernform Quote Helper"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"; Comment: "Freight quotes and pallet plans"; Tasks: desktopicon
Name: "{autoprograms}\Modernform Quote Helper Tools\Stop Modernform Quote Helper"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"
Name: "{autoprograms}\Modernform Quote Helper Tools\Modernform Quote Helper data folder"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --open-data"; WorkingDir: "{app}"
Name: "{autoprograms}\Modernform Quote Helper Tools\Build2Quote Bridge folder"; Filename: "{app}\bridge"
Name: "{autoprograms}\Modernform Quote Helper Tools\Check for updates"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --check-updates"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"
Name: "{autoprograms}\Modernform Quote Helper Tools\Install update file"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --apply-update"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"
Name: "{autoprograms}\Modernform Quote Helper Tools\Go back to the previous version"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --rollback-update"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\quote-helper.ico"
Name: "{autoprograms}\Modernform Quote Helper Tools\Updates folder"; Filename: "{localappdata}\ModernformQuoteHelper\updates"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"""; WorkingDir: "{app}"; Description: "Open Modernform Quote Helper"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\desktop\launcher.pyw"" --stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopModernformQuoteHelper"

[UninstallDelete]
; Python writes caches next to the code; remove the program folders entirely. Data is elsewhere.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\desktop"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\bridge"
Type: filesandordirs; Name: "{app}\previous"
Type: filesandordirs; Name: "{app}\update-work"

[Code]
var
  DataPage: TInputDirWizardPage;

function ConfigFile: String;
begin
  Result := ExpandConstant('{localappdata}\ModernformQuoteHelper\config.json');
end;

function HasConfig: Boolean;
begin
  Result := FileExists(ConfigFile);
end;

function DefaultDataDir: String;
begin
  Result := ExpandConstant('{param:DATADIR|}');
  if Result = '' then
    Result := ExpandConstant('{localappdata}') + '\ModernformQuoteHelper\data';
end;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir, 'Data folder', 'Where should Modernform Quote Helper keep its data?',
    'Modernform Quote Helper keeps its database, imported documents and backups in this folder. ' +
    'Updates never change or remove it.' + #13#10 + #13#10 +
    'Choose a folder that is backed up (for example in OneDrive) if you want the quote history kept safe.', False, '');
  DataPage.Add('Data folder:');
  DataPage.Values[0] := DefaultDataDir;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // An update keeps the data folder already chosen.
  Result := (PageID = DataPage.ID) and HasConfig;
end;

function InsideFolder(const Folder, Parent: String): Boolean;
begin
  Result := Pos(Lowercase(AddBackslash(Parent)), Lowercase(AddBackslash(Folder))) = 1;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DataPage.ID then
  begin
    if InsideFolder(DataPage.Values[0], WizardDirValue) then
    begin
      MsgBox('Choose a data folder outside the program folder (' + WizardDirValue + '): ' +
        'the program folder is replaced by every update.', mbError, MB_OK);
      Result := False;
    end
    else if FileExists(AddBackslash(DataPage.Values[0]) + 'quote_helper_v2.sqlite3') then
      MsgBox('An existing Modernform Quote Helper database was found in this folder. Modernform Quote Helper will keep using it as it is, ' +
        'and makes a backup copy before a new version first opens it.', mbInformation, MB_OK);
  end;
end;

function JsonString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := '"' + Value + '"';
end;

procedure WriteConfig;
var
  Lines: TArrayOfString;
  DataDir, UpdateFolder: String;
begin
  DataDir := DataPage.Values[0];
  // Optional, for deployments by IT: /UPDATEFOLDER="\\server\share\Modernform Quote Helper updates"
  UpdateFolder := ExpandConstant('{param:UPDATEFOLDER|}');
  ForceDirectories(DataDir);
  ForceDirectories(ExtractFileDir(ConfigFile));
  SetArrayLength(Lines, 4);
  Lines[0] := '{';
  Lines[1] := '  "data_dir": ' + JsonString(DataDir) + ',';
  Lines[2] := '  "port": 8030';
  Lines[3] := '}';
  if UpdateFolder <> '' then
  begin
    SetArrayLength(Lines, 5);
    Lines[2] := '  "port": 8030,';
    Lines[3] := '  "update_folder": ' + JsonString(UpdateFolder);
    Lines[4] := '}';
  end;
  if not SaveStringsToUTF8File(ConfigFile, Lines, False) then
    MsgBox('Modernform Quote Helper could not save its settings to ' + ConfigFile + '.', mbError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Python, Launcher: String;
begin
  Result := '';
  // Stop a running copy so its files can be replaced. Its database writes finish first.
  Python := ExpandConstant('{app}\runtime\pythonw.exe');
  Launcher := ExpandConstant('{app}\desktop\launcher.pyw');
  if FileExists(Python) and FileExists(Launcher) then
    Exec(Python, '"' + Launcher + '" --stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not HasConfig then
    WriteConfig;
end;
