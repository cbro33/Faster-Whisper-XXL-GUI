import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

if "PyQt6" not in sys.modules:
    qtcore_stub = types.SimpleNamespace(Qt=types.SimpleNamespace())
    pyqt6_stub = types.SimpleNamespace(QtCore=qtcore_stub)
    sys.modules["PyQt6"] = pyqt6_stub
    sys.modules["PyQt6.QtCore"] = qtcore_stub

try:
    import yt_dlp  # noqa: F401
except Exception:
    yt_stub = types.SimpleNamespace(__file__=__file__, __version__="0.0.0")
    sys.modules["yt_dlp"] = yt_stub
