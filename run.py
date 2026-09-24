"""Запуск без установки: python run.py (также точка входа для PyInstaller)."""
import sys

from nowplaying.cli import main

if __name__ == "__main__":
    sys.exit(main())
