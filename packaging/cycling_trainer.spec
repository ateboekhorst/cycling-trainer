# PyInstaller recipe: bundles Python, Qt and all libraries into dist/Cycling Trainer/.
# Build with build_installer.ps1 (or: pyinstaller --noconfirm packaging/cycling_trainer.spec).
import os
import re

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
with open(os.path.join(ROOT, "trainer", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
NUMBERS = tuple(int(n) for n in VERSION.split(".")) + (0,)

# Imported dynamically at runtime, so PyInstaller can't find them on its own.
hidden = (
    collect_submodules("bleak.backends.winrt")
    + collect_submodules("winrt")
    + ["keyring.backends.Windows"]
)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "trainer", "resources"), os.path.join("trainer", "resources"))],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "PIL", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
)
pyz = PYZ(a.pure)

version_file = os.path.join(workpath, "version_info.txt")
os.makedirs(workpath, exist_ok=True)
with open(version_file, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={NUMBERS}, prodvers={NUMBERS}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Cycling Trainer'),
      StringStruct('FileDescription', 'Cycling Trainer'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('ProductVersion', '{VERSION}'),
      StringStruct('OriginalFilename', 'Cycling Trainer.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)""")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cycling Trainer",
    console=False,
    icon=os.path.join(ROOT, "trainer", "resources", "icon.ico"),
    version=version_file,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Cycling Trainer")
