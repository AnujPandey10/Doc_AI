from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    project_venv: Path | None
    injected_site_packages: Path | None
    running_in_project_venv: bool


def activate_project_environment(project_root: Path) -> BootstrapResult:
    """Make a local source checkout usable even when `streamlit` resolves to base Python.

    The supported launcher uses the project venv directly. This compatibility path
    prevents the common `(venv) (base)` shell-resolution problem from producing a
    raw ModuleNotFoundError when somebody still runs `streamlit run app.py`.
    """

    project_venv = project_root / "venv"
    if not project_venv.is_dir():
        project_venv = project_root / ".venv"
    if not project_venv.is_dir():
        return BootstrapResult(None, None, False)

    running_in_project_venv = _same_path(Path(sys.prefix), project_venv)
    if running_in_project_venv:
        return BootstrapResult(project_venv, None, True)

    site_packages = _find_site_packages(project_venv)
    if site_packages is not None and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
        os.environ.setdefault("VIRTUAL_ENV", str(project_venv))
    return BootstrapResult(project_venv, site_packages, False)


def project_venv_python(project_root: Path) -> Path | None:
    for directory_name in ("venv", ".venv"):
        # Unix-style venv
        posix_candidate = project_root / directory_name / "bin" / "python"
        if posix_candidate.is_file():
            return posix_candidate
        # Windows-style venv
        win_candidate = project_root / directory_name / "Scripts" / "python.exe"
        if win_candidate.is_file():
            return win_candidate
    return None


def _find_site_packages(project_venv: Path) -> Path | None:
    candidates = sorted((project_venv / "lib").glob("python*/site-packages"))
    expected = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for candidate in candidates:
        if candidate.parent.name == expected:
            return candidate
    return candidates[0] if candidates else None


def _same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return False

