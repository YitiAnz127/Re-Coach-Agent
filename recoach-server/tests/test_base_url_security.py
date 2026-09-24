"""LLM 端点明文 http 防护的回归测试。

背景：TUI 一直有 checkBaseUrlSecurity（明文 http 会阻断启动，附 5 个测试），
后端完全没有这层检查——配上 `RECOACH_LLM_BASE_URL=http://...` 时
`Authorization: Bearer <key>` 会明文上路。这类错误是静默的：请求照常成功。

后端采用分级规则（与 TUI 有意不同）：https 放行；http 放行回环与私有网段
（容器 → 宿主机 / 局域网的 Ollama、vLLM 是合法部署）；公网 http 阻断。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import check_base_url_security, get_settings
from app.main import create_app

KEY = "sk-should-not-travel-in-plaintext"


@pytest.fixture(autouse=True)
def _settings_cache_isolation():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------- 判定函数

@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1",
        "https://api.deepseek.com",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "http://192.168.1.50:8000/v1",   # 局域网推理机
        "http://10.0.0.7:8000/v1",       # 内网
        "http://172.28.0.9:8000/v1",     # docker 网桥
        "http://169.254.10.10:8000/v1",  # 链路本地
        "http://100.64.0.1:8000/v1",     # CGNAT（is_private 会误判为公网）
        "http://198.18.0.1:8000/v1",     # 基准网段（不可路由）
    ],
)
def test_allowed_endpoints(url):
    assert check_base_url_security(url, True) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",     # 域名 → 无法保证不是公网
        "http://8.8.8.8:8000/v1",        # 公网 IP
        "http://93.184.216.34/v1",
        "http://[2606:4700::1111]/v1",   # 公网 IPv6
        "ftp://api.example.com/v1",      # 不支持的协议
        "api.example.com/v1",            # 缺协议头
    ],
)
def test_blocked_endpoints(url):
    problem = check_base_url_security(url, True)
    assert problem, f"{url} 应当被阻断"
    assert "https" in problem or "协议" in problem


def test_predicate_follows_routability_not_rfc1918():
    """判据必须是"公网能否路由"，不是"是不是 RFC1918"。

    回归：初版用 is_private，把文档段 203.0.113.7 当成内网放行，
    却把 CGNAT 100.64.0.1 当成公网阻断——两个方向都判错。
    """
    from app.config import _is_local_network

    assert _is_local_network("192.168.1.1") is True
    assert _is_local_network("100.64.0.1") is True   # CGNAT 不可公网路由
    assert _is_local_network("203.0.113.7") is True  # 文档段不可公网路由
    assert _is_local_network("8.8.8.8") is False
    assert _is_local_network("api.example.com") is False  # 非 IP 字面量一律当公网


def test_no_key_means_nothing_to_protect():
    """没配密钥就没什么可泄露的，不该因此挡住本地推理服务。"""
    assert check_base_url_security("http://203.0.113.7:8000/v1", False) is None
    assert check_base_url_security("", True) is None


# --------------------------------------------------------------- 启动期强制

def _boot(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("RECOACH_DB_PATH", str(tmp_path / "urlsec.db"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    db.reset_for_tests(str(tmp_path / "urlsec.db"))


def test_startup_refuses_public_plaintext_endpoint(tmp_path, monkeypatch):
    """公网明文端点必须让进程起不来，而不是带着密钥裸奔。"""
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="openai_compatible",
        RECOACH_LLM_BASE_URL="http://api.example.com/v1",
        RECOACH_LLM_API_KEY=KEY,
        RECOACH_LLM_MODEL="gpt-x",
    )
    with pytest.raises(RuntimeError) as excinfo:
        create_app()
    assert "https" in str(excinfo.value)
    assert KEY not in str(excinfo.value), "错误信息不得回显密钥"


def test_startup_refuses_public_plaintext_deepseek_endpoint(tmp_path, monkeypatch):
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="deepseek",
        RECOACH_DEEPSEEK_BASE_URL="http://api.deepseek.example/v1",
        RECOACH_DEEPSEEK_API_KEY=KEY,
    )
    with pytest.raises(RuntimeError):
        create_app()


def test_startup_allows_local_inference_over_http(tmp_path, monkeypatch):
    """容器 → 宿主机 / 局域网 http 推理服务是合法部署，不得被挡。"""
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="openai_compatible",
        RECOACH_LLM_BASE_URL="http://192.168.1.50:11434/v1",
        RECOACH_LLM_API_KEY="local-only",
        RECOACH_LLM_MODEL="llama3",
    )
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


def test_startup_allows_https(tmp_path, monkeypatch):
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="deepseek",
        RECOACH_DEEPSEEK_BASE_URL="https://api.deepseek.com",
        RECOACH_DEEPSEEK_API_KEY=KEY,
    )
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


# --------------------------------------------------- 不得拦截「用不到」的配置

def test_unused_endpoint_does_not_block_startup(tmp_path, monkeypatch):
    """provider=template 时 base_url 不参与任何请求，不该因此让服务起不来。

    回归：初版对两个 base url 无条件校验，于是 .env 里残留一个公网 http 的
    llm_base_url 就会让进程启动失败——compose 的 restart: unless-stopped 下
    会变成重启循环，而那个变量根本没被用到。
    """
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="template",
        RECOACH_LLM_BASE_URL="http://api.example.com/v1",
        RECOACH_LLM_API_KEY=KEY,
    )
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


def test_unused_openai_endpoint_does_not_block_when_deepseek_is_active(tmp_path, monkeypatch):
    """生效 provider 是 deepseek 时，llm_base_url 同样不参与请求。"""
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="deepseek",
        RECOACH_DEEPSEEK_API_KEY=KEY,
        RECOACH_DEEPSEEK_BASE_URL="https://api.deepseek.com",
        RECOACH_LLM_BASE_URL="http://api.example.com/v1",
        RECOACH_LLM_API_KEY=KEY,
        RECOACH_LLM_MODEL="gpt-x",
    )
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


def test_active_provider_is_still_blocked(tmp_path, monkeypatch):
    """真正生效的那个端点仍然必须被拦——否则这层校验就白加了。"""
    _boot(
        tmp_path, monkeypatch,
        RECOACH_LLM_PROVIDER="openai_compatible",
        RECOACH_LLM_BASE_URL="http://api.example.com/v1",
        RECOACH_LLM_API_KEY=KEY,
        RECOACH_LLM_MODEL="gpt-x",
    )
    with pytest.raises(RuntimeError):
        create_app()
