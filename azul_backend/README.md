# azul_backend

Python backend for AzulClaw.

## Contents

- `azul_brain/`: orchestration, memory, bot handler and main runtime
- `azul_hands_mcp/`: MCP sandbox for secure file operations

## Memory stack (`azul_brain/memory/`)

- **`vector_store.py`** — SQLite + FTS5 + embeddings; hybrid search (RRF-weighted vector + BM25).
- **`safe_memory.py`** — Bounded per-user history with optional write-through to the same SQLite file.
- **`embedding_service.py`** — Azure OpenAI embeddings (default `text-embedding-3-large`, 3072 dims).
- **`preference_extractor.py`** — After each turn, schedules JSON extraction on the **fast** Azure deployment (`AZURE_OPENAI_FAST_DEPLOYMENT`, e.g. `gpt-5.4-nano`) and stores deduplicated preferences/facts.
- **`hybrid_ranker.py`** — Weighted reciprocal rank fusion.
- **`episodic_store.py`** — Session diary schema/helpers (same DB path convention); wire-up may evolve separately from chat.

Configure via `azul_brain/.env.local`; see root **README** and `docs/02_setup_and_development.md#hybrid-memory-env`.

## Running

From the repo root:

```bash
python -m azul_backend.azul_brain.main_launcher
```
