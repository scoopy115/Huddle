"""PyInstaller entry point for the packaged engine.

`huddle_engine/__main__.py` uses relative imports, which only work when Python runs it as
`python -m huddle_engine`. PyInstaller executes the given script as a top-level module, so the
sidecar starts here and hands over to the package's real `main()`.
"""
import sys

from huddle_engine.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
