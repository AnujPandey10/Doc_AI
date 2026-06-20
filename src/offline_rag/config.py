from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _validate_loopback_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("RAG_OLLAMA_BASE_URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "RAG_OLLAMA_BASE_URL must point to localhost; remote model endpoints are disabled"
        )
    return url.rstrip("/")


def _local_model_or_id(directory_name: str, model_id: str) -> str:
    model_root = Path(
        os.getenv(
            "RAG_MODEL_ROOT",
            str(Path(__file__).resolve().parents[2] / "models"),
        )
    ).expanduser()
    local_model = model_root / directory_name
    return str(local_model.resolve()) if local_model.is_dir() else model_id


@dataclass(frozen=True, slots=True)
class AppConfig:
    data_root: Path
    embedding_model: str
    reranker_model: str
    ollama_model: str
    ollama_base_url: str
    device: str
    chunk_size: int
    chunk_overlap: int
    parse_workers: int
    embedding_batch_size: int
    dense_k: int
    sparse_k: int
    rerank_top_n: int
    history_turns: int
    dense_weight: float
    sparse_weight: float

    @classmethod
    def from_env(cls) -> AppConfig:
        cpu_count = os.cpu_count() or 4
        data_root = Path(os.getenv("RAG_DATA_ROOT", ".rag_data")).expanduser().resolve()
        dense_weight = _env_float("RAG_DENSE_WEIGHT", 0.65)
        sparse_weight = _env_float("RAG_SPARSE_WEIGHT", 0.35)
        if dense_weight < 0 or sparse_weight < 0 or dense_weight + sparse_weight <= 0:
            raise ValueError("Retrieval weights must be non-negative and not both zero")

        chunk_size = _env_int("RAG_CHUNK_SIZE", 1000)
        chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", "150"))
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be between 0 and chunk size")

        return cls(
            data_root=data_root,
            embedding_model=os.getenv(
                "RAG_EMBEDDING_MODEL",
                _local_model_or_id("bge-small-en-v1.5", "BAAI/bge-small-en-v1.5"),
            ),
            reranker_model=os.getenv(
                "RAG_RERANKER_MODEL",
                _local_model_or_id("bge-reranker-base", "BAAI/bge-reranker-base"),
            ),
            ollama_model=os.getenv("RAG_OLLAMA_MODEL", "llama3.2"),
            ollama_base_url=_validate_loopback_url(
                os.getenv("RAG_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
            ),
            device=os.getenv("RAG_DEVICE", "cpu"),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            parse_workers=_env_int("RAG_PARSE_WORKERS", min(8, cpu_count)),
            embedding_batch_size=_env_int("RAG_EMBEDDING_BATCH_SIZE", 64),
            dense_k=_env_int("RAG_DENSE_K", 20),
            sparse_k=_env_int("RAG_SPARSE_K", 20),
            rerank_top_n=_env_int("RAG_RERANK_TOP_N", 6),
            history_turns=_env_int("RAG_HISTORY_TURNS", 6),
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
        )

    def apply_offline_environment(self) -> None:
        """Force Hugging Face and Transformers to use only preloaded local artifacts."""
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["ANONYMIZED_TELEMETRY"] = "FALSE"
        os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        self.data_root.mkdir(parents=True, exist_ok=True)
