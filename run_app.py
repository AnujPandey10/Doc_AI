from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _project_bootstrap import project_venv_python


def main() -> None:
    project_root = Path(__file__).resolve().parent
    venv_python = project_venv_python(project_root)
    if venv_python is None:
        raise SystemExit(
            "Project virtual environment not found. Create it with:\n"
            "  python -m venv venv\n"
            "  venv/bin/python -m pip install -r requirements.txt"
        )

    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "ANONYMIZED_TELEMETRY": "FALSE",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
            "TOKENIZERS_PARALLELISM": "false",
            "VIRTUAL_ENV": str(venv_python.parent.parent),
        }
    )
    command = [
        str(venv_python),
        str(project_root / "server_fastapi.py"),
        *sys.argv[1:],
    ]
    sys.exit(subprocess.run(command, env=environment).returncode)


if __name__ == "__main__":
    main()

