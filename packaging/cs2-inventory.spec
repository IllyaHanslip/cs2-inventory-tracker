# PyInstaller recipe for the one-file Windows app.
#   pip install -r requirements-build.txt
#   pyinstaller packaging/cs2-inventory.spec --noconfirm
# The result is dist/CS2-Inventory-Intelligence.exe. Your data goes to
# %LOCALAPPDATA%\CS2 Inventory Intelligence (see config.py), never inside the .exe.
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "templates"), "templates"), (str(ROOT / "static"), "static")],
    excludes=["pytest", "tkinter", "matplotlib", "IPython", "notebook", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CS2-Inventory-Intelligence",
    icon=str(ROOT / "static" / "icon.ico"),
    console=True,          # the window shows the app is running; close it to stop
    upx=False,
)
