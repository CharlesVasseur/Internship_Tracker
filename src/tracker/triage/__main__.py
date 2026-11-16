"""Lets `python -m tracker.triage` run the command-line tool."""

import sys

from tracker.triage.cli import main

if __name__ == "__main__":
    sys.exit(main())
