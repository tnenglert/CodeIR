"""Token counting helpers with optional tiktoken support."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=8)
def _get_tiktoken_encoder(encoding_name: str = "o200k_base"):
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return None


def count_tokens(text: str, encoding_name: str = "o200k_base") -> int:
    """Count text tokens for compression decisions.

    Uses tiktoken when available, then falls back to len(text)/4 approximation.
    Default encoding is 'o200k_base' (GPT-4o and o-series, the most current
    public tokenizer); 'cl100k_base' (GPT-3.5/4 era) remains selectable.
    Note: counts are tokenizer-specific. For benchmark accounting, prefer the
    usage fields the model API actually reports (including cache reads) over
    any static count.
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
