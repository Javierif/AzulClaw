# Memory System

Last reviewed: 2026-04-23

## Purpose

AzulClaw uses a local persistent memory layer so the assistant can retain useful facts and preferences across sessions without depending on an external vector database.

## Storage model

The default database location is:

```text
<workspace_root>/.azul/azul_memory.db
```

This file is created automatically as part of the workspace scaffold.

## What the memory system stores

- recent conversation history for continuity
- durable learned preferences and facts
- embeddings for vector retrieval
- full-text index entries for keyword retrieval
- conversation and session metadata

## Retrieval model

AzulClaw uses hybrid retrieval:

1. vector similarity over embedded memory items
2. FTS-backed keyword search
3. weighted reciprocal rank fusion to merge results

## Main components

| File | Responsibility |
|---|---|
| `memory/vector_store.py` | SQLite persistence and hybrid search |
| `memory/hybrid_ranker.py` | Weighted result fusion |
| `memory/embedding_service.py` | Azure OpenAI embedding calls |
| `memory/safe_memory.py` | Recent history and conversation continuity |
| `memory/preference_extractor.py` | Background extraction of durable facts |

## Key environment variables

```env
AZUL_WORKSPACE_ROOT=
AZUL_MEMORY_DB_PATH=
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=
AZUL_AZURE_OPENAI_AUTH_MODE=entra
AZUL_EMBEDDING_DIM=3072
VECTOR_MEMORY_ENABLED=true
AZUL_HYBRID_VECTOR_WEIGHT=0.7
AZUL_HYBRID_TEXT_WEIGHT=0.3
AZUL_PREFERENCE_EXTRACTION_ENABLED=true
MEMORY_MAX_MESSAGES=50
```

The embedding layer follows the same Azure OpenAI authentication mode as the
rest of the backend. In desktop scenarios, Microsoft Entra ID is the preferred
path. If remote embeddings are unavailable, AzulClaw falls back to local
OpenAI-compatible embeddings and then to deterministic hash embeddings.
