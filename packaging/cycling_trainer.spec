# PyInstaller recipe: bundles Python, Qt and all libraries.
#   Windows: dist/Cycling Trainer/ (wrapped by installer.iss)
#   macOS:   dist/Cycling Trainer.app (wrapped in a .dmg by .github/workflows/build-macos.yml)
# Build with: pyinstaller --noconfirm packaging/cycling_trainer.spec
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MAC = sys.platform == "darwin"
with open(os.path.join(ROOT, "trainer", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
NUMBERS = tuple(int(n) for n in VERSION.split(".")) + (0,)

# Imported dynamically at runtime, so PyInstaller can't find them on its own.
if MAC:
    hidden = collect_submodules("bleak.backends.corebluetooth") + ["keyring.backends.macOS"]
else:
    hidden = collect_submodules("bleak.backends.winrt") + collect_submodules("winrt") + ["keyring.backends.Windows"]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "trainer", "resources"), os.path.join("trainer", "resources"))],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "PIL", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
)
pyz = PYZ(a.pure)

version_file = None
if not MAC:
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

# PyInstaller converts the PNG to .icns on macOS (needs Pillow at build time).
icon = os.path.join(ROOT, "trainer", "resources", "icon.png" if MAC else "icon.ico")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cycling Trainer",
    console=False,
    icon=icon,
    version=version_file,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Cycling Trainer")

if MAC:
    app = BUNDLE(
        coll,
        name="Cycling Trainer.app",
        icon=icon,
        bundle_identifier="io.github.ateboekhorst.cyclingtrainer",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            # Without this text macOS ends the app the moment it uses Bluetooth.
            "NSBluetoothAlwaysUsageDescription":
                "Cycling Trainer connects to your smart trainer, heart rate strap and power meter over Bluetooth.",
        },
    )
