import sys
import os
import ctypes
import logging
from PyQt6.QtWidgets import QApplication

os.environ["QT_LOGGING_RULES"] = "qt.multimedia.*=false"

# Silence FFmpeg warnings from Qt's embedded multimedia backend during import,
# then restore stderr so Python exceptions are still visible.
_devnull = os.open(os.devnull, os.O_WRONLY)
_saved_stderr = os.dup(2)
os.dup2(_devnull, 2)
from src.ui.main_window import MainWindow
os.dup2(_saved_stderr, 2)
os.close(_devnull)
os.close(_saved_stderr)


def _is_already_running() -> bool:
    """Use a Windows named mutex for single-instance enforcement.
    The OS automatically releases it when the process exits, even on crash."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW(None, True, "Global\\VideoEditorSingleInstance")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    ctypes.windll.kernel32.SetLastError(0)
    if _is_already_running():
        sys.exit(0)

    # Log to file so errors are visible even under pythonw (no console)
    from src.core.paths import get_config_dir
    logging.basicConfig(
        filename=str(get_config_dir() / "editor.log"),
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    rc = app.exec()
    # Force-exit: guarantees the interpreter dies immediately instead of
    # possibly hanging on a non-daemon thread or orphaned subprocess. By the
    # time app.exec() returns, Qt's own cleanup has already finished, so we
    # don't need atexit/stdlib teardown.
    os._exit(rc)


if __name__ == "__main__":
    main()
