# Air-Gapped RAG

A local-only Retrieval-Augmented Generation application for large PDF and DOCX
collections. It uses LangChain, Ollama, Hugging Face models loaded from local
storage, persistent Chroma HNSW search, BM25 sparse retrieval, cross-encoder
reranking, SQLite content hashing, watchdog monitoring, and Streamlit.

## What it does

- Recursively scans 1,000+ PDF/DOCX files and hashes changed files in parallel.
- Embeds each unique SHA-256 content object once, even when identical files have
  different names.
- Parses and chunks unique documents concurrently.
- Keeps SQLite catalog updates transactional and serializes batched Chroma writes.
- Watches for creates, edits, moves, and deletes without blocking the UI.
- Combines dense Chroma and sparse BM25 retrieval, then reranks with
  `BAAI/bge-reranker-base`.
- Uses history-aware conversational retrieval with a strict grounded-answer prompt.
- Shows exact source file names and page numbers for every chunk supplied as context.

DOCX files are flow documents and `Docx2txtLoader` does not expose stable rendered
page boundaries, so DOCX chunks are cited as page 1. PDF page numbers are preserved.

## Air-gapped prerequisites

Prepare these artifacts on a connected staging machine, scan them according to your
organization's supply-chain policy, and transfer them into the air-gapped network:

1. A Python wheelhouse containing all packages in `requirements.txt`.
2. The complete Hugging Face snapshots for:
   - `BAAI/bge-small-en-v1.5`
   - `BAAI/bge-reranker-base`
3. An Ollama installation and the `llama3.2` model blob/manifest.

The running application forces Hugging Face/Transformers offline mode, disables
Hugging Face, Chroma, and Streamlit telemetry, and rejects non-loopback Ollama
endpoints.

By default, the application automatically uses these project-local directories
when present:

- `models/bge-small-en-v1.5`
- `models/bge-reranker-base`

## Installation

Using Python 3.11 or 3.12 in the air-gapped environment:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --no-index --find-links /path/to/wheelhouse -r requirements.txt
python -m pip install --no-index --no-build-isolation --find-links /path/to/wheelhouse -e .
```

Make the model snapshots available in the Hugging Face cache, or point directly to
their transferred local directories:

```bash
export RAG_EMBEDDING_MODEL=/models/bge-small-en-v1.5
export RAG_RERANKER_MODEL=/models/bge-reranker-base
export RAG_OLLAMA_MODEL=llama3.2
export RAG_OLLAMA_BASE_URL=http://127.0.0.1:11434
export RAG_DATA_ROOT=/var/lib/airgap-rag
```

Ensure Ollama is running locally and already contains the model. Do not use
`ollama pull` inside the air-gapped runtime.

## Run

```bash
python run_app.py
```

The launcher always uses `venv/bin/python`, even if Anaconda base is also active or
the shell has cached the wrong `streamlit` executable. Additional Streamlit flags
can be appended, for example `python run_app.py --server.port 8502`.

Direct launch is also supported:

```bash
venv/bin/python -m streamlit run app.py
```

Avoid bare `streamlit run app.py` in shells showing both `(venv)` and `(base)`,
because the command can still resolve to `/opt/anaconda3/bin/streamlit`.

Enter an absolute directory path in the sidebar and choose **Start / switch index**.
The first run loads local models, scans files, parses/chunks unique content, embeds
in batches, builds BM25, and loads the reranker. Later runs process only changes.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `RAG_DATA_ROOT` | `.rag_data` | Persistent SQLite and Chroma indexes |
| `RAG_DEVICE` | `cpu` | `cpu`, `cuda`, or `mps` |
| `RAG_CHUNK_SIZE` | `1000` | Chunk size in characters |
| `RAG_CHUNK_OVERLAP` | `150` | Character overlap |
| `RAG_PARSE_WORKERS` | `min(8, CPU count)` | Hash/parse thread count |
| `RAG_EMBEDDING_BATCH_SIZE` | `64` | Serialized Chroma embedding batch |
| `RAG_DENSE_K` | `20` | Dense candidates |
| `RAG_SPARSE_K` | `20` | BM25 candidates |
| `RAG_RERANK_TOP_N` | `6` | Context chunks passed to Llama |
| `RAG_HISTORY_TURNS` | `6` | Recent user/assistant turns sent to Llama |
| `RAG_DENSE_WEIGHT` | `0.65` | Ensemble dense weight |
| `RAG_SPARSE_WEIGHT` | `0.35` | Ensemble sparse weight |

## Validation

```bash
pytest
ruff check .
python -m compileall app.py src tests
```

Runtime data is isolated per source directory under
`$RAG_DATA_ROOT/indexes/<index-hash>/`. The hash also includes the embedding model
and chunking configuration, preventing incompatible vectors from being mixed.
