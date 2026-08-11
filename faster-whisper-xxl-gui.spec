# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/faster-whisper-xxl-gui.py'],
    pathex=[],
    binaries=[],
    datas=[('resources', 'resources')],
    hiddenimports=[
        'yt_dlp', 
        'yt_dlp.extractor', 
        'requests', 
        'tempfile', 
        'subprocess', 
        'threading',
        'webbrowser',
        'pathlib',
        'logging',
        'json',
        'platform',
        'shutil',
        'PyQt6.QtCore',
        'PyQt6.QtGui', 
        'PyQt6.QtWidgets',
        'py7zr'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The app only uses QtCore/QtGui/QtWidgets, but PyInstaller bundles every
    # Qt module it can find. Trimming these keeps the exe under the 50 MB limit
    # of Microsoft's false-positive submission portal (see issue #21) and cuts
    # the download size. Deliberately conservative: QtNetwork, QtSvg, QtOpenGL
    # and QtPrintSupport are left in because QtWidgets can pull them in
    # indirectly. torch/pynvml are optional GPU-detection imports guarded by
    # `except ImportError` in gpu_utils.py, and are not bundled today anyway.
    excludes=[
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineQuick',
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuick3D', 'PyQt6.QtQuickWidgets',
        'PyQt6.Qt3DCore', 'PyQt6.Qt3DRender', 'PyQt6.Qt3DExtras',
        'PyQt6.Qt3DInput', 'PyQt6.Qt3DLogic', 'PyQt6.Qt3DAnimation',
        'PyQt6.QtCharts', 'PyQt6.QtDataVisualization',
        'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtPositioning', 'PyQt6.QtLocation',
        'PyQt6.QtSerialPort', 'PyQt6.QtWebSockets', 'PyQt6.QtWebChannel',
        'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtDesigner', 'PyQt6.QtHelp',
        'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
        'tkinter',
        'torch', 'torchvision', 'torchaudio',
        'numpy', 'scipy', 'pandas', 'matplotlib', 'PIL',
        'IPython', 'jupyter', 'notebook', 'pytest', '_pytest',
    ],
    noarchive=False,
    optimize=0,
)
# The `excludes` above only reach Python imports. These DLLs arrive underneath
# them as Qt dependencies, and nothing in the bundle imports any of them, read
# off the PE import tables of a shipped build. opengl32sw is the 20 MB software
# OpenGL fallback that qwindows.dll loads only if a GL context is asked for, and
# a widgets-only app never asks. Qt6Pdf exists for the PDF image format plugin,
# and Qt6Network is left with no users at all once that and the TUIO touch
# plugin are gone.
UNUSED_BINARIES = {
    'opengl32sw.dll',
    'qt6pdf.dll',
    'qpdf.dll',
    'qt6network.dll',
    'qtuiotouchplugin.dll',
}
a.binaries = [
    entry for entry in a.binaries
    if entry[0].replace('\\', '/').rsplit('/', 1)[-1].lower() not in UNUSED_BINARIES
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='faster-whisper-xxl-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Keep UPX off. Packed executables are one of the strongest antivirus
    # heuristic triggers, and this build is unsigned, so it has no signature to
    # offset that. Builds so far were unpacked only because UPX happened not to
    # be installed -- this makes it deliberate. See issue #21.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
