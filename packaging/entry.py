"""Точка входа для PyInstaller: окно Compass без консоли."""

import multiprocessing

from compass.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
