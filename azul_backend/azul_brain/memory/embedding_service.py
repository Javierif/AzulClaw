"""Local embedding service.

Generates embeddings locally using Ollama's OpenAI-compatible API.
If Ollama is not available or not configured with an embedding model,
falls back to a deterministic hash-based local embedding (no network calls,
no external dependencies).

Configure in .env.local:
  AZUL_EMBEDDING_MODEL=nomic-embed-text   # or mxbai-embed-large, etc.
  OLLAMA_HOST=http://127.0.0.1:11434       # default
"""

import hashlib
import logging
import math
import os
from urllib.parse import urlparse

import aiohttp

LOGGER = logging.getLogger(__name__)

# Ollama embedding models output 768 dims (nomic-embed-text) or 1024 (mxbai-embed-large).
# The hash fallback uses 384 dims — lightweight and consistent.
_HASH_DIM = 384
_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"


def _hash_embed(text: str, dim: int = _HASH_DIM) -> list[float]:
    """Deterministic bag-of-words embedding via the hashing trick.

    Maps each word to a vector dimension using MD5 and accumulates counts,
    then L2-normalises. Captures lexical overlap — fully local, zero deps.
    """
    words = text.lower().split()
    vec = [0.0] * dim
    for word in words:
        idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class EmbeddingService:
    """Generates text embeddings — Ollama first, hash-based fallback."""

    def __init__(self, ollama_url: str, model: str):
        # Normalise to just scheme+host+port (strip paths like /v1)
        parsed = urlparse(ollama_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        self._url = f"{base}/v1/embeddings"
        self._model = model

    @classmethod
    def from_env(cls) -> "EmbeddingService":
        """Creates an EmbeddingService from environment variables."""
        ollama_url = (
            os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip()
            or os.environ.get("OLLAMA_HOST", "").strip()
            or "http://127.0.0.1:11434"
        )
        model = os.environ.get("AZUL_EMBEDDING_MODEL", _DEFAULT_OLLAMA_MODEL).strip()
        return cls(ollama_url=ollama_url, model=model)

    async def embed_text(self, text: str) -> list[float]:
        """Returns an embedding vector. Tries Ollama, falls back to hash embedding."""
        if not text or not text.strip():
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json={"model": self._model, "input": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=False,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vector = data["data"][0]["embedding"]
                        if isinstance(vector, list) and vector:
                            return vector
                    else:
                        body = await resp.text()
                        LOGGER.debug("[Embedding] Ollama returned %d — using local fallback: %s", resp.status, body[:120])
        except Exception as error:
            LOGGER.debug("[Embedding] Ollama unavailable (%s) — using local fallback.", error)

        return _hash_embed(text)
