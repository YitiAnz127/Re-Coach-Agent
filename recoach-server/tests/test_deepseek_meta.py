from __future__ import annotations

from types import SimpleNamespace

from app import db
from app.config import Settings
from app.routes import meta as meta_route
from app.services import coach


def test_deepseek_defaults_use_medium_thinking():
    settings = Settings()

    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.deepseek_thinking == "enabled"
    assert settings.deepseek_reasoning_effort == "medium"


def test_meta_exposes_deepseek_thinking(monkeypatch):
    db.reset_for_tests(":memory:")
    settings = SimpleNamespace(
        memory_on=True,
        deepseek_thinking="enabled",
        deepseek_reasoning_effort="high",
    )
    monkeypatch.setattr(meta_route, "get_settings", lambda: settings)
    monkeypatch.setattr(coach, "resolve_provider", lambda: ("deepseek", "deepseek-v4-flash"))

    payload = meta_route.meta()["data"]["llm"]

    assert payload == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "configured": True,
        "thinkingEnabled": True,
        "reasoningEffort": "high",
    }
