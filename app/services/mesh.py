"""Mesh API client.

Every LLM/AI call in the project (chat completions AND embeddings) must go
through Mesh API. This module is the single place that talks to Mesh.
"""
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class MeshError(RuntimeError):
    pass


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.mesh_api_key:
            raise MeshError("MESH_API_KEY is not configured in .env")
        _client = OpenAI(base_url=settings.mesh_base_url, api_key=settings.mesh_api_key)
    return _client


def chat(messages: list[dict], model: str | None = None, **kwargs) -> str:
    """One LLM chat completion via Mesh. Returns the text content."""
    try:
        response = get_client().chat.completions.create(
            model=model or settings.llm_model,
            messages=messages,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - surface as MeshError
        logger.exception("Mesh chat failed")
        raise MeshError(str(exc)) from exc
    return response.choices[0].message.content or ""


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed a list of texts via Mesh. Returns one vector per input text."""
    if not texts:
        return []
    try:
        response = get_client().embeddings.create(
            model=model or settings.embedding_model,
            input=texts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Mesh embedding failed")
        raise MeshError(str(exc)) from exc
    ordered = sorted(response.data, key=lambda d: d.index)
    return [item.embedding for item in ordered]


def embed_one(text: str, model: str | None = None) -> list[float]:
    return embed([text], model=model)[0]
