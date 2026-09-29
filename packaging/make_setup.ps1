# Собирает dist\CompassSetup.exe: zip папки PyInstaller и компиляция установщика.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$payload = Join-Path $root "dist\payload"
New-Item -ItemType Directory -Force -Path $payload | Out-Null
$zip = Join-Path $payload "compass-app.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
tar -a -cf $zip -C (Join-Path $root "dist") Compass
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$out = Join-Path $root "dist\CompassSetup.exe"
$src = Join-Path $root "packaging\Installer.cs"
& $csc /nologo /target:winexe /reference:System.IO.Compression.FileSystem.dll /reference:System.Windows.Forms.dll "/resource:${zip},App.zip" /out:$out $src
if ($LASTEXITCODE -ne 0) { throw "csc failed: $LASTEXITCODE" }
if (-not (Test-Path (Join-Path $root "dist\CompassSetup.exe"))) {
    throw "CompassSetup.exe was not created"
}
