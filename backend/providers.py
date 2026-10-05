from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .models import Citation, ModelAnswer

SYSTEM = (
    "You answer questions about one scanned software repository. "
    "Use only the context, including the scan-summary line and dependency or framework import lines. "
    "If the context does not contain the answer, set refused to true and say what is missing. "
    "Return one JSON object and no other text: "
    '{"answer": "...", "citations": [{"path": "file", "line": 1}], "refused": false}. '
    "Every citation path must appear in the context. line is 1-based or null."
)

PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "XAI_API_KEY",
}


class ProviderError(RuntimeError):
    """The selected model could not be called."""


def configured_providers() -> list[dict[str, str | bool]]:
    providers = [{"id": "offline", "label": "Offline excerpts", "configured": True}]
    labels = {"openai": "GPT", "anthropic": "Claude", "gemini": "Gemini", "grok": "Grok"}
    for provider, env_name in PROVIDER_ENV.items():
        providers.append({"id": provider, "label": labels[provider], "configured": bool(os.getenv(env_name, "").strip())})
    return providers


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"{exc.code} from the model provider. {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError("Could not reach the model provider") from exc


def _openai_compatible(base_url: str, env_name: str, model: str, prompt: str) -> str:
    key = os.getenv(env_name, "").strip()
    if not key:
        raise ProviderError(f"Set {env_name} in .env")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    }
    body = _post_json(f"{base_url}/chat/completions", payload, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("The model provider returned an unexpected response") from exc


def _complete_remote(provider: str, prompt: str) -> str:
    if provider == "openai":
        return _openai_compatible("https://api.openai.com/v1", "OPENAI_API_KEY", os.getenv("OPENAI_MODEL", "gpt-4o-mini"), prompt)
    if provider == "grok":
        return _openai_compatible("https://api.x.ai/v1", "XAI_API_KEY", os.getenv("XAI_MODEL", "grok-3"), prompt)
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ProviderError("Set ANTHROPIC_API_KEY in .env")
        body = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022"),
                "max_tokens": 800,
                "temperature": 0,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        try:
            return body["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The model provider returned an unexpected response") from exc
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise ProviderError("Set GEMINI_API_KEY in .env")
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        body = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            {"contents": [{"parts": [{"text": SYSTEM + "\n\n" + prompt}]}], "generationConfig": {"temperature": 0}},
            {"Content-Type": "application/json"},
        )
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The model provider returned an unexpected response") from exc
    raise ProviderError("Choose offline, openai, anthropic, gemini, or grok")


def parse_model_json(text: str, provider: str, mode: str) -> ModelAnswer:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        try:
            data = json.loads(cleaned[start : end + 1]) if start >= 0 and end > start else {}
        except json.JSONDecodeError:
            data = {}
    if not data:
        return ModelAnswer(answer=text.strip() or "The model returned an empty answer.", provider=provider, refused=False, mode=mode)
    citations = []
    for item in data.get("citations") or []:
        if isinstance(item, dict) and item.get("path"):
            line = item.get("line")
            citations.append(Citation(path=str(item["path"]), line=line if isinstance(line, int) else None))
    return ModelAnswer(
        answer=str(data.get("answer") or "").strip() or "The model returned no answer text.",
        citations=citations,
        provider=provider,
        refused=bool(data.get("refused")),
        mode=mode,
    )


def complete(provider: str, prompt: str, mode: str) -> ModelAnswer:
    if provider == "offline":
        from .ask import offline_answer

        return offline_answer(prompt, mode)
    return parse_model_json(_complete_remote(provider, prompt), provider, mode)
