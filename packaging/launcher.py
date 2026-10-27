"""Entry script for the packaged app (PyInstaller needs a script, not `python -m trainer`)."""
import sys

from trainer.__main__ import main

sys.exit(main())
