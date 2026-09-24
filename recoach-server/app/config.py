from __future__ import annotations

from functools import lru_cache
import ipaddress
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 身份标识的合法字符集与长度。它会进入 SQLite、参与作用域判定并出现在日志里，
# 因此必须限制，避免异常值与日志注入。
# 定义在 config 层：Settings 需要用它校验 locked_user，而 routes 与 auth 也要用同一份
# 规则（config 不反向依赖任何 app 模块，放这里不会形成循环导入）。
USER_ID_RE = re.compile(r"^[A-Za-z0-9._@:-]{1,128}$")


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
    # 进程实例标识。启动时的"遗留 streaming Turn 恢复"只作用于本实例创建的 Turn，
    # 避免多实例部署下误标别的进程正在流式输出的轮次（见 services/instances.py）。
    # 留空 = 使用库内持久化的随机标识（DB 在挂载卷上，重启/重建容器后保持不变）。
    # 仅当同一份 DB 上跑多个进程且它们共享该标识时才需要显式设置：
    #   uvicorn --workers 4 时，给每个 worker 设不同的 RECOACH_INSTANCE_ID。
    instance_id: str = ""

    # 单用户锁定。设置后所有请求的身份恒为该用户，x-user-id 请求头不再有话语权；
    # 显式指定另一个身份会被拒绝（401），而不是被静默忽略。
    #
    # 背景：本项目的默认信任模型是"单实例 + 可信客户端"，身份由 x-user-id 表达，
    # 因此**任何持令牌者都能读写他人的记忆与会话**。本机单人使用时这没有实际风险；
    # 一旦把服务暴露给更多人（同机多账户、内网共享），就必须打开这个锁。
    # 留空 = 保持默认行为（x-user-id 自由取值），行为与之前完全一致。
    locked_user: str = ""
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
    # 【预留，当前无效果】微型实验工具尚未实现（/meta 的 capabilities.microExperiment
    # 如实返回 False），因此这个上限目前不被任何代码读取。保留是为 P1 预留接口形状。
    # 若实现工具：这里就是每次 Turn 允许的工具调用次数上限，同时需要把
    # microExperiment 改成 True——能力声明与实现必须同时变。
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

    @field_validator("locked_user")
    @classmethod
    def _validate_locked_user(cls, value: str) -> str:
        """非法值必须**响亮失败**，不能静默忽略。

        静默忽略等于锁根本没开，而部署方会以为已经隔离好了——
        那比不提供这个开关更危险。所以这里直接让进程起不来。
        """
        cleaned = value.strip()
        if cleaned and not USER_ID_RE.match(cleaned):
            raise ValueError(
                "RECOACH_LOCKED_USER 只允许 [A-Za-z0-9._@:-] 且长度 1..128，"
                f"收到：{cleaned!r}"
            )
        return cleaned


def _is_local_network(host: str) -> bool:
    """主机是否**无法从公网路由**（回环 / RFC1918 / 链路本地 / 保留网段）。

    判据是"公网够不够得着"，而不是"是不是 RFC1918"，所以用 `not is_global`：
    文档段（203.0.113.0/24）、基准段（198.18/15）、CGNAT（100.64/10）同样不可路由，
    允许它们没有风险；而 `is_private` 的集合与这个语义并不重合——它不认 CGNAT，
    却认文档段，两头都会判错。
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def check_base_url_security(base_url: str, has_key: bool) -> str | None:
    """校验携带 API Key 的自定义端点，返回问题说明；合规时返回 None。

    明文 http 会把 `Authorization: Bearer <key>` 直接发到网络上，而这类配置错误
    是**静默的**：请求照常成功，只是密钥一直在明文上路。

    规则（分级，而非一刀切）：
    - https 一律放行；
    - http 放行**回环与私有网段**：后端常跑在容器里，指向宿主机或局域网的推理
      服务（Ollama / vLLM / llama.cpp）是合法部署，一刀切会打断它们；
    - 其余（公网 IP、任何域名的 http）一律阻断——那里没有合法的密钥使用场景。

    与 TUI 的 checkBaseUrlSecurity 有意不同：TUI 跑在用户本机，只有回环可信；
    后端跑在容器里，宿主机/局域网的推理端点是常态。这是刻意的不对称，不是漂移。
    """
    if not base_url or not has_key:
        return None
    try:
        parsed = urlsplit(base_url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
    except ValueError:
        return f"LLM Base URL 不是合法 URL：{base_url}"

    if scheme == "https":
        return None
    if scheme != "http":
        return (
            f"LLM Base URL 使用了不支持的协议 {scheme or '(缺失)'}://，"
            f"请使用 https://：{base_url}"
        )
    if host == "localhost" or _is_local_network(host):
        return None
    # 措辞刻意不说"这是公网地址"：判据只认**地址字面量**，所以
    # `http://127.1/v1` 这类 inet_aton 简写回环（合法但 ipaddress 不认）也会落到
    # 这里。断言它"是公网"会把本地端点的运维引到错误方向；说清"无法确认是本地"
    # 才是事实，也给出了可操作的出路。
    return (
        f"LLM Base URL 使用明文 http:// 且无法确认是本地地址（{host}）："
        "只有回环与私有网段的**地址字面量**允许 http，其余（域名、公网 IP、"
        "简写地址）一律按公网处理。会把 API Key 明文发到网络上，请改用 https://。"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
