"""测试环境隔离。

为什么需要：`Settings` 会读取工作目录下的 `.env`。开发者一旦按文档配好本地
`.env`（例如把 `RECOACH_DEEPSEEK_MODEL` 改成自己的模型），断言"默认值"的测试
就会失败——测试结果取决于开发者本机的配置，这不是测试想表达的意思。

这里把配置项固定回代码里的默认值。pydantic-settings 的优先级是
「真实环境变量 > .env 文件」，所以在 import 阶段写入 os.environ 即可压过 `.env`。
测试内部仍可用 monkeypatch.setenv / monkeypatch.setattr 覆盖。

同时清掉 `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`：`Settings.effective_*_key`
会回退到这两个无前缀环境变量，而 CI 或某些终端里它们可能已被设置，
会让"是否已配置密钥"的断言随环境漂移。
"""
from __future__ import annotations

import os

# 代码中的默认值。与 app/config.py 的字段默认值保持一致。
_DEFAULTS: dict[str, str] = {
    "RECOACH_DB_PATH": "./recoach.db",
    "RECOACH_DEV_USER": "dev_user",
    "RECOACH_CORS_ORIGINS": "http://127.0.0.1:4173,http://localhost:4173",
    "RECOACH_API_TOKEN": "",
    "RECOACH_ALLOW_LOCAL_WITHOUT_TOKEN": "true",
    "RECOACH_TRUSTED_HOSTS": "",
    "RECOACH_RATE_LIMIT_PER_MINUTE": "30",
    "RECOACH_MAX_CONCURRENT_TURNS": "16",
    "RECOACH_MAX_BODY_BYTES": "65536",
    "RECOACH_LLM_PROVIDER": "template",
    "RECOACH_LLM_BASE_URL": "",
    "RECOACH_LLM_API_KEY": "",
    "RECOACH_LLM_MODEL": "",
    "RECOACH_DEEPSEEK_API_KEY": "",
    "RECOACH_DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "RECOACH_DEEPSEEK_MODEL": "deepseek-v4-flash",
    "RECOACH_DEEPSEEK_THINKING": "enabled",
    "RECOACH_DEEPSEEK_REASONING_EFFORT": "medium",
    "RECOACH_ANTHROPIC_API_KEY": "",
    "RECOACH_ANTHROPIC_MODEL": "claude-opus-5",
    "RECOACH_LLM_MAX_TOKENS": "10000",
    "RECOACH_LLM_TIMEOUT": "90",
    "RECOACH_LLM_MAX_CONTINUATIONS": "2",
    "RECOACH_LLM_FAIL_FAST": "false",
    "RECOACH_MEMORY_ON": "true",
    "RECOACH_MEMORY_MAX_SELECTED": "3",
    "RECOACH_MEMORY_HARD_LIMIT": "4",
    "RECOACH_MEMORY_CAPSULE_TOKENS": "280",
    "RECOACH_TOOL_BUDGET": "1",
}

for _key, _value in _DEFAULTS.items():
    os.environ[_key] = _value

# 无前缀回退变量：设置与否取决于运行环境，必须清掉才能让断言稳定。
for _key in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_key, None)
