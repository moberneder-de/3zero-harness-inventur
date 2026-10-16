#!/usr/bin/env python3
"""Dashboard mit Knöpfen auf http://127.0.0.1:8765: python3 server.py [--harness claude-code|codex]. Der Code steht in inventur/server.py."""
import sys

if sys.version_info < (3, 11):  # /usr/bin/python3 ist auf dem Mac 3.9; import tomllib scheiterte dort mit einem Traceback
    sys.exit(f"server.py braucht Python 3.11 oder neuer, gestartet mit {sys.version.split()[0]}.")

from inventur.server import main

if __name__ == "__main__":
    main()
