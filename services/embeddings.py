from openai import AsyncOpenAI
from config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_batch(texts: list[str]) -> list[list[float]]:
    # Truncate to ~24000 chars as safety margin for 8192-token context
    truncated = [t[:24000] for t in texts]
    resp = await _get_client().embeddings.create(
        model="text-embedding-3-small",
        input=truncated,
    )
    return [d.embedding for d in resp.data]


async def embed_single(text: str) -> list[float]:
    result = await embed_batch([text])
    return result[0]
