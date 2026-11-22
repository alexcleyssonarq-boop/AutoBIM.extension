; Script Inno Setup 6 para gerar instalador .exe tradicional do AutoBIM (pyRevit)
; Instale o Inno Setup 6 (https://jrsoftware.org/isdl.php) e compile este arquivo.

#define MyAppName "AutoBIM para Revit (pyRevit)"
#define MyAppVersion "1.0.4"
#define MyAppPublisher "Alex Cleysson"
#define MyAppURL "https://github.com/alexcleyssonarq-boop/AutoBIM.extension"

[Setup]
AppId={{8D6B4F11-2E1A-49D1-A53B-9940AUTOBIM01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={userappdata}\pyRevit\Extensions\AutoBIM.extension
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=AutoBIM_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".git\*,.git,.github\*,.github,__pycache__\*,*\__pycache__\*,_cache_rfa\*,*_cache_rfa\*,*_settings.json,_AutoBIM_PeDireito_SP.txt,installer\Output\*,*.exe,*.zip,*.pyc"

[Code]
var
  PyRevitVersionFound: string;
  PyRevitLocFound: string;
  PyRevitDetected: Boolean;

function ParseVersionTuple(const VerStr: string; var Maj, Min, Pat: Integer): Boolean;
var
  S, Token: string;
  P, Val: Integer;
begin
  Result := False;
  Maj := 0; Min := 0; Pat := 0;
  S := Trim(VerStr);
  if S = '' then Exit;

  P := Pos('.', S);
  if P = 0 then Exit;
  Token := Copy(S, 1, P - 1);
  Maj := StrToIntDef(Token, -1);
  if Maj < 0 then Exit;
  Delete(S, 1, P);

  P := Pos('.', S);
  if P = 0 then
  begin
    Min := StrToIntDef(S, 0);
    Result := True;
    Exit;
  end;
  Min := StrToIntDef(Copy(S, 1, P - 1), 0);
  Delete(S, 1, P);

  P := Pos('.', S);
  if P > 0 then
    Token := Copy(S, 1, P - 1)
  else
    Token := S;
  Pat := StrToIntDef(Token, 0);
  Val := Maj * 10000 + Min * 100 + Pat;
  Result := (Val >= 0);
end;

function CompareSemVer(MajA, MinA, PatA, MajB, MinB, PatB: Integer): Integer;
begin
  if MajA <> MajB then begin if MajA < MajB then Result := -1 else Result := 1; Exit; end;
  if MinA <> MinB then begin if MinA < MinB then Result := -1 else Result := 1; Exit; end;
  if PatA <> PatB then begin if PatA < PatB then Result := -1 else Result := 1; Exit; end;
  Result := 0;
end;

function ScanUninstallKey(RootKey: Integer; const BasePath: string): Boolean;
var
  Subkeys: TArrayOfString;
  I: Integer;
  KeyPath, DispName, DispVer, InstLoc: string;
begin
  Result := False;
  if not RegGetSubkeyNames(RootKey, BasePath, Subkeys) then Exit;
  for I := 0 to GetArrayLength(Subkeys) - 1 do
  begin
    KeyPath := BasePath + '\' + Subkeys[I];
    if RegQueryStringValue(RootKey, KeyPath, 'DisplayName', DispName) then
    begin
      if Pos('pyrevit', Lowercase(DispName)) > 0 then
      begin
        PyRevitDetected := True;
        RegQueryStringValue(RootKey, KeyPath, 'DisplayVersion', DispVer);
        RegQueryStringValue(RootKey, KeyPath, 'InstallLocation', InstLoc);
        PyRevitVersionFound := Trim(DispVer);
        PyRevitLocFound := Trim(InstLoc);
        Result := True;
        Exit;
      end;
    end;
  end;
end;

function HasRevit2025OrNewer(): Boolean;
begin
  Result := DirExists(ExpandConstant('{commonappdata}\Autodesk\Revit\Addins\2025'))
         or DirExists(ExpandConstant('{commonappdata}\Autodesk\Revit\Addins\2026'))
         or DirExists(ExpandConstant('{userappdata}\Autodesk\Revit\Addins\2025'))
         or DirExists(ExpandConstant('{userappdata}\Autodesk\Revit\Addins\2026'));
end;

function InitializeSetup(): Boolean;
var
  Maj, Min, Pat: Integer;
  UninstPath, VerMsg: string;
  HasModernRevit: Boolean;
begin
  PyRevitDetected := False;
  PyRevitVersionFound := '';
  PyRevitLocFound := '';
  UninstPath := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall';

  if not ScanUninstallKey(HKCU, UninstPath) then
    if not ScanUninstallKey(HKLM, UninstPath) then
      ScanUninstallKey(HKLM32, UninstPath);

  if not PyRevitDetected then
  begin
    PyRevitDetected := DirExists(ExpandConstant('{userappdata}\pyRevit-Master'))
                    or DirExists(ExpandConstant('{commonpf}\pyRevit-Master'))
                    or DirExists(ExpandConstant('{userappdata}\pyRevit'));
  end;

  if not PyRevitDetected then
  begin
    if MsgBox('O pyRevit não foi detectado neste computador.' #13#10 #13#10 'É necessário ter o pyRevit instalado para usar o AutoBIM no Revit (https://github.com/pyrevitlabs/pyRevit/releases).' #13#10 #13#10 'Deseja continuar a instalação mesmo assim?', mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
    Result := True;
    Exit;
  end;

  if PyRevitVersionFound <> '' then
    VerMsg := 'v' + PyRevitVersionFound
  else
    VerMsg := '(versão não identificada no registro)';

  HasModernRevit := HasRevit2025OrNewer();

  if ParseVersionTuple(PyRevitVersionFound, Maj, Min, Pat) then
  begin
    if CompareSemVer(Maj, Min, Pat, 4, 8, 12) < 0 then
    begin
      if MsgBox('Foi detectada uma versão antiga do pyRevit (' + VerMsg + ').' #13#10 'O AutoBIM recomenda pyRevit v4.8.12 ou superior.' #13#10 #13#10 'Deseja continuar a instalação mesmo assim?', mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
    end
    else if HasModernRevit and (Maj < 5) then
    begin
      if MsgBox('Revit 2025/2026 detectado com pyRevit ' + VerMsg + '.' #13#10 'Para Revit 2025/2026 recomenda-se atualizar o pyRevit para a versão 5.0 ou superior.' #13#10 #13#10 'Deseja continuar a instalação do AutoBIM mesmo assim?', mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;

  Result := True;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: string): string;
var
  S: string;
begin
  S := '';
  if PyRevitDetected then
  begin
    if PyRevitVersionFound <> '' then
      S := S + 'pyRevit detectado: v' + PyRevitVersionFound
    else
      S := S + 'pyRevit detectado: Sim';
    if PyRevitLocFound <> '' then
      S := S + NewLine + Space + 'Local: ' + PyRevitLocFound;
    S := S + NewLine + NewLine;
  end
  else
    S := S + 'pyRevit detectado: Não encontrado (instale antes de abrir o Revit)' + NewLine + NewLine;

  if MemoDirInfo <> '' then
    S := S + MemoDirInfo + NewLine + NewLine;
  Result := S;
end;

[Messages]
FinishedLabel=O [name] foi instalado em sua pasta de extensões do pyRevit.%n%nAbra o Revit (ou clique em pyRevit -> Reload) para começar a usar.
