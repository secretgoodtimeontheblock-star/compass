# Сборка установщика Windows: интерфейс -> Compass.exe (PyInstaller) -> Compass-Setup-*.exe (Inno Setup).
# Нужны: Python 3.12, Node 22 + pnpm, Inno Setup 6 (iscc в PATH). Запуск из корня репозитория.
$ErrorActionPreference = "Stop"

Push-Location ui
pnpm install --frozen-lockfile
pnpm build
Pop-Location

python -m pip install -e ".[desktop]" pyinstaller
python -m PyInstaller packaging/compass.spec --noconfirm --distpath dist --workpath build

$version = (python -c "import compass; print(compass.__version__)").Trim()
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc "/DAppVersion=$version" packaging\compass.iss
Write-Host "Готово: dist\Compass-Setup-$version.exe"
