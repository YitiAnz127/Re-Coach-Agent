# 知返 Re: Coach

[简体中文](README.md) | [English](README_EN.md)

> 面向机器学习与深度学习学习场景的个性化知识讲解与人机复盘 Agent

[![Tests](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml/badge.svg)](https://github.com/YitiAnz127/Re-Coach-Agent/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**设计版本：** v1.0  
**核心原则：** 先把问题弄清楚，再检索相关记忆；少调用、低延迟、自动记忆、作用域明确。

---

## 🚀 快速开始

### 使用Docker（推荐）

```bash
# 克隆项目
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# 一键启动
docker-compose up -d

# 访问应用
# 前端: http://localhost:4173
# API文档: http://localhost:8000/docs
```

### 本地开发

```bash
# 后端
cd recoach-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# 前端
cd recoach-frontend
npm install
npm run dev
```

**详细文档**: 查看 [docs/quickstart.md](docs/quickstart.md)

---

## 📊 项目状态

- ✅ 代码质量优秀（92/92 tests passing）
- ✅ Docker容器化
- ✅ CI/CD自动化
- ✅ 完整文档
- 🟢 可运行，可部署

---

## 💡 项目概述

**知返 Re: Coach** 将 AI Coach 的教学策略与反馈记忆系统结合，记住用户如何理解知识、当前卡在哪个命题、怎样讲更有效，并在后续问题中自动应用用户偏好的方法。

### 核心特性

- 🧠 **智能澄清**: 必要时提出高信息量问题
- 📝 **作用域记忆**: 五级作用域防止错误泛化
- ⚡ **流式响应**: SSE实时推送，思考过程可见
- 🎯 **确定性编译**: 可追溯的记忆应用
- 🔄 **自动学习**: 从反馈中提取稳定偏好

---

## 🛠 技术栈

**前端**: React 19 + TypeScript + Vite + KaTeX  
**后端**: FastAPI + Python 3.10 + SQLite  
**LLM**: DeepSeek / Anthropic Claude / OpenAI兼容

---

## 📖 文档

- [快速开始](docs/quickstart.md)
- [部署指南](docs/deployment.md)
- [前端文档](recoach-frontend/README.md)
- [后端文档](recoach-server/README.md)

---

## 🧪 测试

```bash
# 后端测试
cd recoach-server && pytest

# 前端测试
cd recoach-frontend && npm test
```

### 跨文件一致性审计

改完配置项或接口字段后跑一遍，能发现单看某个文件看不出来的问题
（配置项加了却忘了写进 `.env.example`、后端加了字段却忘了同步前端类型、
代码里残留调试输出等）：

```bash
python tools/consistency_audit.py
```

退出码 0 表示全部通过，可直接接入 CI。脚本会自行定位仓库位置，
在任意目录下运行都可以；同级目录若存在 TUI 仓库，会一并检查它的问题。

---

## 📄 License

MIT License - 查看 [LICENSE](LICENSE) 了解详情

---

**⭐ 如果有帮助，请给个star！**
