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
    # 桩对象必须覆盖 meta() 实际读到的每个字段，否则改动路由会以
    # AttributeError 的形式暴露为"测试挂了"而不是"契约变了"。
    settings = SimpleNamespace(
        memory_on=True,
        deepseek_thinking="enabled",
        deepseek_reasoning_effort="high",
        effective_deepseek_key="",
        effective_anthropic_key="",
        llm_api_key="",
        llm_base_url="",
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
        # keysPresent 只暴露"是否配了密钥"的布尔值，不含密钥内容
        "keysPresent": {
            "deepseek": False,
            "anthropic": False,
            "openaiCompatible": False,
        },
    }
