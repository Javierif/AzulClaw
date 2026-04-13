"""Helpers for Azure AI Foundry and Azure OpenAI endpoint normalization."""

from urllib.parse import urlparse, urlunparse


def is_foundry_endpoint(raw_url: str) -> bool:
    """Returns True when the URL points to an Azure AI Foundry endpoint."""
    text = (raw_url or "").strip()
    if not text:
        return False

    parsed = urlparse(text)
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    return hostname.endswith(".services.ai.azure.com") or path.startswith("/api/projects/")


def normalize_foundry_base_url(raw_url: str) -> str:
    """Normalizes a Foundry endpoint into an OpenAI-compatible base_url."""
    text = (raw_url or "").strip().rstrip("/")
    if not text:
        return ""

    parsed = urlparse(text)
    parts = [part for part in (parsed.path or "").split("/") if part]

    if len(parts) >= 3 and parts[0].lower() == "api" and parts[1].lower() == "projects":
        normalized_path = f"/api/projects/{parts[2]}/openai/v1"
        return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))

    if text.endswith("/openai/v1"):
        return text
    if text.endswith("/openai"):
        return f"{text}/v1"
    return f"{text}/openai/v1"


def normalize_azure_openai_endpoint(raw_url: str) -> str:
    """Normalizes Azure OpenAI endpoints to the resource root."""
    text = (raw_url or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/")

    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")
