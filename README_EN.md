# 知返 Re: Coach

[简体中文](README.md) | [English](README_EN.md)

> A personalized knowledge-explanation and retrospection agent for machine learning & deep learning learning scenarios.

[![Tests](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml/badge.svg)](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Version:** v1.1.0 (phase `p1`, policy version `policy_1.1.0`) — read from the running `GET /api/v1/meta`

**Core principles:** First understand the question, then retrieve relevant memories; minimal calls, low latency, automatic memory, clear scope.

---

## 🚀 Quick Start

### Using Docker (recommended)

```bash
# Clone the repo
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# One-command start
docker compose up -d

# Access the app
# Frontend: http://127.0.0.1:4173
# API docs: http://127.0.0.1:8000/docs
# Health check: http://127.0.0.1:8000/health
```

`docker-compose.yml` binds both ports to loopback (`127.0.0.1`), so only the local machine can reach
them by default. That is deliberate: the bundled web client holds no access token, so read the
[access-control section of the deployment guide](docs/deployment.en.md#access-control-required-reading)
before moving the entry point to `0.0.0.0`.

The default is `RECOACH_LLM_PROVIDER=template`, which needs no model key and still exercises the full
protocol and memory loop. To get real subject-matter explanations, put your key in
`recoach-server/.env` (application config is read only from there — never from Compose `environment`):

```bash
cp recoach-server/.env.example recoach-server/.env
# edit recoach-server/.env: pick a provider and fill in the key
docker compose up -d --force-recreate backend
```

See the [LLM configuration guide](recoach-server/README_LLM_CONFIG.md).

### Local development

```powershell
# Backend (run from the backend project root — the directory containing app/)
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

> Install `requirements.txt` locally, **not** `requirements.lock.txt`: the lock file is produced by Linux
> `pip freeze`, which drops environment markers, and its `uvloop` pin cannot compile on Windows — that
> aborts the whole install. The lock file is for the image.

```bash
# Frontend (run from the frontend project root)
npm ci
npm run dev        # http://127.0.0.1:4173; /api is proxied to 127.0.0.1:8000 by Vite
```

The frontend connects to the real backend by default (`VITE_API_BASE_URL` defaults to the same-origin
`/api/v1`), and the repo ships **no** frontend `.env.example`; create `.env.local` with
`VITE_DEMO_MODE=true` only when you want the offline demo. On macOS / Linux replace the PowerShell
commands with the equivalent `python3 -m venv` / `source .venv/bin/activate`.

**TUI (terminal edition)**: `re-coach-tui/` is a standalone application that does not depend on `recoach-server`:

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

**Detailed docs**: [docs/quickstart.md](docs/quickstart.md) ([EN](docs/quickstart.en.md))

---

## 📊 Project Status

- ✅ Four independently runnable parts: backend, web frontend, TUI, AI Coach Skill
- ✅ Docker containerization; GitHub Actions runs backend tests plus the consistency audit, frontend type-check and tests, and both image builds
- ✅ Honest capability reporting: the `capabilities` object in `GET /api/v1/meta` declares what is and is not implemented
- 🟢 Single-user local use, runnable and deployable

---

## 💡 Overview

**知返 Re: Coach** combines AI Coach teaching strategies with a feedback-memory system: it remembers how you understand concepts, where you're currently stuck on a proposition, and what explanations work for you — then automatically applies your preferred methods in later questions.

What actually happens in one Turn:

```text
Minimal session state
  → Clarification Gate (ask exactly one high-information question when needed)
  → ResolvedTask
  → Five-level scoped retrieval of stable memories
  → Deterministic Context Compiler builds the context (no extra planner LLM)
  → One streaming main-Coach call
  → Behaviour and outcome recorded in the Event Ledger
```

### Core Features

- 🧠 **Smart clarification**: three-state gate `READY` / `NEEDS_CLARIFICATION` / `ANSWER_WITH_ASSUMPTION`, at most two consecutive rounds
- 📝 **Scoped memory**: five-level scope (user / domain / concept / proposition / task) prevents wrong generalization
- ⚡ **Streaming responses**: SSE real-time push; the model's `reasoning_content` is visible and timed separately from the body
- 🎯 **Deterministic compilation**: how memories enter the context is rule-based, traceable and reproducible
- 🔄 **Automatic learning**: stable preferences are extracted from feedback; in-scope updates keep an `archived + superseded_by` chain
- 🔬 **Fair comparison**: a Fair Fork freezes the current session and the server creates two fixed, read-only On/Off branches
- 🔒 **Honest capabilities**: anything unimplemented is `false` in `/api/v1/meta`, and the UI never shows placeholder data

---

## 🧩 Repository Layout

| Directory | Contents | Notes |
|---|---|---|
| `recoach-server/` | FastAPI + SQLite backend | Single source of truth for sessions, memories, events, metrics |
| `recoach-frontend/` | React web client | Conversation, thinking block, personalization evidence, Fair Fork comparison |
| `re-coach-tui/` | Terminal TUI (Node ≥ 22.19) | Standalone app that implements the same business logic in-process with TypeScript |
| `ai-coach-skill-repo/` | AI Coach skill | Behaviour layer for Hermes / Codex / Claude Desktop, with no code coupling to the three apps above |
| `docs/` | Bilingual docs | Quick start and deployment guide |
| `tools/` | `consistency_audit.py` | Cross-file consistency audit |

---

## 🛠 Tech Stack

**Frontend**: React 19 + TypeScript 5.9 + Vite 8 + KaTeX (`react-markdown` / `remark-math` / `rehype-katex`)

**Backend**: FastAPI + SQLite (WAL + FTS5, falling back to LIKE) + SSE; Python 3.10 in the image and CI, 3.11 in the local venv

**TUI**: TypeScript + `@earendil-works/pi-tui` + chalk, JSON file persistence

**LLM**: `template` (default, no key) / `openai_compatible` / `deepseek` / `anthropic`

---

## 📖 Documentation

| Document | Contents |
|---|---|
| [Quick start](docs/quickstart.md) · [EN](docs/quickstart.en.md) | Running it in five minutes |
| [Deployment guide](docs/deployment.md) · [EN](docs/deployment.en.md) | Local / Docker / production, access control, rate limits, backup |
| [Project structure](PROJECT_STRUCTURE.md) | Directories and key files |
| [Backend docs](recoach-server/README.md) | Architecture, API, implemented vs. unimplemented |
| [Backend LLM config](recoach-server/README_LLM_CONFIG.md) | DeepSeek / OpenAI-compatible / Anthropic setup |
| [Frontend docs](recoach-frontend/README.md) | Components, SSE contract, logical Turn retry |
| [TUI docs](re-coach-tui/README.md) | Terminal capabilities and module mapping to the backend |
| [AI Coach Skill](ai-coach-skill-repo/README.md) · [EN](ai-coach-skill-repo/README_EN.md) | Behaviour-layer design |
| [Contributing](CONTRIBUTING.md) | Workflow and code conventions |

---

## 🧪 Tests

```bash
# Backend: 18 test files, 217 passed
cd recoach-server && ./.venv/Scripts/python.exe -m pytest -q

# Frontend: 4 test files, 22 passed (Node built-in test runner)
cd recoach-frontend && npm test

# TUI: 6 test files, 92 passed (vitest)
cd re-coach-tui && npm test
```

> These are measured results (Python 3.11 / Node 24). The backend's 165 test functions expand to 217
> cases through `@pytest.mark.parametrize`, and the TUI's `it.each` does the same (64 → 92). CI currently
> runs only the backend and
> frontend jobs ([.github/workflows/test.yml](.github/workflows/test.yml)); the TUI suite is not wired into CI yet.

### Cross-file consistency audit

Run this after changing configuration keys or API fields — it catches problems no single file reveals
(a config key added but never documented in `.env.example`, a backend field missing from the frontend
types, leftover debug output, and so on):

```bash
python tools/consistency_audit.py
```

Exit code 0 means everything passed, so it is safe to wire into CI. The script locates the repository
itself and can be run from any directory; the TUI is checked both when it sits at the repository root
and when it lives in a sibling directory.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details

---

**⭐ If this helped you, please give it a star!**
