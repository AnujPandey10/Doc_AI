from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def check_local_model(model_reference: str, label: str) -> PreflightCheck:
    direct_path = Path(model_reference).expanduser()
    if _is_complete_model_directory(direct_path):
        return PreflightCheck(label, True, str(direct_path.resolve()))
    if direct_path.is_dir():
        return PreflightCheck(
            label,
            False,
            f"{direct_path} exists but its model weights are incomplete.",
        )

    for cache_root in _huggingface_cache_roots():
        model_directory = cache_root / f"models--{model_reference.replace('/', '--')}"
        snapshots = model_directory / "snapshots"
        if snapshots.is_dir() and any(
            _is_complete_model_directory(path) for path in snapshots.iterdir()
        ):
            return PreflightCheck(label, True, str(model_directory))

    return PreflightCheck(
        label,
        False,
        (
            f"{model_reference} is not available locally. Transfer its complete "
            "Hugging Face snapshot and set the matching RAG_*_MODEL variable to "
            "that directory."
        ),
    )


def check_ollama(base_url: str, model_name: str, timeout: float = 1.5) -> PreflightCheck:
    try:
        with urlopen(f"{base_url}/api/tags", timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return PreflightCheck(
            "Ollama",
            False,
            f"Cannot reach {base_url}: {type(exc).__name__}: {exc}",
        )

    available = {
        str(model.get("name", ""))
        for model in payload.get("models", [])
        if isinstance(model, dict)
    }
    normalized = model_name if ":" in model_name else f"{model_name}:latest"
    if model_name in available or normalized in available:
        return PreflightCheck("Ollama", True, f"{normalized} is available locally")
    return PreflightCheck(
        "Ollama",
        False,
        (
            f"{normalized} is not installed. Available models: "
            f"{', '.join(sorted(available)) or 'none'}"
        ),
    )


def _huggingface_cache_roots() -> list[Path]:
    roots: list[Path] = []
    if os.getenv("HUGGINGFACE_HUB_CACHE"):
        roots.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]).expanduser())
    if os.getenv("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]).expanduser() / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    return list(dict.fromkeys(roots))


def _is_complete_model_directory(path: Path) -> bool:
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    return (path / "model.safetensors").is_file() or (path / "pytorch_model.bin").is_file()
