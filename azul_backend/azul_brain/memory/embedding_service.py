"""Embedding service backed by Azure OpenAI via agent_framework."""

import logging
import os

from agent_framework.openai import OpenAIEmbeddingClient

LOGGER = logging.getLogger(__name__)

_DEFAULT_EMBEDDING_MODEL = "text-embedding-ada-002"


class EmbeddingService:
    """Generates text embeddings using Azure OpenAI."""

    def __init__(self, client: OpenAIEmbeddingClient, model: str):
        self._client = client
        self._model = model

    @classmethod
    def from_env(cls) -> "EmbeddingService":
        """Creates an EmbeddingService from environment variables.

        Raises RuntimeError if required variables are not set.
        """
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        if not endpoint or not api_key:
            raise RuntimeError(
                "Vector memory requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY."
            )
        model = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", _DEFAULT_EMBEDDING_MODEL).strip()
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()

        client = OpenAIEmbeddingClient(
            model=model,
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )
        return cls(client=client, model=model)

    async def embed_text(self, text: str) -> list[float]:
        """Returns the embedding vector for the given text, or an empty list on failure."""
        if not text or not text.strip():
            return []
        try:
            result = await self._client.get_embeddings([text])
            embeddings = getattr(result, "embeddings", None) or getattr(result, "data", None)
            if embeddings:
                first = embeddings[0]
                vector = getattr(first, "embedding", None) or getattr(first, "data", None)
                if isinstance(vector, list):
                    return vector
        except Exception as error:
            LOGGER.warning("[Embedding] Failed to embed text: %s", error)
        return []
