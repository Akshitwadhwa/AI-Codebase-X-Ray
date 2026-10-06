import io
import json
from urllib import request

from backend.providers import _gemini_model_id, complete


def test_retired_gemini_model_uses_3_8_flash(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "models/gemini-2.0-flash")
    assert _gemini_model_id() == "gemini-3.8-flash"


def test_gemini_request_uses_3_8_flash_and_skips_thoughts(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    captured: dict = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "thinking"},
                            {"text": '{"answer": "FastAPI", "citations": [], "refused": false}'},
                        ]
                    }
                }
            ]
        }
        return Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    answer = complete("gemini", "What is the backend?", "grounded")
    assert "models/gemini-3.8-flash:generateContent" in captured["url"]
    assert "temperature" not in json.dumps(captured["body"])
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 1200
    assert captured["body"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
    assert answer.answer == "FastAPI"
    assert answer.refused is False
