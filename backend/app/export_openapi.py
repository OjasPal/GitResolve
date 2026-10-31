"""Write the OpenAPI document: `python -m app.export_openapi [path]` (default docs/openapi.json)."""

import json
import sys

from app.core.config import Settings
from app.main import create_app

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "docs/openapi.json"
    spec = create_app(settings=Settings(environment="test", _env_file=None)).openapi()
    with open(path, "w") as fh:
        json.dump(spec, fh, indent=2)
    print(f"wrote {path} ({len(spec['paths'])} paths)")
