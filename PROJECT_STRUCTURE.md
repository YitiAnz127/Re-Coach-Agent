# Re:Coach 项目结构说明

本文档说明项目的目录结构和文件组织。所有内容与仓库当前状态一致。

## 根目录

```
Re-Coach-Agent/
├── README.md                    # 项目主文档（中文）
├── README_EN.md                 # 项目主文档（英文）
├── PROJECT_STRUCTURE.md         # 本文档
├── CONTRIBUTING.md              # 贡献指南
├── LICENSE                      # MIT 许可证
├── docker-compose.yml           # Docker 编排（前后端，均只绑回环）
├── .gitignore                   # Git 忽略规则
│
├── .github/
│   └── workflows/
│       └── test.yml            # CI：后端测试+一致性审计 / 前端类型检查+测试 / TUI 类型检查+测试 / 镜像构建
│
├── docs/                        # 指南与分析文档
│   ├── quickstart.md           # 快速开始（中文）
│   ├── quickstart.en.md        # 快速开始（英文）
│   ├── deployment.md           # 部署指南（中文）
│   ├── deployment.en.md        # 部署指南（英文）
│   ├── expert-recoach-integration.md   # ExPerT 对照分析与升级方向
│   └── teaching-start-evaluation.md    # 教学起点成对评估的数据格式与汇总
│
├── tools/
│   ├── consistency_audit.py     # 跨文件一致性审计（配置项/错误码/类型/代码卫生）
│   ├── evaluate_teaching_start.py      # 教学起点成对评估汇总（离线，不调用模型）
│   └── test_evaluate_teaching_start.py # 上面脚本的单元测试
│
├── recoach-server/              # 后端（FastAPI + SQLite）
├── recoach-frontend/            # Web 前端（React + Vite）
├── re-coach-tui/                # 终端 TUI（独立应用，不依赖后端）
└── ai-coach-skill-repo/         # AI Coach Skill（行为层，无代码耦合）
```

## 运行时目录

运行时会生成以下目录（均已在 `.gitignore` 中）：

```
data/                            # Docker 部署的数据库挂载点
└── recoach.db                  # SQLite 数据库（WAL 模式）

recoach-server/recoach.db        # 本地开发默认路径（RECOACH_DB_PATH）
recoach-frontend/node_modules/   # 前端依赖
recoach-server/.venv/            # 后端虚拟环境
re-coach-tui/node_modules/       # TUI 依赖
re-coach-tui/dist/               # TUI 构建产物
~/.recoach/store.json            # TUI 数据（可用 RECOACH_DATA_DIR 覆盖）
```

## 关键文件说明

### 根目录

- **README.md / README_EN.md**: 项目介绍、快速开始、仓库组成、文档索引
- **docker-compose.yml**: 一键启动配置。只放容器部署特有的值（DB 绝对路径、令牌、可信网段），应用配置由 `env_file` 从 `recoach-server/.env` 注入
- **tools/consistency_audit.py**: 检查 `.env.example` 与 `config.py` 字段/默认值是否同步、后端 Metrics 字段是否同步到前端与 TUI 类型、错误码是否被前端硬编码、TUI 与后端的常量/默认值是否漂移、是否残留调试输出
- **tools/evaluate_teaching_start.py**: 汇总人工标注的成对评估数据（起点准确率、起点过高率、迁移题正确率、满意度差值、首字延迟中位数），只读不调用模型

### 后端 (recoach-server/)

```
recoach-server/
├── Dockerfile                  # python:3.10-slim，非 root 运行，从 requirements.lock.txt 安装
├── .dockerignore
├── requirements.txt            # 直接依赖
├── requirements.lock.txt       # 锁定快照（python:3.10-slim 的 pip freeze 生成，仅用于镜像）
├── pytest.ini
├── .env.example                # 全部 RECOACH_* 配置项与默认值
├── README.md                   # 后端设计与 API
├── README_LLM_CONFIG.md        # LLM 供应商接入指南
├── app/
│   ├── main.py                 # FastAPI 入口、中间件链、统一错误信封、health
│   ├── config.py               # 集中式配置（RECOACH_* 环境变量）
│   ├── db.py                   # SQLite schema、migration、FTS5 可用性探测
│   ├── auth.py                 # 访问控制中间件（令牌模式 / 本机回环开发模式）
│   ├── errors.py               # 错误码表与用户可见消息（含 provider 失败分类）
│   ├── schemas.py              # Pydantic 契约
│   ├── sse.py                  # SSE frame 编码
│   ├── ids.py / tokens.py
│   ├── routes/                 # sessions / turns / forks / memories / meta / metrics
│   └── services/               # 业务逻辑（见下）
└── tests/                      # 24 个测试文件
```

业务逻辑（`app/services/`）：

| 文件 | 职责 |
|---|---|
| `orchestrator.py` | 单 Turn 编排与 SSE 终止语义 |
| `gate.py` | Clarification Gate / ResolvedTask |
| `teaching.py` | 逐问教学起点推断、概念反馈读取与写入 |
| `instances.py` | 进程实例标识（启动恢复的归属判定） |
| `memory.py` | 作用域检索、写入、归档、遗忘、反馈分类 |
| `compiler.py` | 确定性 Context Compiler、系统提示词、不可信内容定界 |
| `coach.py` | 模板 / OpenAI 兼容 / DeepSeek / Anthropic 流式 Coach |
| `brief.py` | Session、Session Brief、消息、Concept State |
| `events.py` | 事件白名单与幂等写入（指标的唯一事实来源） |
| `selection.py` | 记忆选择结果与 presentation 的加载 |
| `metrics.py` | 运行指标 p50/p95 汇总 |
| `ratelimit.py` / `turn_gate.py` | 每身份限流 / 并发 Turn 闸门 |
| `turns.py` | 逻辑 Turn 存储、claim、历史恢复、启动时恢复遗留 streaming Turn |

### 前端 (recoach-frontend/)

```
recoach-frontend/
├── Dockerfile                  # node:22 构建 → nginx 提供静态产物与 /api 反代
├── nginx.conf                  # 安全响应头、CSP、SSE 关闭缓冲、剥离 x-user-id
├── index.html
├── vite.config.ts              # 固定端口 4173（strictPort），/api 代理到 127.0.0.1:8000
├── tsconfig*.json
├── public/recoach-mark.svg
├── scripts/e2e-smoke.mjs       # 前后端 HTTP 级联调
├── src/
│   ├── App.tsx                 # Session、逻辑 Turn、流消费、重试、历史恢复
│   ├── types.ts                # 与服务端 presentation / metrics 对齐的类型
│   ├── styles.css
│   ├── components/             # Conversation / Composer / SideRail /
│   │                           # FairForkComparison / FormulaText / ModelBadge
│   ├── services/               # agent-client / sse-protocol / turn-retry /
│   │                           # service-meta / fallback-notice
│   └── data/demo.ts            # 脱机演示数据（真实模式不调用）
└── tests/                      # 4 个测试文件
```

### TUI (re-coach-tui/)

```
re-coach-tui/
├── package.json                # bin: recoach；engines: node >= 22.19
├── tsconfig.json / tsconfig.build.json
├── vitest.config.ts
├── src/
│   ├── cli.ts                  # 入口：配置校验、数据目录、会话恢复
│   ├── index.ts                # 对外导出
│   ├── config.ts               # RECOACH_* 环境变量（与后端对齐）
│   ├── store.ts                # JSON 文件持久化（替代 SQLite）
│   ├── orchestrator.ts         # Turn 编排流水线
│   ├── agent.ts / ids.ts / tokens.ts / types.ts / text.ts
│   ├── core/                   # gate / teaching / memory / compiler / coach / brief / events / selection
│   └── ui/                     # app / theme / sanitize（pi-tui 界面）
└── tests/                      # 11 个测试文件
```

### AI Coach Skill (ai-coach-skill-repo/)

```
ai-coach-skill-repo/
├── README.md                   # 设计说明（中文）
├── README_EN.md                # 设计说明（英文）
├── LICENSE
└── skills/
    ├── Hermes/SKILL.md
    ├── Codex/SKILL.md
    └── Claude Desktop/SKILL.md
```

三个 `SKILL.md` 是**同一份内容的三个副本**（适配各平台的 Skill / Instructions 机制），不包含可执行代码，也不被上面三个应用引用。

## 文档

- **docs/quickstart.md**: 5 分钟快速上手（Docker 与本地开发）
- **docs/deployment.md**: 部署指南（访问控制、限流、备份、故障排查）
- **docs/expert-recoach-integration.md**: ExPerT 论文对照、相似点与 A/B/D 升级方向
- **docs/teaching-start-evaluation.md**: 教学起点适配的成对评估数据格式与离线汇总
- **recoach-frontend/README.md**: 前端能力、SSE 契约、已实现与未实现边界
- **recoach-server/README.md**: 后端架构、API 表、能力诚实性
- **recoach-server/README_LLM_CONFIG.md**: LLM 供应商配置与验证
- **re-coach-tui/README.md**: TUI 用法与后端模块对应关系
- **ai-coach-skill-repo/README.md**: AI Coach 的设计理念与安装方式

## Docker

- **Dockerfile**（前后端各一个）: 构建生产镜像
- **docker-compose.yml**: 编排配置，包含：
  - `backend` 服务（容器内 8000，宿主侧由 `RECOACH_BACKEND_PORT` 决定，只绑 `127.0.0.1`）
  - `frontend` 服务（`127.0.0.1:4173`，唯一对外入口，同源反代 `/api`）
  - `./data` 数据卷挂载、健康检查、内存/CPU 上限、`no-new-privileges`
  - 固定网段 `172.28.0.0/24`，与 `RECOACH_TRUSTED_HOSTS` 默认值必须一致

## CI/CD

- **.github/workflows/test.yml**: GitHub Actions 配置
  - `backend`: Python 3.10 上安装依赖 → `pytest -q` → `python tools/consistency_audit.py`
  - `frontend`: Node 22 上 `npm ci` → `tsc --noEmit` → `npm test`
  - `tui`: Node 22 上 `npm ci` → `npm run typecheck` → `npm run build` → `npm test`
  - `docker-build`: 前后端镜像构建（只验证构建，不启动）

## 开始使用

1. **快速开始**: `docker compose up -d`
2. **本地开发**: 参考 `docs/quickstart.md`
3. **生产部署**: 参考 `docs/deployment.md`

## 更多信息

- 主 README: 项目概述、仓库组成与文档索引
- 前端 README: 前端技术细节与未实现边界
- 后端 README: 后端架构设计与 API
