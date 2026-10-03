"""PyInstaller entry script."""

import sys

if __name__ == "__main__":
    import multiprocessing

    # Dependencies may create resource-tracker processes even without workers.
    # Divert those helpers before importing or starting the GUI.
    multiprocessing.freeze_support()

    from subbyai.__main__ import main

    sys.exit(main())
