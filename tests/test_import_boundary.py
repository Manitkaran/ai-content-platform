"""M1.1 — no module outside the drivers package imports faster_whisper.

CR-025 extends the same rule to ``argostranslate`` for the translate seam: the MT
library may be imported only under ``app/translate/drivers/``.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ALLOWED = APP_DIR / "model" / "drivers"
TRANSLATE_ALLOWED = APP_DIR / "translate" / "drivers"


def test_faster_whisper_import_is_contained():
    offenders = []
    pattern = re.compile(r"^\s*(import|from)\s+faster_whisper\b", re.MULTILINE)
    for py in APP_DIR.rglob("*.py"):
        if ALLOWED in py.parents or py.parent == ALLOWED:
            continue
        if pattern.search(py.read_text()):
            offenders.append(str(py.relative_to(APP_DIR)))
    assert offenders == [], (
        f"faster_whisper imported outside app/model/drivers: {offenders}"
    )


def test_argostranslate_import_is_contained():
    """CR-025: argostranslate is imported only under app/translate/drivers/."""

    offenders = []
    pattern = re.compile(r"^\s*(import|from)\s+argostranslate\b", re.MULTILINE)
    for py in APP_DIR.rglob("*.py"):
        if TRANSLATE_ALLOWED in py.parents or py.parent == TRANSLATE_ALLOWED:
            continue
        if pattern.search(py.read_text()):
            offenders.append(str(py.relative_to(APP_DIR)))
    assert offenders == [], (
        f"argostranslate imported outside app/translate/drivers: {offenders}"
    )
