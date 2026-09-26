"""Concrete ``Transcriber`` drivers.

This is the ONLY package permitted to import ``faster_whisper``. A test
(``tests/test_import_boundary.py``) enforces that no module outside this package
imports it, so swapping the model stays a one-package change.
"""
