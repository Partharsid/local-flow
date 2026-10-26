"""PyInstaller entry point for LocalFlow.exe."""
import sys

from localflow.app import main

if __name__ == "__main__":
    sys.exit(main())
