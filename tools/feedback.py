# CLI for client feedback (shared MCP standard, section 10).
# Usage: .venv/bin/python tools/feedback.py pending | all | decide <id> approved|rejected|done ["note"]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from feedback import _cli  # noqa: E402

_cli()
