#!/usr/bin/env python3
"""Daymark developer CLI. Run `python3 daymark.py --help`."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from daymark_cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
