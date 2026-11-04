from __future__ import annotations

from requests.exceptions import ConnectionError

from app.rag.chunking import _CharacterEncoding, _encoding


def test_offline_tokenizer_asset_uses_bounded_character_fallback(monkeypatch) -> None:
    _encoding.cache_clear()

    def unavailable(_name: str):
        raise ConnectionError("offline")

    monkeypatch.setattr("app.rag.chunking.tiktoken.get_encoding", unavailable)
    encoding = _encoding("cl100k_base")
    assert isinstance(encoding, _CharacterEncoding)
    text = "Café runbook"
    tokens = encoding.encode(text)
    decoded, offsets = encoding.decode_with_offsets(tokens)
    assert decoded == text
    assert len(tokens) == len(text)
    assert offsets == list(range(len(text)))
    _encoding.cache_clear()
