@echo off
chcp 65001 >nul
title Lancar Nova Versao - AutoBIM (GitHub)
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ErrorActionPreference = 'Stop'; ^
$root = Split-Path -Parent '%~dp0'.TrimEnd('\'); ^
Set-Location $root; ^
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { ^
    throw 'Git nao encontrado no PATH. Reinicie o terminal ou verifique a instalacao do Git for Windows.' ^
}; ^
$vjPath = Join-Path $root 'version.json'; ^
$vj = Get-Content $vjPath -Raw -Encoding UTF8 | ConvertFrom-Json; ^
Write-Host (\"Versao atual: \" + $vj.version) -ForegroundColor Cyan; ^
$nova = Read-Host 'Digite a nova versao (ex: 1.0.1)'; ^
if (-not $nova) { throw 'Versao obrigatoria.' }; ^
$nova = $nova.Trim().TrimStart('v','V'); ^
$notas = Read-Host 'Resumo do que mudou nesta versao'; ^
if (-not $notas) { $notas = 'Melhorias e correcoes gerais.' }; ^
$vj.version = $nova; ^
$vj.release_date = (Get-Date).ToString('yyyy-MM-dd'); ^
$vj.notes = $notas; ^
$jsonOut = $vj | ConvertTo-Json -Depth 4; ^
[IO.File]::WriteAllText($vjPath, $jsonOut + \"`r`n\", [System.Text.UTF8Encoding]::new($false)); ^
$issPath = Join-Path $root 'installer\AutoBIM_Setup.iss'; ^
if (Test-Path $issPath) { ^
    $iss = Get-Content $issPath -Raw -Encoding UTF8; ^
    $iss = $iss -replace '#define MyAppVersion \".*?\"', \"#define MyAppVersion `\"$nova`\"\"; ^
    [IO.File]::WriteAllText($issPath, $iss, [System.Text.UTF8Encoding]::new($false)); ^
}; ^
foreach ($ic in @('G:\Arquivos de Programas\Inno Setup 7\ISCC.exe', 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe', 'C:\Program Files\Inno Setup 7\ISCC.exe')) { ^
    if (Test-Path $ic) { Write-Host '[*] Gerando EXE local...' -ForegroundColor White; & $ic $issPath | Out-Null; break } ^
}; ^
& git add -A; ^
& git commit -m \"Release v$nova - $notas\"; ^
& git tag -a \"v$nova\" -m \"AutoBIM v$nova - $notas\"; ^
& git push origin main --tags; ^
Write-Host ''; ^
Write-Host \"[OK] Versao v$nova enviada para o GitHub! Os usuarios receberao o aviso automaticamente.\" -ForegroundColor Green"

echo.
pause
