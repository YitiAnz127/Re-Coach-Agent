# 快速开始指南

[简体中文](quickstart.md) | [English](quickstart.en.md)

本指南将帮助你在5分钟内运行Re:Coach项目。

## 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0（命令为 `docker compose`；旧版独立安装的 `docker-compose` 等价）
- (可选) Git

## 快速启动

### 1. 克隆项目（如果还没有）

```bash
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent
```

### 2. 一键启动

```bash
docker compose up -d
```

第一次运行会构建镜像，大约需要3-5分钟。

### 3. 验证运行

访问以下URL验证服务是否正常：

- **前端**: http://127.0.0.1:4173
- **后端API文档**: http://127.0.0.1:8000/docs
- **后端健康检查**: http://127.0.0.1:8000/health

两个端口都只绑定在回环地址（`127.0.0.1`）上，这是有意设计：内置 Web 客户端不持有访问令牌，所以默认不允许其他机器访问。需要对外开放时请先读 [部署指南的访问控制章节](deployment.md#访问控制必读)。

### 4. 开始使用

打开浏览器访问 http://127.0.0.1:4173，你应该能看到Re:Coach的界面。

默认配置使用**模板模式**（`RECOACH_LLM_PROVIDER=template`，无需API key），可以体验完整协议与记忆闭环，但回答只是教学结构骨架。

## 配置真实LLM

如果想使用真实的AI模型，需要配置API key。

应用配置只从 `recoach-server/.env` 读取。**不要**把模型配置写进 `docker-compose.yml` 的 `environment`——那里优先级高于 `env_file`，会静默覆盖你自己的 `.env`，造成"改了 `.env` 却不生效"。

```bash
# 1. 从模板创建配置（后端项目根目录）
cp recoach-server/.env.example recoach-server/.env

# 2. 编辑 recoach-server/.env，选择 provider 并填入密钥，例如 DeepSeek：
#    RECOACH_LLM_PROVIDER=deepseek
#    RECOACH_DEEPSEEK_API_KEY=你的密钥
#    RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash

# 3. 让 backend 重新读取配置
docker compose up -d --force-recreate backend
```

Compose 会把这份 `.env` 注入容器（`env_file: ./recoach-server/.env`，缺失时不报错并走内置默认值）。支持的 provider 与各字段含义见 [LLM 配置指南](../recoach-server/README_LLM_CONFIG.md)。

> 仓库根目录的 `.env` 是**另一个**文件，只被 `docker compose` 用于变量替换（例如 `RECOACH_BACKEND_PORT`），不会被注入容器。两个 `.env` 的分工见[部署指南](deployment.md#两个-env-的分工容易搞混)。

## 常用命令

### 查看日志

```bash
# 查看所有日志
docker compose logs -f

# 只查看后端日志
docker compose logs -f backend

# 只查看前端日志
docker compose logs -f frontend
```

### 停止服务

```bash
docker compose down
```

### 重启服务

```bash
docker compose restart
```

### 清理并重新构建

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

## 本地开发模式

如果你想进行开发而不是只运行项目：

### 后端开发

在后端项目根目录（能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）执行：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

macOS / Linux 把前两条换成 `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`，启动命令换成 `./.venv/bin/python -m uvicorn ...`。

> 这里装的是 `requirements.txt` 而不是 `requirements.lock.txt`：锁文件由 Linux 的 `pip freeze` 生成，不保留环境标记，其中的 `uvloop` 只支持 Linux/macOS，在 Windows 上会编译失败并中断整条安装。锁文件用于镜像构建。

### 前端开发

在前端项目根目录执行：

```bash
npm ci
npm run dev
```

开发服务器固定在 http://127.0.0.1:4173（`vite.config.ts` 里 `strictPort: true`，与后端 CORS 白名单一致；不会退到 5173）。`/api` 由 Vite 代理到 `http://127.0.0.1:8000`，未设置环境变量时前端默认连真实后端。

### 终端版（可选）

`re-coach-tui/` 是独立的终端应用，不需要启动后端：

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

## 故障排除

### 后端端口 8000 起不来（Windows 常见）

Windows 会把一批端口段保留给 Hyper-V / WSL，落在保留段内的端口绑不上，报错形如 `[WinError 10013]`。先确认：

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

如果 8000 在列表里，换一个**宿主**端口即可（容器内仍监听 8000）：

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

### 前端无法连接后端

前端默认经同源 `/api/v1` 访问。容器部署下由前端 nginx 反代到 `backend:8000`；本地开发下由 Vite 代理到 `http://127.0.0.1:8000`。若要指向别的地址，在前端 `.env.local` 设置 `VITE_API_BASE_URL`（仓库未提供 `.env.example`，请自行创建；改完必须重启开发服务器）。

### 数据库文件权限问题

确保 `./data` 目录存在且有写权限：

```bash
mkdir -p data
chmod 755 data
```

### 查看容器状态

```bash
docker compose ps
```

应该看到两个容器都是 `healthy` 状态。

### 页面显示"本地演示"

说明前端处于演示模式。检查前端项目下 `.env.local` 里是否写了 `VITE_DEMO_MODE=true`，删掉或改为 `false` 后重启 `npm run dev`。

## 下一步

- 查看 [API文档](http://127.0.0.1:8000/docs) 了解后端接口
- 阅读 [部署指南](deployment.md) 了解生产环境部署与访问控制
- 阅读 [后端文档](../recoach-server/README.md) 了解已实现与未实现边界

## 需要帮助？

- 提交 [GitHub Issue](https://github.com/YitiAnz127/Re-Coach-Agent/issues)
- 查看项目 [README](../README.md)
