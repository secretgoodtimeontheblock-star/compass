# PyInstaller: собирает папку dist/Compass с Compass.exe (без консоли).
# Интерфейс (src/compass/static) должен быть собран заранее: cd ui && pnpm build.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
static = root / "src" / "compass" / "static"
if not (static / "index.html").exists():
    raise SystemExit("Нет src/compass/static: сначала соберите интерфейс (cd ui && pnpm build)")

datas = [(str(static), "compass/static")] + collect_data_files("ccxt") + collect_data_files("webview")
hidden = collect_submodules("uvicorn") + collect_submodules("ccxt") + collect_submodules("webview")

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Compass", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Compass")
