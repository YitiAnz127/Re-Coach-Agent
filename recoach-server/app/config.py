from __future__ import annotations

from functools import lru_cache
import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中式配置。所有字段都可用同名环境变量覆盖，见 .env.example。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="RECOACH_", extra="ignore"
    )

    # 基础
    db_path: str = "./recoach.db"
    dev_user: str = "dev_user"
    cors_origins: str = (
        "http://127.0.0.1:4173,http://localhost:4173"
    )

    # 主 Coach 模型
    llm_provider: Literal["template", "openai_compatible", "deepseek", "anthropic"] = (
        "template"
    )
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # DeepSeek 官方 API
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: Literal["enabled", "disabled"] = "enabled"
    
    # 2026-09-04 更新：基于实测性能调整
    # low: 首字3.1s, 端到端10.6s - 推荐交互场景
    # medium: 首字48s, 端到端67s - 深度推理场景
    # high: 首字>60s - 仅用于极复杂推理
    deepseek_reasoning_effort: Literal["low", "medium", "high"] = "medium"

    # Anthropic 官方 API
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    # LLM通用配置
    # 2026-09-04 更新：提升到10000以适应thinking+正文
    # 经验值：low档thinking约2k tokens，正文需4-6k tokens
    llm_max_tokens: int = 10000
    llm_timeout: float = 90.0  # 提升超时以适应thinking延迟
    llm_max_continuations: int = 2

    # 记忆与预算
    memory_on: bool = True
    memory_max_selected: int = 3
    memory_hard_limit: int = 4
    memory_capsule_tokens: int = 280
    tool_budget: int = 1

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def effective_deepseek_key(self) -> str:
        return self.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def effective_anthropic_key(self) -> str:
        return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
