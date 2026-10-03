"""Test package bootstrap.

Keeps test imports working with zero edits to the test files themselves:
repo root stays importable (`import auth`, `from db import ...`) and this
directory too (`from test_helpers import client`). Run from the repo root:

    python -m unittest discover -s tests
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_here), _here):
    if _p not in sys.path:
        sys.path.insert(0, _p)
