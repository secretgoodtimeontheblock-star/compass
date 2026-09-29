# Распаковывает Compass в профиль пользователя и создаёт ярлык.
# Запускается установщиком из той же папки, где лежит compass-app.zip.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip = Join-Path $here "compass-app.zip"
$destParent = $env:LOCALAPPDATA
$dest = Join-Path $destParent "Compass"
if (Test-Path $dest) {
    Remove-Item $dest -Recurse -Force
}
New-Item -ItemType Directory -Path $destParent -Force | Out-Null
tar -xf $zip -C $destParent
$exe = Join-Path $dest "Compass.exe"
if (-not (Test-Path $exe)) {
    throw "В архиве нет Compass.exe"
}
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @("Desktop", "Programs")) {
    $dir = [Environment]::GetFolderPath($folder)
    if ($folder -eq "Programs") {
        $menu = Join-Path $dir "Compass"
        New-Item -ItemType Directory -Path $menu -Force | Out-Null
        $dir = $menu
    }
    $link = $shell.CreateShortcut((Join-Path $dir "Compass.lnk"))
    $link.TargetPath = $exe
    $link.WorkingDirectory = $dest
    $link.Description = "Compass"
    $link.Save()
}
Start-Process $exe
