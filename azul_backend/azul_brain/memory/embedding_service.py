"""Embedding service with Azure/Foundry support plus local fallback."""

import hashlib
import logging
import math
import os
from urllib.parse import urlparse

import aiohttp
from agent_framework.azure import AzureOpenAIEmbeddingClient
from agent_framework.openai import OpenAIEmbeddingClient

from ..azure_auth import (
    get_azure_openai_token_provider,
    get_default_azure_credential,
    resolve_azure_openai_auth_mode,
)
from ..foundry_url import (
    is_foundry_endpoint,
    normalize_azure_openai_endpoint,
    normalize_foundry_base_url,
)

LOGGER = logging.getLogger(__name__)

_HASH_DIM = 384
_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
_DEFAULT_AZURE_EMBEDDING_MODEL = "text-embedding-ada-002"


def _hash_embed(text: str, dim: int = _HASH_DIM) -> list[float]:
    """Deterministic bag-of-words embedding via the hashing trick."""
    words = text.lower().split()
    vec = [0.0] * dim
    for word in words:
        idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class EmbeddingService:
    """Generates text embeddings using Azure/Foundry first, then local fallback."""

    def __init__(
        self,
        *,
        client: AzureOpenAIEmbeddingClient | OpenAIEmbeddingClient | None,
        model: str,
        ollama_url: str,
        ollama_model: str,
    ):
        self._client = client
        self._model = model
        parsed = urlparse(ollama_url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "http://127.0.0.1:11434"
        self._local_url = f"{base}/v1/embeddings"
        self._local_model = ollama_model

    @classmethod
    def from_env(cls) -> "EmbeddingService":
        """Creates an EmbeddingService from environment variables."""
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        auth_mode = resolve_azure_openai_auth_mode(api_key)
        model = os.environ.get(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
            _DEFAULT_AZURE_EMBEDDING_MODEL,
        ).strip()
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()

        client: AzureOpenAIEmbeddingClient | OpenAIEmbeddingClient | None = None
        if endpoint and model and (auth_mode == "entra" or api_key):
            try:
                if is_foundry_endpoint(endpoint):
                    kwargs = {
                        "model_id": model,
                        "base_url": normalize_foundry_base_url(endpoint),
                    }
                    if auth_mode == "entra":
                        kwargs["api_key"] = get_azure_openai_token_provider()
                    else:
                        kwargs["api_key"] = api_key
                    client = OpenAIEmbeddingClient(**kwargs)
                else:
                    kwargs = {
                        "deployment_name": model,
                        "endpoint": normalize_azure_openai_endpoint(endpoint),
                        "api_version": api_version,
                    }
                    if auth_mode == "entra":
                        kwargs["credential"] = get_default_azure_credential()
                    else:
                        kwargs["api_key"] = api_key
                    client = AzureOpenAIEmbeddingClient(**kwargs)
            except Exception as error:
                LOGGER.warning("[Embedding] Azure embedding client unavailable: %s", error)
                client = None

        ollama_url = (
            os.environ.get("AZUL_FAST_OLLAMA_BASE_URL", "").strip()
            or os.environ.get("OLLAMA_HOST", "").strip()
            or "http://127.0.0.1:11434"
        )
        ollama_model = os.environ.get("AZUL_EMBEDDING_MODEL", _DEFAULT_OLLAMA_MODEL).strip()
        return cls(
            client=client,
            model=model,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
        )

    async def _embed_with_remote_client(self, text: str) -> list[float]:
        """Returns embeddings from the configured Azure/Foundry client."""
        if self._client is None:
            return []
        result = await self._client.get_embeddings([text])
        embeddings = getattr(result, "embeddings", None) or getattr(result, "data", None)
        if not embeddings:
            return []
        first = embeddings[0]
        vector = getattr(first, "embedding", None) or getattr(first, "data", None)
        return vector if isinstance(vector, list) else []

    async def _embed_with_local_fallback(self, text: str) -> list[float]:
        """Tries the local OpenAI-compatible embeddings endpoint before hashing."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._local_url,
                    json={"model": self._local_model, "input": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vector = data["data"][0]["embedding"]
                        if isinstance(vector, list) and vector:
                            return vector
                    else:
                        body = await resp.text()
                        LOGGER.debug(
                            "[Embedding] Local embedding endpoint returned %d; using hash fallback: %s",
                            resp.status,
                            body[:120],
                        )
        except Exception as error:
            LOGGER.debug("[Embedding] Local embedding endpoint unavailable (%s); using hash fallback.", error)

        return _hash_embed(text)

    async def embed_text(self, text: str) -> list[float]:
        """Returns an embedding vector using remote-first, local-second strategy."""
        if not text or not text.strip():
            return []

        try:
            vector = await self._embed_with_remote_client(text)
            if vector:
                return vector
        except Exception as error:
            LOGGER.warning("[Embedding] Remote embedding failed; falling back locally: %s", error)

        return await self._embed_with_local_fallback(text)
