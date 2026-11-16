@echo off
chcp 65001 >nul
title Instalador AutoBIM (pyRevit)
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ErrorActionPreference = 'Stop'; ^
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ^
Write-Host '======================================================' -ForegroundColor Cyan; ^
Write-Host '        INSTALADOR AUTOBIM PARA REVIT (pyRevit)       ' -ForegroundColor Cyan; ^
Write-Host '======================================================' -ForegroundColor Cyan; ^
Write-Host ''; ^
function Get-PyRevitInfo { ^
    $verStr = $null; $instLoc = $null; ^
    $regPaths = @( ^
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', ^
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', ^
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' ^
    ); ^
    foreach ($rp in $regPaths) { ^
        $hit = Get-ItemProperty $rp -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like '*pyRevit*' } | Select-Object -First 1; ^
        if ($hit) { ^
            if ($hit.DisplayVersion) { $verStr = [string]$hit.DisplayVersion }; ^
            if ($hit.InstallLocation) { $instLoc = [string]$hit.InstallLocation }; ^
            break ^
        } ^
    }; ^
    if (-not $verStr -and (Get-Command pyrevit -ErrorAction SilentlyContinue)) { ^
        try { ^
            $rawV = (& pyrevit --version 2>$null | Select-Object -First 1); ^
            if ($rawV -match '(\d+\.\d+\.\d+(?:\.\d+)?)') { $verStr = $Matches[1] } ^
        } catch {} ^
    }; ^
    if (-not $verStr) { ^
        foreach ($cand in @(\"$env:APPDATA\pyRevit-Master\", \"$env:ProgramFiles\pyRevit-Master\", \"${env:ProgramFiles(x86)}\pyRevit-Master\", \"$env:LOCALAPPDATA\Programs\pyRevit-Master\")) { ^
            $vf = Join-Path $cand 'pyrevitlib\pyrevit\version'; ^
            if (Test-Path $vf) { ^
                $instLoc = $cand; ^
                $rawF = (Get-Content $vf -Raw -ErrorAction SilentlyContinue); ^
                if ($rawF -match '(\d+\.\d+\.\d+(?:\.\d+)?)') { $verStr = $Matches[1] }; ^
                break ^
            } ^
        } ^
    }; ^
    $hasAddin = $false; ^
    $revitVers = @(); ^
    foreach ($adRoot in @(\"$env:ProgramData\Autodesk\Revit\Addins\", \"$env:APPDATA\Autodesk\Revit\Addins\")) { ^
        if (Test-Path $adRoot) { ^
            Get-ChildItem $adRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object { ^
                if ($_.Name -match '^\d{4}$') { $revitVers += [int]$_.Name }; ^
                if (Test-Path (Join-Path $_.FullName 'pyRevit.addin')) { $hasAddin = $true } ^
            } ^
        } ^
    }; ^
    $parsedVer = $null; ^
    if ($verStr -and ($verStr -match '^(\d+)\.(\d+)\.(\d+)')) { ^
        $parsedVer = [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3]) ^
    }; ^
    return [pscustomobject]@{ ^
        Found = ($null -ne $verStr -or $hasAddin -or $null -ne $instLoc); ^
        VersionText = $verStr; ^
        ParsedVersion = $parsedVer; ^
        InstallLocation = $instLoc; ^
        RevitYears = ($revitVers | Select-Object -Unique | Sort-Object) ^
    } ^
}; ^
$pyInfo = Get-PyRevitInfo; ^
$minPyRevit = [version]'4.8.12'; ^
if (-not $pyInfo.Found) { ^
    Write-Host '[AVISO] pyRevit nao foi detectado neste computador.' -ForegroundColor Yellow; ^
    Write-Host 'Instale primeiro o pyRevit em: https://github.com/pyrevitlabs/pyRevit/releases' -ForegroundColor Yellow; ^
    $ans = Read-Host 'Deseja continuar a instalacao mesmo assim? (S/N)'; ^
    if ($ans -notin @('S','s','Y','y')) { exit 1 } ^
} else { ^
    $vLabel = if ($pyInfo.VersionText) { 'v' + $pyInfo.VersionText } else { '(versao nao informada)' }; ^
    Write-Host \"[OK] pyRevit detectado: $vLabel\" -ForegroundColor Green; ^
    if ($pyInfo.InstallLocation) { Write-Host \"     Local: $($pyInfo.InstallLocation)\" -ForegroundColor Gray }; ^
    $maxRevit = if ($pyInfo.RevitYears.Count -gt 0) { ($pyInfo.RevitYears | Measure-Object -Maximum).Maximum } else { 0 }; ^
    if ($pyInfo.ParsedVersion) { ^
        if ($pyInfo.ParsedVersion -lt $minPyRevit) { ^
            Write-Host \"[AVISO] Versao antiga do pyRevit detectada ($vLabel). Recomendado >= v4.8.12.\" -ForegroundColor Yellow; ^
            $ans = Read-Host 'Continuar instalacao mesmo assim? (S/N)'; ^
            if ($ans -notin @('S','s','Y','y')) { exit 1 } ^
        } elseif ($maxRevit -ge 2025 -and $pyInfo.ParsedVersion.Major -lt 5) { ^
            Write-Host \"[AVISO] Revit $maxRevit exige pyRevit v5.0 ou superior (atual: $vLabel).\" -ForegroundColor Yellow; ^
            Write-Host 'Recomendado atualizar o pyRevit em: https://github.com/pyrevitlabs/pyRevit/releases' -ForegroundColor Yellow; ^
            $ans = Read-Host 'Continuar instalacao mesmo assim? (S/N)'; ^
            if ($ans -notin @('S','s','Y','y')) { exit 1 } ^
        } else { ^
            Write-Host '[OK] Versao do pyRevit compativel com o AutoBIM.' -ForegroundColor Green ^
        } ^
    } ^
}; ^
$extParent = Join-Path $env:APPDATA 'pyRevit\Extensions'; ^
$destDir   = Join-Path $extParent 'AutoBIM.extension'; ^
New-Item -ItemType Directory -Path $extParent -Force | Out-Null; ^
$backups = @{}; ^
$snipDest = Join-Path $destDir 'lib\Snippets'; ^
if (Test-Path $snipDest) { ^
    Get-ChildItem $snipDest -File | Where-Object { $_.Name -like '*_settings.json' -or $_.Name -eq '_AutoBIM_PeDireito_SP.txt' } | ForEach-Object { ^
        $backups[$_.Name] = [IO.File]::ReadAllBytes($_.FullName) ^
    } ^
}; ^
$scriptDir = '%~dp0'.TrimEnd('\'); ^
$localParent = Split-Path -Parent $scriptDir; ^
$localSrc = $null; ^
if (Test-Path (Join-Path $scriptDir 'AutoBIM.tab')) { $localSrc = $scriptDir } ^
elseif (Test-Path (Join-Path $localParent 'AutoBIM.tab')) { $localSrc = $localParent }; ^
$tempRoot = Join-Path $env:TEMP ('AutoBIM_Inst_' + [guid]::NewGuid().ToString('N').Substring(0,8)); ^
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null; ^
try { ^
    $srcUse = $localSrc; ^
    if (-not $srcUse) { ^
        $zipUrl = 'https://github.com/alexcleyssonarq-boop/AutoBIM.extension/archive/refs/heads/main.zip'; ^
        $zipFile = Join-Path $tempRoot 'autobim.zip'; ^
        $extTemp = Join-Path $tempRoot 'ex'; ^
        Write-Host '[*] Baixando ultima versao do GitHub...' -ForegroundColor White; ^
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing; ^
        Expand-Archive -Path $zipFile -DestinationPath $extTemp -Force; ^
        $found = Get-ChildItem $extTemp -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'AutoBIM.tab') } | Select-Object -First 1; ^
        if (-not $found) { throw 'Pasta AutoBIM.tab nao encontrada no pacote baixado.' }; ^
        $srcUse = $found.FullName; ^
    } else { ^
        Write-Host \"[*] Copiando arquivos locais de: $srcUse\" -ForegroundColor White; ^
    }; ^
    if ((Resolve-Path $srcUse).Path.TrimEnd('\') -eq $destDir.TrimEnd('\')) { ^
        Write-Host '[OK] O AutoBIM ja esta instalado nesta pasta.' -ForegroundColor Green ^
    } else { ^
        if (Test-Path $destDir) { Remove-Item -Path $destDir -Recurse -Force }; ^
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null; ^
        Get-ChildItem -Path $srcUse -Force | Where-Object { $_.Name -notin @('.git','dist') } | ForEach-Object { ^
            Copy-Item -Path $_.FullName -Destination $destDir -Recurse -Force ^
        }; ^
        Get-ChildItem -Path $destDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; ^
        $cacheRfa = Join-Path $destDir 'lib\Snippets\_cache_rfa'; ^
        if (Test-Path $cacheRfa) { Remove-Item $cacheRfa -Recurse -Force -ErrorAction SilentlyContinue }; ^
        if ($backups.Count -gt 0) { ^
            New-Item -ItemType Directory -Path $snipDest -Force | Out-Null; ^
            foreach ($k in $backups.Keys) { [IO.File]::WriteAllBytes((Join-Path $snipDest $k), $backups[$k]) } ^
        }; ^
    }; ^
    if (Get-Command pyrevit -ErrorAction SilentlyContinue) { ^
        try { & pyrevit extensions paths add $extParent 2>$null | Out-Null } catch {} ^
    }; ^
    $verTxt = '1.0.0'; ^
    $vj = Join-Path $destDir 'version.json'; ^
    if (Test-Path $vj) { try { $verTxt = (Get-Content $vj -Raw | ConvertFrom-Json).version } catch {} }; ^
    Write-Host ''; ^
    Write-Host \"[SUCESSO] AutoBIM v$verTxt instalado em:\" -ForegroundColor Green; ^
    Write-Host \"          $destDir\" -ForegroundColor Gray; ^
    Write-Host ''; ^
    Write-Host 'Proximo passo: Abra o Revit (ou clique em pyRevit -> Reload).' -ForegroundColor Cyan; ^
} finally { ^
    if (Test-Path $tempRoot) { Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue } ^
}"

echo.
pause
