"""Fully offline retrieval-augmented generation application."""

import os

# Set offline controls before importing libraries that cache environment settings
# during module initialization.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "FALSE")
os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

__all__ = ["__version__"]
__version__ = "0.1.0"
