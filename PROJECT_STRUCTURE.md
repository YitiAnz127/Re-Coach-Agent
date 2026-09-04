# Re:Coach 项目结构说明

本文档说明项目的目录结构和文件组织。

## 根目录

```
Re_Coach_GitHub/
├── README.md                    # 项目主文档
├── LICENSE                      # MIT许可证
├── CONTRIBUTING.md              # 贡献指南
├── PROJECT_STRUCTURE.md         # 本文档
├── docker-compose.yml           # Docker编排配置
├── .gitignore                   # Git忽略规则
│
├── .github/                     # GitHub配置
│   └── workflows/
│       └── test.yml            # CI/CD自动化测试
│
├── docs/                        # 文档目录
│   ├── quickstart.md           # 快速开始指南
│   └── deployment.md           # 部署指南
│
├── recoach-frontend/            # 前端项目
│   ├── Dockerfile              # 前端Docker镜像
│   ├── package.json            # 依赖配置
│   ├── vite.config.ts          # Vite配置
│   ├── tsconfig.json           # TypeScript配置
│   ├── index.html              # 入口HTML
│   ├── src/                    # 源代码
│   │   ├── App.tsx            # 主应用组件
│   │   ├── components/        # React组件
│   │   ├── services/          # API服务
│   │   └── types.ts           # 类型定义
│   └── tests/                  # 测试文件
│
└── recoach-server/              # 后端项目
    ├── Dockerfile              # 后端Docker镜像
    ├── requirements.txt        # Python依赖
    ├── .env.example           # 环境变量示例
    ├── pytest.ini             # 测试配置
    ├── app/                    # 应用代码
    │   ├── main.py            # FastAPI入口
    │   ├── config.py          # 配置管理
    │   ├── db.py              # 数据库
    │   ├── routes/            # API路由
    │   └── services/          # 业务逻辑
    └── tests/                  # 测试文件
```

## 运行时目录

运行时会生成以下目录（已在.gitignore中）：

```
data/                            # 数据存储
└── recoach.db                  # SQLite数据库

recoach-frontend/node_modules/   # 前端依赖
recoach-server/venv/             # Python虚拟环境
```

## 关键文件说明

### 根目录

- **README.md**: 项目介绍、快速开始、核心特性
- **docker-compose.yml**: 一键启动配置，包含前后端服务
- **LICENSE**: MIT开源许可证
- **CONTRIBUTING.md**: 贡献指南和开发规范

### 前端 (recoach-frontend/)

- **src/App.tsx**: 主应用组件，管理会话和消息
- **src/components/**: UI组件
  - Conversation.tsx: 对话展示
  - Composer.tsx: 消息输入
  - SideRail.tsx: 侧边栏（记忆、性能）
- **src/services/**: API通信
  - agent-client.ts: 后端API客户端
  - sse-protocol.ts: SSE协议处理
- **tests/**: 单元测试

### 后端 (recoach-server/)

- **app/main.py**: FastAPI应用入口、中间件、异常处理
- **app/config.py**: 环境变量配置管理
- **app/db.py**: SQLite数据库schema和操作
- **app/routes/**: API端点
  - sessions.py: 会话管理
  - turns.py: Turn流式处理
  - memories.py: 记忆CRUD
  - meta.py: 服务元数据
- **app/services/**: 业务逻辑
  - coach.py: LLM调用
  - gate.py: 澄清判断
  - memory.py: 记忆检索和写入
  - compiler.py: 上下文编译
- **tests/**: 完整测试套件（81个测试）

## 文档

- **docs/quickstart.md**: 5分钟快速上手
- **docs/deployment.md**: 详细部署指南
- **recoach-frontend/README.md**: 前端架构和开发
- **recoach-server/README.md**: 后端设计和API
- **recoach-server/README_LLM_CONFIG.md**: LLM配置指南

## Docker

- **Dockerfile** (前后端各一个): 构建生产镜像
- **docker-compose.yml**: 编排配置，包含：
  - backend服务 (端口8000)
  - frontend服务 (端口4173)
  - 数据卷挂载
  - 健康检查

## CI/CD

- **.github/workflows/test.yml**: GitHub Actions配置
  - 后端测试 (pytest)
  - 前端测试 (npm test)
  - TypeScript类型检查
  - Docker镜像构建

## 开始使用

1. **快速开始**: `docker-compose up -d`
2. **本地开发**: 参考 `docs/quickstart.md`
3. **生产部署**: 参考 `docs/deployment.md`

## 更多信息

- 主README: 项目概述和快速开始
- 前端README: 前端技术细节
- 后端README: 后端架构设计
