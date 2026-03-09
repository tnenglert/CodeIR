"""Shared model providers for evaluation runners."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# Load .env file if it exists
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value

# Optional imports for each provider
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import google.generativeai as genai
    HAS_GOOGLE = True
except ImportError:
    HAS_GOOGLE = False


class LLMProvider(Protocol):
    """Protocol for LLM providers."""
    model: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        ...


@dataclass
class AnthropicProvider:
    """Anthropic API provider with model alias support."""

    model: str = "claude-haiku-4-5-20251001"
    _client: Any = None

    MODELS = {
        "haiku-4": "claude-haiku-4-5-20251001",
        "sonnet-4": "claude-sonnet-4-20250514",
        "opus-4": "claude-opus-4-20250514",
    }

    def __post_init__(self):
        if not HAS_ANTHROPIC:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        import anthropic
        self._client = anthropic.Anthropic()
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 600,
    ) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,  # Deterministic output for reproducible benchmarks
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        input_tokens = response.usage.input_tokens
        return response.content[0].text, input_tokens


@dataclass
class OpenAIProvider:
    """OpenAI API provider with model alias support."""

    model: str = "gpt-4o-mini"
    _client: Any = None

    MODELS = {
        "gpt-4.1": "gpt-4.1",
        "gpt-4.1-mini": "gpt-4.1-mini",
        "gpt-4o-mini": "gpt-4o-mini",
        "gpt-4o": "gpt-4o",
        "gpt-3.5-turbo": "gpt-3.5-turbo",
        "o1-mini": "o1-mini",
    }

    def __post_init__(self):
        if not HAS_OPENAI:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        import openai
        self._client = openai.OpenAI()
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 600,
    ) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0,  # Deterministic output for reproducible benchmarks
        )
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        return response.choices[0].message.content, input_tokens


@dataclass
class GoogleProvider:
    """Google Gemini API provider with model alias support."""

    model: str = "gemini-2.0-flash"
    rate_limit_seconds: float = 13.0  # Free tier: 5 req/min = 12s minimum, use 13 for safety
    _client: Any = None
    _last_call_time: float = 0.0

    MODELS = {
        "gemini-flash": "gemini-2.0-flash",
        "gemini-2.0-flash": "gemini-2.0-flash",
        "gemini-1.5-flash": "gemini-1.5-flash",
        "gemini-2.5-flash": "gemini-2.5-flash",
        "gemini-pro": "gemini-1.5-pro",
    }

    def __post_init__(self):
        if not HAS_GOOGLE:
            raise RuntimeError("google-generativeai package not installed. Run: pip install google-generativeai")
        import google.generativeai as genai
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]
        self._client = genai.GenerativeModel(self.model)

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 600,
    ) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        import time

        # Rate limiting for free tier (5 requests/minute)
        if self._last_call_time > 0:
            elapsed = time.time() - self._last_call_time
            if elapsed < self.rate_limit_seconds:
                sleep_time = self.rate_limit_seconds - elapsed
                time.sleep(sleep_time)

        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        response = self._client.generate_content(
            full_prompt,
            generation_config={
                "max_output_tokens": max_tokens,
                "temperature": 0,  # Deterministic output for reproducible benchmarks
            },
        )
        self._last_call_time = time.time()

        # Gemini doesn't directly report input tokens in the same way
        # Estimate based on prompt length (roughly 4 chars per token)
        input_tokens = len(full_prompt) // 4
        return response.text, input_tokens


@dataclass
class DeepSeekProvider:
    """DeepSeek API provider (OpenAI-compatible API)."""

    model: str = "deepseek-chat"
    _client: Any = None

    MODELS = {
        "deepseek-chat": "deepseek-chat",
        "deepseek-coder": "deepseek-coder",
    }

    def __post_init__(self):
        if not HAS_OPENAI:
            raise RuntimeError("openai package not installed (used for DeepSeek). Run: pip install openai")
        import os
        import openai
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable not set")
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        if self.model in self.MODELS:
            self.model = self.MODELS[self.model]

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 600,
    ) -> tuple[str, int]:
        """Returns (response_text, input_token_count)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0,  # Deterministic output for reproducible benchmarks
        )
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        return response.choices[0].message.content, input_tokens


# Provider registry
PROVIDERS = {
    "anthropic": {
        "class": AnthropicProvider,
        "models": ["haiku-4", "sonnet-4", "opus-4"],
        "default": "haiku-4",
        "available": HAS_ANTHROPIC,
    },
    "openai": {
        "class": OpenAIProvider,
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini", "gpt-3.5-turbo", "gpt-4o", "o1-mini"],
        "default": "gpt-4.1",
        "available": HAS_OPENAI,
    },
    "google": {
        "class": GoogleProvider,
        "models": ["gemini-flash", "gemini-1.5-flash", "gemini-pro"],
        "default": "gemini-flash",
        "available": HAS_GOOGLE,
    },
    "deepseek": {
        "class": DeepSeekProvider,
        "models": ["deepseek-chat", "deepseek-coder"],
        "default": "deepseek-chat",
        "available": HAS_OPENAI,  # Uses openai package
    },
}


def list_available_providers() -> list[str]:
    """Return list of provider names that have required packages installed."""
    return [name for name, info in PROVIDERS.items() if info["available"]]


def select_provider_interactive() -> tuple[str, str]:
    """Interactively prompt user to select provider and model.

    Returns:
        Tuple of (provider_name, model_name)
    """
    available = list_available_providers()

    if not available:
        raise RuntimeError(
            "No LLM providers available. Install one of:\n"
            "  pip install anthropic      # For Anthropic/Claude\n"
            "  pip install openai         # For OpenAI or DeepSeek\n"
            "  pip install google-generativeai  # For Google Gemini"
        )

    print("\n=== Select LLM Provider ===")
    print("Available providers:")
    for i, name in enumerate(available, 1):
        info = PROVIDERS[name]
        models_str = ", ".join(info["models"])
        print(f"  {i}. {name} (models: {models_str})")

    while True:
        try:
            choice = input(f"\nSelect provider [1-{len(available)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(available):
                provider_name = available[idx]
                break
            print(f"Please enter a number between 1 and {len(available)}")
        except ValueError:
            print("Please enter a valid number")

    # Select model
    info = PROVIDERS[provider_name]
    models = info["models"]
    default_model = info["default"]

    print(f"\nAvailable models for {provider_name}:")
    for i, model in enumerate(models, 1):
        default_marker = " (default)" if model == default_model else ""
        print(f"  {i}. {model}{default_marker}")

    while True:
        try:
            choice = input(f"Select model [1-{len(models)}, or Enter for default]: ").strip()
            if not choice:
                model_name = default_model
                break
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                model_name = models[idx]
                break
            print(f"Please enter a number between 1 and {len(models)}")
        except ValueError:
            print("Please enter a valid number")

    print(f"\nSelected: {provider_name} / {model_name}\n")
    return provider_name, model_name


def ensure_api_key(provider_name: str) -> None:
    """Prompt for API key if not set in environment.

    Args:
        provider_name: Provider name to check/prompt for
    """
    import os

    env_var_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    env_var = env_var_map.get(provider_name)
    if not env_var:
        return

    if os.environ.get(env_var):
        return

    print(f"\n{env_var} not set.")
    api_key = input(f"Enter your {provider_name.title()} API key: ").strip()
    if api_key:
        os.environ[env_var] = api_key
        print("API key set for this session.\n")
    else:
        raise RuntimeError(f"{env_var} is required but not provided")


def create_provider(provider_name: str, model: str) -> LLMProvider:
    """Create a provider instance.

    Args:
        provider_name: One of 'anthropic', 'openai', 'google', 'deepseek'
        model: Model name or alias

    Returns:
        Provider instance
    """
    if provider_name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_name}. Available: {list(PROVIDERS.keys())}")

    info = PROVIDERS[provider_name]
    if not info["available"]:
        raise RuntimeError(f"Provider {provider_name} not available (missing package)")

    # Prompt for API key if not set
    ensure_api_key(provider_name)

    return info["class"](model=model)
