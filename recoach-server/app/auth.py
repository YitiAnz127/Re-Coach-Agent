"""访问控制中间件。

信任模型（v1.1 起）：

- 身份仍然由 `x-user-id` 请求头表达，但该头**只在调用方已通过鉴权之后**才可信。
  鉴权方式为共享令牌 `RECOACH_API_TOKEN`（`Authorization: Bearer <token>`）。
- 未配置令牌时进入**开发模式**：`/api/v1` 只接受本机回环客户端，其余一律 401。
  绝不静默放行——这是修复 v1.1 之前"任何人设置 x-user-id 即可读写他人数据"的关键。
- `/health` 始终免鉴权，供容器健康检查使用。

多用户/公网部署应在反向代理层再做一层认证，并把令牌换成签名会话；
当前实现定位是"单实例 + 可信客户端"，令牌只解决"谁能访问"，
不解决"用户之间如何隔离到不可伪造"。
"""
from __future__ import annotations

import hmac
import ipaddress
import json
from typing import Any

from .config import get_settings
from .errors import error_payload

# ASGI 服务器与测试客户端的本机标识
_LOOPBACK_HOSTS = frozenset(
    {"127.0.0.1", "::1", "localhost", "testclient", "::ffff:127.0.0.1"}
)

PROTECTED_PREFIX = "/api/"
PUBLIC_PATHS = frozenset({"/health"})

# 解析结果按原始配置串缓存，避免每请求重复解析
_trusted_cache: tuple[str, tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] | None = None


def trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """解析 RECOACH_TRUSTED_HOSTS（逗号分隔的 IP 或 CIDR）。

    格式不合法的条目会被跳过——方向是**失败关闭**：不被信任即拒绝，
    不会因为写错配置而放开访问。改完配置若仍然全部 401，先检查这里。
    """
    global _trusted_cache
    raw = get_settings().trusted_hosts.strip()
    if _trusted_cache is not None and _trusted_cache[0] == raw:
        return _trusted_cache[1]

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    _trusted_cache = (raw, tuple(networks))
    return _trusted_cache[1]


def is_loopback_host(host: str | None) -> bool:
    """判断客户端地址是否为本机回环。"""
    value = (host or "").strip().lower()
    if not value:
        return False
    if value in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def is_local_source(host: str | None) -> bool:
    """开发模式下的来源判定：回环，或显式配置的可信网段。

    为什么需要可信网段：docker-compose 里前端 nginx 把 /api 反代到后端时，
    后端看到的来源是 **nginx 容器的内网 IP**，不是回环。只认回环会让默认
    部署的每个 API 调用都 401。可信网段由部署方显式声明（见 compose 的
    RECOACH_TRUSTED_HOSTS），未声明时维持"只信回环"的最严默认。
    """
    if is_loopback_host(host):
        return True
    value = (host or "").strip().lower()
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in trusted_networks())


def client_host(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    if not client:
        return ""
    return client[0] if isinstance(client, (tuple, list)) and client else ""


def bearer_token(scope: dict[str, Any]) -> str:
    """从 Authorization 头提取 Bearer 令牌；缺失或格式不对返回空串。"""
    for key, value in scope.get("headers", []):
        if key.lower() != b"authorization":
            continue
        try:
            raw = value.decode("latin-1").strip()
        except Exception:
            return ""
        prefix, _, token = raw.partition(" ")
        if prefix.lower() != "bearer":
            return ""
        return token.strip()
    return ""


def authorize(scope: dict[str, Any]) -> tuple[bool, str]:
    """返回 (是否放行, 拒绝原因)。原因仅用于服务端日志与错误码。"""
    settings = get_settings()

    if settings.auth_enabled:
        presented = bearer_token(scope)
        # 常量时间比较，避免通过响应时间逐字节探测令牌。
        # 必须比较 **bytes**：compare_digest 对含非 ASCII 的 str 会抛
        # TypeError（"comparing strings with non-ASCII characters is not supported"），
        # 客户端只要发一个 0xFF 字节就能让鉴权抛异常变成 500 而不是干净的 401。
        expected = settings.api_token.strip()
        if not presented or not hmac.compare_digest(
            presented.encode("utf-8", "surrogateescape"),
            expected.encode("utf-8", "surrogateescape"),
        ):
            return False, "BAD_TOKEN"
        return True, ""

    # 开发模式：只信任本机回环 + 显式声明的可信网段
    if not settings.allow_local_without_token:
        return False, "TOKEN_REQUIRED"
    if not is_local_source(client_host(scope)):
        return False, "NOT_LOCAL"
    return True, ""


class AuthMiddleware:
    """纯 ASGI 鉴权中间件。

    与其他中间件一样手写 send 层，避免 BaseHTTPMiddleware 在异常路径丢失响应头。
    必须挂在 CORSMiddleware 内层，这样 401 响应也能带上 CORS 头与 x-request-id。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS or not path.startswith(PROTECTED_PREFIX):
            await self.app(scope, receive, send)
            return

        allowed, _reason = authorize(scope)
        if not allowed:
            await self._deny(scope, send)
            return

        scope.setdefault("state", {})["authenticated"] = True
        await self.app(scope, receive, send)

    async def _deny(self, scope: dict[str, Any], send) -> None:
        request_id = (scope.get("state") or {}).get("request_id") or ""
        settings = get_settings()
        payload = json.dumps(
            {"error": error_payload("UNAUTHORIZED", request_id=request_id or None)},
            ensure_ascii=False,
        ).encode("utf-8")

        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("latin-1")),
            (b"cache-control", b"no-store"),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("latin-1")))
        if settings.auth_enabled:
            headers.append((b"www-authenticate", b'Bearer realm="recoach"'))

        await send(
            {"type": "http.response.start", "status": 401, "headers": headers}
        )
        await send({"type": "http.response.body", "body": payload})
