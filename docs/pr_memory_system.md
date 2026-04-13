# PR: Hybrid Memory System

## Overview

This PR introduces a full persistent memory layer for AzulClaw. The assistant now learns user preferences from conversations, seeds initial context from the onboarding profile, and injects that knowledge into every LLM call. All data is stored locally in SQLite — no external vector database required.

---

## Prerequisites

### Backend

- Python 3.10+
- Azure OpenAI endpoint and API key configured in `.env` (same credentials used for chat — no additional deployment needed to start)
- Optional: `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` set to an embedding model (e.g. `text-embedding-3-large`) to enable vector search. Without it the system falls back to BM25 text search only.

### Environment variables

```env
# Required (same as existing chat config)
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key

# Optional — enables vector search on top of BM25
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Optional overrides
AZUL_MEMORY_DB_PATH=/custom/path/azul_memory.db    # defaults to <workspace>/.azul/azul_memory.db
AZUL_WORKSPACE_ROOT=/custom/workspace              # defaults to ~/Documents/dev/AzulWorkspace
AZUL_PREFERENCE_EXTRACTION_ENABLED=true            # set to false to disable background extraction
VECTOR_MEMORY_ENABLED=true                         # set to false to disable vector store entirely
```

---

## What changed

### New files

| File | Purpose |
|---|---|
| `azul_backend/workspace_layout.py` | Ensures workspace scaffold including `.azul/` dir and SQLite file on first run |
| `azul_backend/azul_brain/memory/vector_store.py` | SQLite-backed store: vector (cosine) + BM25/FTS5 + RRF hybrid search |
| `azul_backend/azul_brain/memory/hybrid_ranker.py` | Reciprocal Rank Fusion merging vector and text results (70/30 default) |
| `azul_backend/azul_brain/memory/preference_extractor.py` | Background LLM module: extracts user preferences after each turn |
| `azul_backend/azul_brain/memory/safe_memory.py` | Short-term conversation history with SQLite write-through |
| `azul_backend/azul_brain/memory/embedding_service.py` | Azure OpenAI embedding client |

### Modified files

#### Backend

- **`hatching_store.py`** — Added `resolve_memory_db_path()` (single source of truth for DB path), `_AZUL_STATE_DIR`, `_MEMORY_DB_FILENAME` constants
- **`conversation.py`** — Memory layers wired: `SafeMemory`, `VectorMemoryStore`, `EmbeddingService`, `PreferenceExtractor`. Added `seed_profile_facts()`, `reload_persistent_memory()`, hybrid knowledge injection in `build_agent_messages()`
- **`services.py`** — `summarize_memory()` now reads real preferences from SQLite. Conversation turns removed from memory view
- **`routes.py`** — Added `DELETE /api/desktop/memory/{memory_id}`, `POST /api/desktop/data-wipe`
- **`system_prompt.py`** — Instructs the LLM to acknowledge when it will remember something, ending with 🐾
- **`main_launcher.py`** — Calls `ensure_workspace_scaffold()` at startup

#### Frontend

- **`MemoryShell.tsx`** — Full rewrite: shows only learned preferences, wired delete button, detail panel with content block and meta rows
- **`HatchingShell.tsx`** — Added "Setting up your environment" preparing screen with mascot and "All set" tick; after completing onboarding the DB is initialised before navigating to chat
- **`SettingsShell.tsx`** — Data wipe replaced with a confirmation modal: shows the phrase prominently with a copy button, confirm button disabled until phrase matches exactly
- **`ChatShell.tsx`** — Replaced "Activating the first visible response..." placeholder with animated wave dots
- **`api.ts`** — Added `deleteMemory()` function
- **`contracts.ts`** — Added `content?`, `created_at?` fields to `MemoryRecord`

---

## Architecture

### SQLite schema

All memory is stored in a single file: `<workspace_root>/.azul/azul_memory.db`

```
memories
  id          TEXT PRIMARY KEY
  user_id     TEXT
  role        TEXT
  content     TEXT          ← the preference sentence
  source      TEXT          ← 'extractor' | 'hatching-profile'
  category    TEXT          ← 'preference' (only type saved)
  embedding   BLOB          ← float32 vector, NULL if no embedding service
  created_at  TEXT

memories_fts  (FTS5 virtual table, kept in sync via triggers)
conversation_history
  user_id, role, content, created_at
```

WAL mode is enabled at creation — the `.db-shm` and `.db-wal` sidecar files are normal and expected while the backend is running.

### Search modes

| Mode | When used |
|---|---|
| Vector only | Embedding service configured, query embedding available |
| BM25 only | No embedding service, or embedding failed |
| Hybrid (default) | Both available — RRF fusion with 70% vector / 30% BM25 |

### Preference extraction pipeline

```
User sends message
  → LLM responds (main turn)
  → fire_and_forget() launches background task
      → should_extract() pre-filter (skips greetings, < 3 words, credentials)
      → Fast-lane LLM call with extraction prompt
      → Returns JSON: [{"type": "preference", "content": "..."}]
      → Deduplication check (exact content match)
      → Embed content (optional)
      → INSERT INTO memories
```

The extraction never blocks the user-facing response.

### Context injection priority

On every LLM call, `build_agent_messages()` injects two blocks into the system context:

```
What the user has told you directly (higher priority):
- <preferences extracted from conversations>

Initial setup preferences (use as baseline, explicit statements above override these):
- <preferences seeded from onboarding profile>
```

Explicit user statements always override onboarding defaults.

---

## Important things to consider

### DB location

The DB lives at `<workspace_root>/.azul/azul_memory.db`. The `workspace_root` is read from `hatching_profile.json` at `<repo>/memory/hatching_profile.json`. If you're seeing an empty DB, confirm you're querying the right path:

```bash
sqlite3 <workspace_root>/.azul/azul_memory.db ".tables"
sqlite3 <workspace_root>/.azul/azul_memory.db "SELECT content, source FROM memories;"
# e.g. sqlite3 ~/Documents/dev/AzulWorkspace/.azul/azul_memory.db ".tables"
# actual path stored in memory/hatching_profile.json → workspace_root field
```

### Embeddings are optional

If `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` is not set, the vector store still works — rows are stored with `embedding = NULL` and only BM25 search is used. Vector search is added automatically once an embedding deployment is configured, no code change required.

### Extraction prompt limits

The extractor is instructed to return at most 3 condensed preference items per turn, grouping related ideas. It only saves preferences (how the user likes things done, communication style, goals) — not neutral facts, conversation content, or technical context.

### Data wipe

Settings → wipe requires typing the phrase `RESET_ALL_LOCAL_DATA` exactly. This deletes the SQLite file, resets `hatching_profile.json` to defaults (`is_hatched: false`), and sends the user back through onboarding on next load. The backend must be restarted after a wipe for the memory subsystem to reopen a clean file.

### Re-seeding onboarding preferences

`seed_profile_facts()` uses exact-content deduplication — it won't insert the same sentence twice. If you change the onboarding profile and want the new values seeded, wipe the `hatching-profile` rows first:

```bash
sqlite3 <workspace_root>/.azul/azul_memory.db \
  "DELETE FROM memories WHERE source = 'hatching-profile';"
```

Then restart the backend and re-save the hatching profile from Settings.

---

## Testing the pipeline end-to-end

1. Start the backend: `python -m azul_backend.azul_brain.main_launcher`
2. Start the frontend: `npm run dev` inside `azul_desktop/`
3. Complete onboarding — check that `memories` table has 5 rows with `source = 'hatching-profile'`
4. Chat something with personal context (e.g. *"I always work in Python and I prefer short, direct answers"*)
5. Watch backend logs for `[PrefExtractor] Learned preference for user desktop-user: ...`
6. Open the Memory section — the extracted preference should appear under "X learned"
7. Click a preference → detail panel shows the full content
8. Click "Delete memory" → row removed from DB and list

```bash
# Quick DB check after step 4
sqlite3 <workspace_root>/.azul/azul_memory.db \
  "SELECT content, source, created_at FROM memories ORDER BY created_at DESC LIMIT 5;"
```
