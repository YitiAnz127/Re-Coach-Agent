#!/usr/bin/env python3
"""跨文件一致性审计。

这类问题单看任何一个文件都发现不了：改了一个配置项却忘了同步 .env.example，
加了后端字段却没同步前端类型，删了代码却留下调试输出……
本脚本把它们做成可重复执行的检查，改完配置跑一遍即可。

用法（需要后端依赖已安装）：

    cd recoach-server
    python -m venv venv && venv/bin/pip install -r requirements.txt   # 首次
    cd ..
    python tools/consistency_audit.py

    # 或者直接指定后端解释器：
    python tools/consistency_audit.py --python recoach-server/venv/bin/python

退出码：0 = 全部通过，1 = 发现问题（可直接用于 CI）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "recoach-server"
FRONTEND = ROOT / "recoach-frontend"
# TUI 是本仓库的**同级目录**（独立仓库）。缺失时跳过相关检查，
# 不能因为别人只 clone 了这个仓库就报错。
TUI = ROOT.parent / "re-coach-tui"

issues: list[tuple[str, str]] = []
notes: list[str] = []


def err(check: str, detail: str) -> None:
    issues.append((check, detail))


def note(message: str) -> None:
    notes.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_settings_module():
    """在后端目录里导入 app.config，返回 (Settings, ERRORS)。

    需要后端依赖（pydantic-settings 等）已安装。缺失时给出可操作的提示，
    而不是抛一堆 ImportError 堆栈。
    """
    sys.path.insert(0, str(BACKEND))
    cwd = os.getcwd()
    try:
        os.chdir(BACKEND)
        from app.config import Settings  # noqa: PLC0415
        from app.errors import ERRORS  # noqa: PLC0415

        return Settings, ERRORS
    except ModuleNotFoundError as exc:
        print(f"无法导入后端配置：{exc.name} 未安装。", file=sys.stderr)
        print(
            "\n请先在后端目录安装依赖：\n"
            "    cd recoach-server\n"
            "    python -m venv venv && venv/bin/pip install -r requirements.txt\n"
            "或用 --python 指定一个已装好依赖的解释器。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    finally:
        os.chdir(cwd)


# ---------------------------------------------------------------- 配置项一致性

# 有意推荐非默认值的情形，必须在此显式登记理由，否则视为不一致。
INTENTIONAL_DIVERGENCE = {
    "RECOACH_DEEPSEEK_REASONING_EFFORT": "有意推荐 low（附实测延迟数据），代码默认 medium 更保守",
}


def check_config(Settings) -> None:
    env_example = read(BACKEND / ".env.example")
    fields = set(Settings.model_fields.keys())
    documented = {
        m[len("RECOACH_") :].lower()
        for m in re.findall(r"^\s*#?\s*(RECOACH_[A-Z0-9_]+)=", env_example, re.M)
    }

    missing = sorted(fields - documented)
    if missing:
        err("配置项未在 .env.example 说明", ", ".join(missing))
    else:
        note(f"config.py 的 {len(fields)} 个字段全部在 .env.example 有说明")

    unknown = sorted(documented - fields)
    if unknown:
        err(".env.example 含不存在的配置项（拼写错误？）", ", ".join(unknown))

    mismatch: list[str] = []
    for name, field in Settings.model_fields.items():
        key = f"RECOACH_{name.upper()}"
        match = re.search(rf"^{key}=([^\r\n]*)$", env_example, re.M)
        if not match:
            continue
        example_value = match.group(1).strip()
        default = field.default
        if not example_value or "${" in example_value:
            continue
        if key in INTENTIONAL_DIVERGENCE:
            note(f"{key} 非默认值属有意推荐（{INTENTIONAL_DIVERGENCE[key]}）")
            continue
        if isinstance(default, bool):
            same = example_value.lower() == str(default).lower()
        elif isinstance(default, (int, float)):
            try:
                same = float(example_value) == float(default)
            except ValueError:
                same = False
        else:
            same = str(default) == example_value
        if not same:
            mismatch.append(f"{key}: example={example_value} default={default!r}")
    if mismatch:
        err(".env.example 与 config.py 默认值不一致", "; ".join(mismatch))
    else:
        note(".env.example 的显式取值与 config.py 默认值一致")


def check_error_codes(ERRORS) -> None:
    errors_src = read(BACKEND / "app" / "errors.py")
    for code in ERRORS:
        if f'"{code}"' not in errors_src:
            err("错误码未在 ERRORS 中定义", code)
    note(f"错误码 {len(ERRORS)} 个：{', '.join(sorted(ERRORS))}")

    # 客户端若硬编码错误码比较，新增错误码会静默失效
    for path in (
        FRONTEND / "src" / "services" / "sse-protocol.ts",
        FRONTEND / "src" / "services" / "agent-client.ts",
    ):
        if not path.exists():
            continue
        hard = re.findall(r'code\s*===\s*"([A-Z_]+)"', read(path))
        if hard:
            err("前端硬编码错误码比较（新错误码会失效）", f"{path.name}: {hard}")


def check_metrics_contract(Settings, ERRORS) -> None:
    schemas = read(BACKEND / "app" / "schemas.py")
    match = re.search(r"class Metrics\(BaseModel\):(.*?)(?=\nclass |\Z)", schemas, re.S)
    if not match:
        err("无法解析后端 Metrics 字段", "")
        return
    backend_fields = set(re.findall(r"^\s{4}(\w+):", match.group(1), re.M))

    targets = [("前端", FRONTEND / "src" / "types.ts", r"interface PerformanceMetrics \{(.*?)\}")]
    if TUI.exists():
        targets.append(("TUI", TUI / "src" / "types.ts", r"interface TurnMetrics \{(.*?)\}"))
    else:
        note("未找到同级 TUI 目录，跳过 TUI 类型对齐检查")

    for label, path, pattern in targets:
        if not path.exists():
            err(f"{label} 类型文件不存在", str(path))
            continue
        found = re.search(pattern, read(path), re.S)
        fields = set(re.findall(r"^\s{2}(\w+)\??:", found.group(1), re.M)) if found else set()
        missing = sorted(backend_fields - fields)
        if missing:
            err(f"后端 Metrics 字段在{label}缺失", ", ".join(missing))
    if not any("Metrics" in check for check, _ in issues):
        note(f"Metrics 的 {len(backend_fields)} 个字段在各端类型中齐全")


# ---------------------------------------------------------------- 代码卫生

LEFTOVER = [
    (r"\bprint\(", "print 调试输出"),
    (r"\bbreakpoint\(\)", "breakpoint()"),
    (r"\bconsole\.log\(", "console.log"),
    (r"\bdebugger\b", "debugger"),
    (r"XXX|HACK:|FIXME:", "标记注释"),
]
# 演示内容文件：里面的代码片段是给用户看的示例，不是调试残留
FIXTURE_FILES = {"src/data/demo.ts"}


def _scan_dirs() -> list[Path]:
    dirs = [BACKEND / "app", FRONTEND / "src"]
    if TUI.exists():
        dirs.append(TUI / "src")
    return [d for d in dirs if d.exists()]


def check_leftovers() -> None:
    found: list[str] = []
    for base in _scan_dirs():
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".ts", ".tsx"):
                continue
            if any(part in ("node_modules", "__pycache__", "dist") for part in path.parts):
                continue
            if path.name == "demo.ts" and "data" in path.parts:
                continue
            for lineno, line in enumerate(read(path).splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("#", "//", "*")):
                    continue
                for pattern, label in LEFTOVER:
                    match = re.search(pattern, line)
                    if not match:
                        continue
                    before = line[: match.start()]
                    # 字符串字面量里的示例文本不算（奇数个引号 = 位于字符串内）
                    if before.count('"') % 2 == 1 or before.count("'") % 2 == 1:
                        continue
                    found.append(f"{path.relative_to(ROOT)}:{lineno} {label}")
                    break
    if found:
        err("残留调试代码", "; ".join(found[:8]))
    else:
        note("未发现 print/console.log/debugger/TODO 残留")


def check_dead_files() -> None:
    for pattern in ("*.old", "*.orig", "*.rej", "*~"):
        for path in BACKEND.rglob(pattern):
            if "node_modules" in path.parts:
                continue
            err("疑似死代码文件仍在", str(path.relative_to(ROOT)))


def check_control_chars() -> None:
    bad: set[str] = set()
    for base in _scan_dirs():
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".ts", ".tsx"):
                continue
            if any(part in ("node_modules", "__pycache__", "dist") for part in path.parts):
                continue
            try:
                text = read(path)
            except UnicodeDecodeError:
                continue
            for ch in text:
                code = ord(ch)
                # 保留 TAB / LF / CR（CRLF 是正常换行）
                if (code < 32 and code not in (9, 10, 13)) or 127 <= code <= 159:
                    bad.add(str(path.relative_to(ROOT)))
                    break
    if bad:
        err("源码含控制字符", ", ".join(sorted(bad)[:6]))
    else:
        note("源码无控制字符残留")


def check_gitignore() -> None:
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        err("缺少 .gitignore", "机密与数据文件可能被提交")
        return
    content = read(gitignore)
    required = [".env", "data/", "*.db", "node_modules/", "__pycache__/"]
    missing = [p for p in required if p not in content]
    if missing:
        err(".gitignore 缺少规则", ", ".join(missing))
    else:
        note(".gitignore 覆盖 .env / 数据 / 依赖 / 构建产物")


# ---------------------------------------------------------------- 入口

def main() -> int:
    parser = argparse.ArgumentParser(description="跨文件一致性审计")
    parser.add_argument(
        "--python",
        help="用于导入后端配置的解释器（默认当前解释器）",
        default=None,
    )
    args = parser.parse_args()

    if args.python and Path(args.python).resolve() != Path(sys.executable).resolve():
        # 换解释器重跑自己，把后端依赖问题交给那个环境
        return subprocess.call([args.python, str(Path(__file__).resolve())])

    Settings, ERRORS = load_settings_module()

    check_config(Settings)
    check_error_codes(ERRORS)
    check_metrics_contract(Settings, ERRORS)
    check_leftovers()
    check_dead_files()
    check_control_chars()
    check_gitignore()

    print("=" * 76)
    print("跨文件一致性审计")
    print("=" * 76)
    print("\n【通过项】")
    for item in notes:
        print(f"  [OK] {item}")
    if issues:
        print(f"\n【发现 {len(issues)} 个问题】")
        for check, detail in issues:
            print(f"  [!!] {check}")
            print(f"       {detail}")
    else:
        print("\n【无问题】")
    print("=" * 76)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
