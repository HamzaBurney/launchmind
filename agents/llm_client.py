"""
Shared LLM client utilities.

Supports OpenAI-compatible API calls for OpenAI, Groq, and Gemini.
"""

import os
import re
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


VALID_PROVIDERS = {"openai", "groq", "gemini"}
DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


_client_cache: dict[tuple[str, str], OpenAI] = {}


def _provider() -> str:
    provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider not in VALID_PROVIDERS:
        allowed = ", ".join(sorted(VALID_PROVIDERS))
        raise ValueError(f"Invalid LLM_PROVIDER='{provider}'. Allowed values: {allowed}")
    return provider


def _provider_config(provider: str) -> tuple[str, str, str]:
    if provider == "openai":
        key_name = "OPENAI_API_KEY"
        api_key = os.environ.get(key_name, "")
        model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
        return api_key, model, base_url

    if provider == "groq":
        key_name = "GROQ_API_KEY"
        api_key = os.environ.get(key_name, "")
        model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        base_url = os.environ.get("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL).strip()
        return api_key, model, base_url

    key_name = "GEMINI_API_KEY"
    api_key = os.environ.get(key_name, "")
    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    base_url = os.environ.get("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL).strip()
    return api_key, model, base_url


def _provider_key_name(provider: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }[provider]


def _get_client(api_key: str, base_url: str) -> OpenAI:
    cache_key = (api_key, base_url)
    if cache_key not in _client_cache:
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client_cache[cache_key] = OpenAI(**kwargs)
    return _client_cache[cache_key]


def _retry_wait_seconds(error_text: str, attempt: int, base_delay: float) -> float:
    """Infer wait seconds from provider text, otherwise use exponential backoff."""
    match = re.search(r"try again in\s*([0-9]*\.?[0-9]+)s", error_text, re.IGNORECASE)
    if match:
        try:
            return min(float(match.group(1)) + 0.5, 30.0)
        except ValueError:
            pass
    return min(base_delay * (2 ** attempt), 30.0)


def _normalize_message_content(message: object) -> str:
    """Extract text from multiple SDK content shapes safely."""
    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    parts.append(part.strip())
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                continue
            text_attr = getattr(part, "text", None)
            if isinstance(text_attr, str) and text_attr.strip():
                parts.append(text_attr.strip())
        return "\n".join(parts).strip()

    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        return refusal.strip()

    return ""


def generate_text(system: str, user: str, max_tokens: int = 2000) -> str:
    provider = _provider()
    api_key, model, base_url = _provider_config(provider)

    if not api_key:
        key_name = _provider_key_name(provider)
        raise RuntimeError(f"Missing required environment variable: {key_name}")

    client = _get_client(api_key=api_key, base_url=base_url)
    max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))
    base_delay = float(os.environ.get("LLM_RETRY_BASE_SECONDS", "2"))

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )

            message = response.choices[0].message
            content_text = _normalize_message_content(message)
            if content_text:
                return content_text

            if attempt >= max_retries:
                raise RuntimeError(
                    f"LLM returned an empty response after retries (provider={provider}, model={model})."
                )
            wait_seconds = _retry_wait_seconds("", attempt, base_delay)
            print(f"[LLM] Empty response from {provider}; retrying in {wait_seconds:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait_seconds)
            continue

        except RateLimitError as exc:
            if attempt >= max_retries:
                raise
            wait_seconds = _retry_wait_seconds(str(exc), attempt, base_delay)
            print(f"[LLM] Rate limited by {provider}; retrying in {wait_seconds:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait_seconds)

        except (APIConnectionError, APITimeoutError) as exc:
            if attempt >= max_retries:
                raise
            wait_seconds = _retry_wait_seconds(str(exc), attempt, base_delay)
            print(f"[LLM] Connection/timeout error with {provider}; retrying in {wait_seconds:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait_seconds)

        except APIStatusError as exc:
            # Retry transient server-side failures.
            if exc.status_code >= 500 and attempt < max_retries:
                wait_seconds = _retry_wait_seconds(str(exc), attempt, base_delay)
                print(f"[LLM] Provider status {exc.status_code}; retrying in {wait_seconds:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_seconds)
                continue
            raise

    raise RuntimeError("LLM request failed after retries.")