import sys
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Setup Logging
# Create a dedicated logs directory
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler() # Only print to console
    ]
)

from gui_main import WhisperGUI

def main():
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    
    window = WhisperGUI()
    
    if window.executable_path:
        window.show()
        sys.exit(app.exec())
    else:
        logging.info("Exiting application because dependencies are not met.")
        sys.exit(0)


if __name__ == '__main__':
    main()