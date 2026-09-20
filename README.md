# 知返 Re: Coach

[简体中文](README.md) | [English](README_EN.md)

> 面向机器学习与深度学习学习场景的个性化知识讲解与人机复盘 Agent

[![Tests](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml/badge.svg)](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**版本：** v1.1.0（phase `p1`，策略版本 `policy_1.1.0`）——取自运行时 `GET /api/v1/meta`

**核心原则：** 先把问题弄清楚，再检索相关记忆；少调用、低延迟、自动记忆、作用域明确。

---

## 🚀 快速开始

### 使用 Docker（推荐）

```bash
# 克隆项目
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# 一键启动
docker compose up -d

# 访问应用
# 前端: http://127.0.0.1:4173
# API 文档: http://127.0.0.1:8000/docs
# 健康检查: http://127.0.0.1:8000/health
```

`docker-compose.yml` 把两个端口都绑定在回环地址上（`127.0.0.1`），默认只允许本机访问。
这是有意的默认值：内置 Web 客户端不持有访问令牌，把入口改到 `0.0.0.0` 前请先读
[部署指南的访问控制章节](docs/deployment.md#访问控制必读)。

默认 `RECOACH_LLM_PROVIDER=template`，无需模型密钥即可跑通完整协议与记忆闭环。要得到真实学科讲解，
把你的密钥写进 `recoach-server/.env`（应用配置只从那里读，不要写进 compose 的 `environment`）：

```bash
cp recoach-server/.env.example recoach-server/.env
# 编辑 recoach-server/.env，选择 provider 并填 key
docker compose up -d --force-recreate backend
```

详见 [LLM 配置指南](recoach-server/README_LLM_CONFIG.md)。

### 本地开发

```powershell
# 后端（在后端项目根目录执行，即能看到 app/ 的目录）
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

> 本地开发装 `requirements.txt`，**不要**装 `requirements.lock.txt`：锁文件由 Linux 的 `pip freeze`
> 生成、不保留环境标记，其中的 `uvloop` 在 Windows 上无法编译，会让整条安装失败。锁文件是给镜像用的。

```bash
# 前端（在前端项目根目录执行）
npm ci
npm run dev        # http://127.0.0.1:4173；/api 由 Vite 代理到 127.0.0.1:8000
```

前端默认就是连真实后端（`VITE_API_BASE_URL` 缺省为同源 `/api/v1`），仓库**没有**提供前端
`.env.example`；只有需要脱机演示时才自建 `.env.local` 写 `VITE_DEMO_MODE=true`。
macOS / Linux 把上面的 PowerShell 命令换成等价的 `python3 -m venv` / `source .venv/bin/activate`。

**TUI（终端版）**：`re-coach-tui/` 是独立应用，不依赖 `recoach-server`：

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

**详细文档**: [docs/quickstart.md](docs/quickstart.md)（[EN](docs/quickstart.en.md)）

---

## 📊 项目状态

- ✅ 四个可独立运行的部分：后端、Web 前端、TUI、AI Coach Skill
- ✅ Docker 容器化；GitHub Actions 跑后端测试与一致性审计、前端类型检查与测试、镜像可构建
- ✅ 能力诚实性可查询：`GET /api/v1/meta` 的 `capabilities` 逐项声明已实现与未实现
- 🟢 单用户本地自用，可运行、可部署

---

## 💡 项目概述

**知返 Re: Coach** 将 AI Coach 的教学策略与反馈记忆系统结合，记住用户如何理解知识、当前卡在哪个命题、怎样讲更有效，并在后续问题中自动应用用户偏好的方法。

一次 Turn 的真实链路：

```text
最小 Session 状态理解
  → Clarification Gate（必要时只问一个高信息量问题）
  → 形成 ResolvedTask
  → 五级作用域检索稳定记忆
  → 确定性 Context Compiler 编译上下文（无额外 Planner LLM）
  → 单次主 Coach 流式输出
  → 行为与结果写入 Event Ledger
```

### 核心特性

- 🧠 **智能澄清**：三态门控 `READY` / `NEEDS_CLARIFICATION` / `ANSWER_WITH_ASSUMPTION`，最多连续两轮
- 📝 **作用域记忆**：五级作用域（用户 / 领域 / 概念 / 命题 / 任务）防止错误泛化
- ⚡ **流式响应**：SSE 实时推送，思考过程（`reasoning_content`）与正文分别可见、分别计时
- 🎯 **确定性编译**：记忆如何进入上下文由规则决定，可追溯、可复现
- 🔄 **自动学习**：从反馈中提取稳定偏好，同作用域更新保留 `archived + superseded_by` 演化链
- 🔬 **公平对照**：Fair Fork 冻结当前会话，由服务端并行生成固定 On/Off 两个只读分支
- 🔒 **能力诚实**：未实现的能力在 `/api/v1/meta` 中一律为 `false`，界面不显示占位假数据

---

## 🧩 仓库组成

| 目录 | 内容 | 说明 |
|---|---|---|
| `recoach-server/` | FastAPI + SQLite 后端 | 会话、记忆、事件与指标的唯一真相来源 |
| `recoach-frontend/` | React Web 客户端 | 对话、思考折叠、个性化依据、Fair Fork 对照 |
| `re-coach-tui/` | 终端 TUI（Node ≥ 22.19） | 独立应用，在本进程内用 TypeScript 实现同一套业务逻辑 |
| `ai-coach-skill-repo/` | AI Coach Skill | 面向 Hermes / Codex / Claude Desktop 的行为层，与上面三个应用无代码耦合 |
| `docs/` | 中英双语文档 | 快速开始、部署指南 |
| `tools/` | `consistency_audit.py` | 跨文件一致性审计 |

---

## 🛠 技术栈

**前端**：React 19 + TypeScript 5.9 + Vite 8 + KaTeX（`react-markdown` / `remark-math` / `rehype-katex`）

**后端**：FastAPI + SQLite（WAL + FTS5，不可用时降级 LIKE）+ SSE；镜像与 CI 使用 Python 3.10，本地 venv 使用 3.11

**TUI**：TypeScript + `@earendil-works/pi-tui` + chalk，JSON 文件持久化

**LLM**：`template`（默认，无需密钥）/ `openai_compatible` / `deepseek` / `anthropic`

---

## 📖 文档

| 文档 | 内容 |
|---|---|
| [快速开始](docs/quickstart.md) · [EN](docs/quickstart.en.md) | 5 分钟跑起来 |
| [部署指南](docs/deployment.md) · [EN](docs/deployment.en.md) | 本地 / Docker / 生产、访问控制、限流、备份 |
| [项目结构](PROJECT_STRUCTURE.md) | 目录与关键文件说明 |
| [后端文档](recoach-server/README.md) | 架构、API、已实现与未实现边界 |
| [后端 LLM 配置](recoach-server/README_LLM_CONFIG.md) | DeepSeek / OpenAI 兼容 / Anthropic 接入 |
| [前端文档](recoach-frontend/README.md) | 组件、SSE 契约、逻辑 Turn 重试 |
| [TUI 文档](re-coach-tui/README.md) | 终端版能力与后端模块对应关系 |
| [AI Coach Skill](ai-coach-skill-repo/README.md) · [EN](ai-coach-skill-repo/README_EN.md) | 行为层设计说明 |
| [贡献指南](CONTRIBUTING.md) | 开发流程与代码规范 |

---

## 🧪 测试

```bash
# 后端：18 个测试文件，217 passed
cd recoach-server && ./.venv/Scripts/python.exe -m pytest -q

# 前端：4 个测试文件，22 passed（Node 内置 test runner）
cd recoach-frontend && npm test

# TUI：6 个测试文件，92 passed（vitest）
cd re-coach-tui && npm test
```

> 数字为本机实测结果（Python 3.11 / Node 24）。后端 165 个测试函数经
> `@pytest.mark.parametrize` 展开后是 217 条用例，TUI 的 `it.each` 同理（64 → 92）。
> CI 目前只跑后端与前端两个 job
> （[.github/workflows/test.yml](.github/workflows/test.yml)），TUI 测试尚未接入 CI。

### 跨文件一致性审计

改完配置项或接口字段后跑一遍，能发现单看某个文件看不出来的问题
（配置项加了却忘了写进 `.env.example`、后端加了字段却忘了同步前端类型、
代码里残留调试输出等）：

```bash
python tools/consistency_audit.py
```

退出码 0 表示全部通过，可直接接入 CI。脚本会自行定位仓库位置，
在任意目录下运行都可以；TUI 在本仓库顶层或在同级独立目录时都会被一并检查。

---

## 📄 License

MIT License - 查看 [LICENSE](LICENSE) 了解详情

---

**⭐ 如果有帮助，请给个star！**
