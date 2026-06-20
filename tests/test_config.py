from pathlib import Path

import pytest

from offline_rag.config import AppConfig


def test_remote_ollama_endpoint_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("RAG_OLLAMA_BASE_URL", "https://example.com")

    with pytest.raises(ValueError, match="localhost"):
        AppConfig.from_env()


def test_offline_environment_is_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_ROOT", str(tmp_path))
    config = AppConfig.from_env()

    config.apply_offline_environment()

    assert config.data_root == Path(tmp_path)
    assert config.data_root.exists()
    assert __import__("os").environ["HF_HUB_OFFLINE"] == "1"
    assert __import__("os").environ["ANONYMIZED_TELEMETRY"] == "FALSE"
