"""One reusable, bounded OpenAI Embeddings API client. No chat endpoint exists here."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence

import httpx

from app.core.config import Settings

EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingsError(RuntimeError):
    """Safe, stable embedding failure without request text, response body, or key."""


class EmbeddingsDisabled(EmbeddingsError):
    pass


class EmbeddingsNotConfigured(EmbeddingsError):
    pass


class OpenAIEmbeddingsClient:
    """Shared asynchronous adapter for ``text-embedding-3-small`` only.

    Calls are off unless OPENAI_EMBEDDINGS_ENABLED=true. Inputs are sent only as individual
    chunk/query strings, never as an uploaded file or parent document. Every response is
    checked for its model, index ordering, finite values, and exactly 1536 dimensions.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = settings.OPENAI_EMBEDDINGS_ENABLED
        self.configured = settings.OPENAI_API_KEY is not None
        self.model = settings.OPENAI_EMBEDDING_MODEL
        self.dimensions = settings.OPENAI_EMBEDDING_DIMENSIONS
        self.batch_size = settings.OPENAI_EMBEDDING_BATCH_SIZE
        self.retries = settings.OPENAI_EMBEDDING_MAX_RETRIES
        self.timeout_seconds = settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS
        self._api_key = settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None
        self._semaphore = asyncio.Semaphore(settings.OPENAI_EMBEDDING_MAX_CONCURRENT_REQUESTS)
        self._timeout = httpx.Timeout(
            connect=min(5.0, self.timeout_seconds),
            read=self.timeout_seconds,
            write=min(10.0, self.timeout_seconds),
            pool=min(5.0, self.timeout_seconds),
        )
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ordered strings in bounded batches; one missing/malformed batch fails closed."""
        if not self.enabled:
            raise EmbeddingsDisabled("embeddings_disabled")
        if not self._api_key:
            raise EmbeddingsNotConfigured("openai_embeddings_not_configured")
        if not texts:
            return []
        if any(not isinstance(value, str) or not value.strip() or len(value) > 40_000 for value in texts):
            raise EmbeddingsError("invalid_embedding_input")
        results: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            results.extend(await self._embed_batch(batch))
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(self.retries + 1):
            try:
                async with self._semaphore:
                    response = await self._http.post(EMBEDDINGS_URL, json=payload, headers=headers)
                if response.status_code in (408, 429) or 500 <= response.status_code < 600:
                    if attempt < self.retries:
                        await asyncio.sleep(min(0.25 * 2**attempt, 2.0))
                        continue
                    raise EmbeddingsError("openai_embeddings_unavailable")
                if response.status_code >= 400:
                    raise EmbeddingsError("openai_embeddings_rejected")
                try:
                    body = response.json()
                    if body.get("model") != self.model:
                        raise EmbeddingsError("openai_embedding_model_mismatch")
                    data = body["data"]
                    vectors_by_index = {}
                    for item in data:
                        index = item["index"]
                        raw_vector = item["embedding"]
                        if (
                            isinstance(index, bool)
                            or not isinstance(index, int)
                            or index in vectors_by_index
                            or not isinstance(raw_vector, list)
                            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_vector)
                        ):
                            raise EmbeddingsError("openai_embedding_response_malformed")
                        vectors_by_index[index] = [float(value) for value in raw_vector]
                    if set(vectors_by_index) != set(range(len(texts))):
                        raise EmbeddingsError("openai_embedding_response_malformed")
                    vectors = [vectors_by_index[index] for index in range(len(texts))]
                except EmbeddingsError:
                    raise
                except (TypeError, ValueError, KeyError, AttributeError):
                    raise EmbeddingsError("openai_embedding_response_malformed") from None
                if any(
                    len(vector) != self.dimensions
                    or not all(math.isfinite(value) for value in vector)
                    for vector in vectors
                ):
                    raise EmbeddingsError("openai_embedding_dimensions_mismatch")
                return vectors
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.retries:
                    raise EmbeddingsError("openai_embeddings_unavailable") from None
                await asyncio.sleep(min(0.25 * 2**attempt, 2.0))
        raise EmbeddingsError("openai_embeddings_unavailable")

    async def close(self) -> None:
        await self._http.aclose()
