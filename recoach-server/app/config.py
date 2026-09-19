from __future__ import annotations

from functools import lru_cache
import os
from typing import Literal

from pydantic import Field
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

    # 鉴权（见 app/auth.py）
    # 为空 = 开发模式：/api/v1 仅接受本机回环客户端，其余一律 401。
    # 非空 = 令牌模式：所有 /api/v1 请求必须携带 Authorization: Bearer <token>。
    # 生产部署必须设置；生成方式：python -c "import secrets;print(secrets.token_urlsafe(32))"
    api_token: str = ""
    # 开发模式下是否允许本机无凭证访问。置 False 可强制本机也带 token。
    allow_local_without_token: bool = True
    # 开发模式下额外信任的 IP / CIDR（逗号分隔）。
    # 用途：docker-compose 里 nginx 反代 /api 时，后端看到的来源是 nginx 容器内网 IP，
    # 不属于回环。只认回环会让默认部署全部 401，因此需要显式声明网桥网段。
    # 留空 = 只信任回环（最严默认）。公网部署不要设置此项，改用 RECOACH_API_TOKEN。
    trusted_hosts: str = ""
    # 是否暴露 /docs 与 /openapi.json。None = 仅开发模式（未配 token）时暴露。
    expose_docs: bool | None = None

    # 限流（每分钟每身份允许的计费型请求数，0 = 关闭）
    # 仅作用于会真实调用 LLM 的端点：创建 Turn 与创建 Fork 对照。
    rate_limit_per_minute: int = Field(default=30, ge=0, le=1_000_000)

    # 同时进行中的流式 Turn 上限（0 = 不限制）。
    # 挡住"开大量 SSE 不读响应"的资源耗尽与全局 DB 锁争用。
    max_concurrent_turns: int = Field(default=16, ge=0, le=100_000)

    # 请求体大小上限（字节，0 = 不限制）。
    # 单条消息上限 4000 字符，64KiB 对合法请求绰绰有余。
    max_body_bytes: int = Field(default=65_536, ge=0, le=100_000_000)

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
    llm_max_tokens: int = Field(default=10_000, ge=1, le=1_000_000)
    llm_timeout: float = Field(default=90.0, gt=0, le=3_600)  # 秒
    llm_max_continuations: int = Field(default=2, ge=0, le=2)
    # 真实模型在首字之前失败时的策略：
    #   False（默认）— 降级为模板兜底，并在本轮 metrics 里披露 fallback/原因
    #   True         — 不降级，直接以 MODEL_UNAVAILABLE 结束本轮
    # 两种都会如实告知用户，区别只是"给个兜底回答"还是"直接报错"。
    llm_fail_fast: bool = False

    # 记忆与预算
    memory_on: bool = True
    memory_max_selected: int = Field(default=3, ge=1, le=100)
    memory_hard_limit: int = Field(default=4, ge=1, le=100)
    memory_capsule_tokens: int = Field(default=280, ge=1, le=100_000)
    tool_budget: int = Field(default=1, ge=0, le=100)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def auth_enabled(self) -> bool:
        """是否处于令牌模式。"""
        return bool(self.api_token.strip())

    @property
    def allow_credentials(self) -> bool:
        """通配符 Origin 下必须关闭凭证，否则浏览器会拒绝且语义危险。"""
        return "*" not in self.cors_origin_list

    @property
    def docs_enabled(self) -> bool:
        """默认只有开发模式暴露交互式文档；生产由 expose_docs 显式开启。"""
        if self.expose_docs is not None:
            return self.expose_docs
        return not self.auth_enabled

    @property
    def effective_deepseek_key(self) -> str:
        return self.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def effective_anthropic_key(self) -> str:
        return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
