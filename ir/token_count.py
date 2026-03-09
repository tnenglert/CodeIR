"""Token counting helpers with optional tiktoken support."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=8)
def _get_tiktoken_encoder(encoding_name: str = "cl100k_base"):
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return None


def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Count text tokens for compression decisions.

    Uses tiktoken when available, then falls back to len(text)/4 approximation.
    The encoding_name can be 'cl100k_base' (GPT-3.5/4) or 'o200k_base' (GPT-4o).
    """
    if not text:
        return 0

    enc = _get_tiktoken_encoder(encoding_name)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass

    # Fallback: standard approximation of ~4 characters per token for English/code.
    return max(1, len(text) // 4)
