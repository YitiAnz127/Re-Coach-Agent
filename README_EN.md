# 知返 Re: Coach

[简体中文](README.md) | [English](README_EN.md)

> A personalized knowledge-explanation and retrospection agent for machine learning & deep learning learning scenarios.

[![Tests](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml/badge.svg)](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Design version:** v1.0  
**Core principles:** First understand the question, then retrieve relevant memories; minimal calls, low latency, automatic memory, clear scope.

---

## 🚀 Quick Start

### Using Docker (recommended)

```bash
# Clone the repo
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# One-command start
docker-compose up -d

# Access the app
# Frontend: http://localhost:4173
# API docs: http://localhost:8000/docs
```

### Local development

```bash
# Backend
cd recoach-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd recoach-frontend
npm install
npm run dev
```

**Detailed docs**: see [docs/quickstart.md](docs/quickstart.md)

---

## 📊 Project Status

- ✅ Excellent code quality (92/92 tests passing)
- ✅ Docker containerization
- ✅ CI/CD automation
- ✅ Complete documentation
- 🟢 Runnable and deployable

---

## 💡 Overview

**知返 Re: Coach** combines AI Coach teaching strategies with a feedback-memory system: it remembers how you understand concepts, where you're currently stuck on a proposition, and what explanations work for you — then automatically applies your preferred methods in later questions.

### Core Features

- 🧠 **Smart clarification**: asks high-information questions when needed
- 📝 **Scoped memory**: five-level scopes prevent wrong generalization
- ⚡ **Streaming responses**: SSE real-time push, thinking process visible
- 🎯 **Deterministic compilation**: traceable memory application
- 🔄 **Automatic learning**: extracts stable preferences from feedback

---

## 🛠 Tech Stack

**Frontend**: React 19 + TypeScript + Vite + KaTeX  
**Backend**: FastAPI + Python 3.10 + SQLite  
**LLM**: DeepSeek / Anthropic Claude / OpenAI-compatible

---

## 📖 Documentation

- [Quick start](docs/quickstart.md)
- [Deployment guide](docs/deployment.md)
- [Frontend docs](recoach-frontend/README.md)
- [Backend docs](recoach-server/README.md)

---

## 🧪 Tests

```bash
# Backend tests (81)
cd recoach-server && pytest

# Frontend tests (11)
cd recoach-frontend && npm test
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details

---

**⭐ If this helped you, please give it a star!**
