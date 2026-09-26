"""Export the generated OpenAPI schema to openapi.json (M5.1).

The schema is generated from the route signatures — never hand-maintained. Run:

    python -m scripts.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

OUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    app = create_app()
    schema = app.openapi()
    OUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
