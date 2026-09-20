# 部署指南

[简体中文](deployment.md) | [English](deployment.en.md)

本文档介绍如何在不同环境中部署Re:Coach。

## 目录

- [本地开发部署](#本地开发部署)
- [Docker部署（推荐）](#docker部署推荐)
- [生产环境部署](#生产环境部署)
- [环境变量配置](#环境变量配置)
- [数据备份](#数据备份)
- [监控和日志](#监控和日志)
- [故障排除](#故障排除)
- [安全建议](#安全建议)
- [性能与容量](#性能与容量)

---

## 本地开发部署

### 后端

在后端项目根目录（能看到 `app/`、`requirements.txt` 和 `.env.example` 的目录）执行：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt

# 配置环境变量（可选）
Copy-Item .env.example .env
# 编辑 .env 文件配置API keys

.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

不使用 uv 时用标准库等价完成：`python -m venv .venv` → `.\\.venv\\Scripts\\Activate.ps1` → `python -m pip install -r requirements.txt`。macOS / Linux 用 `python3 -m venv .venv` + `source .venv/bin/activate`，启动命令为 `./.venv/bin/python -m uvicorn ...`。

> 这里装的是 `requirements.txt` 而不是 `requirements.lock.txt`：锁文件由 Linux 的 `pip freeze` 生成，不保留环境标记，其中的 `uvloop` 只支持 Linux/macOS，在 Windows 上会编译失败并中断整条安装。锁文件用于镜像构建。

### 前端

```bash
cd recoach-frontend

# 安装依赖
npm ci

# 运行开发服务器
npm run dev
```

访问 http://127.0.0.1:4173。端口由 `vite.config.ts` 固定（`strictPort: true`）并与后端 CORS 白名单一致，不会退到 5173；`/api` 由 Vite 代理到 `http://127.0.0.1:8000`。

### 终端版（可选）

`re-coach-tui/` 是独立的终端应用，不与 `recoach-server` 通信，配置同样读 `RECOACH_*` 环境变量：

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

---

## Docker部署（推荐）

### 快速启动

```bash
# 克隆项目
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# 一键启动
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
```

（旧版独立安装的 Docker Compose 用连字符形式 `docker-compose`，参数相同。）

### 配置LLM提供商

应用配置写入 `recoach-server/.env`，再让 backend 重新读取：

```bash
cp recoach-server/.env.example recoach-server/.env
# 编辑 recoach-server/.env，例如：
#   RECOACH_LLM_PROVIDER=deepseek
#   RECOACH_DEEPSEEK_API_KEY=你的密钥
docker compose up -d --force-recreate backend
```

Compose 通过 `env_file: ./recoach-server/.env` 把这份配置注入容器（文件缺失时不报错，走内置默认值）。

> **不要**把模型配置写进 `docker-compose.yml` 的 `environment`：那里的优先级高于 `env_file`，会静默覆盖用户自己的 `.env`，表现为"改了 `.env` 却不生效"。

### 自定义端口

后端**宿主**端口用环境变量改，容器内始终监听 8000：

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

前端入口端口写死在 `docker-compose.yml` 的 `127.0.0.1:4173:4173`。若要改动，除了改端口映射，还必须同步后端 `.env` 的 `RECOACH_CORS_ORIGINS`（换成新的来源），否则浏览器会因 CORS 失败读不到响应。不要改成 `0.0.0.0:4173`——原因见[访问控制（必读）](#访问控制必读)。

---

## 生产环境部署

> **先读这一节的前提**：当前项目的定位是**单用户本地自用**。内置 Web 客户端没有登录页，令牌模式对它没有意义（它不会发送令牌）。因此"生产环境部署"在本项目里指的是 **在可信网络内长期运行**，而不是面向互不信任的多用户公网服务。要对外提供 Web 界面，必须先在外层反向代理接入登录，详见[安全建议](#安全建议)。

### 使用Docker Compose（单机部署）

#### 1. 准备服务器

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Docker Compose v2 随 Docker 一起安装；若仍使用旧版独立二进制：

```bash
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

#### 2. 克隆项目

```bash
cd /opt
sudo git clone https://github.com/YitiAnz127/Re-Coach-Agent.git recoach
cd recoach
```

#### 3. 配置环境变量

**应用配置**（模型、密钥、限流、记忆开关）放 `recoach-server/.env`：

```bash
cp recoach-server/.env.example recoach-server/.env
# 编辑 recoach-server/.env
chmod 600 recoach-server/.env
```

**部署参数**（只给 compose 做变量替换）放仓库根 `.env`：

```bash
cat > .env << 'ENVEOF'
RECOACH_BACKEND_PORT=8000
RECOACH_API_TOKEN=
RECOACH_TRUSTED_HOSTS=172.28.0.0/24
ENVEOF
chmod 600 .env
```

两个文件的分工见[两个 .env 的分工（容易搞混）](#两个-env-的分工容易搞混)。

#### 4. 可选：调整 compose 的部署参数

`docker-compose.yml` 已经内置了可直接用于长期运行的配置（`restart: unless-stopped`、健康检查、内存/CPU 上限、`no-new-privileges`、数据卷挂载）。需要日志轮转时在 backend 服务下新增：

```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

#### 5. 启动服务

```bash
docker compose up -d
docker compose logs -f

# 检查健康状态
curl http://127.0.0.1:8000/health
```

#### 6. 配置Nginx反向代理（对外提供 Web 界面时）

容器默认只绑回环，对外开放前必须先有带登录的反向代理。前端容器内的 nginx 已经把 `/api` 反代到 `backend:8000`，并在转发时**剥掉客户端自带的 `x-user-id`**（阻断"自行声明身份冒充他人"）、保留 `Authorization` 供令牌模式使用，所以宿主机上只需要反代前端入口：

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/recoach
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端（/api 由前端容器内的 nginx 继续转发到 backend）
    location / {
        proxy_pass http://127.0.0.1:4173;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    # 健康检查
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }
}
```

如果确实要在宿主机 nginx 上再直接反代后端 `/api`，必须自己复刻身份头剥离与 SSE 关闭缓冲，否则会绕过前端的保护：

```nginx
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header x-user-id "";          # 必须：剥离客户端身份头
        proxy_set_header x-request-id $http_x_request_id;
        proxy_set_header Authorization $http_authorization;
        proxy_buffering off;                    # 必须：否则 SSE 事件会被攒起来一次性下发
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
```

```bash
# 启用配置
sudo ln -s /etc/nginx/sites-available/recoach /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 7. 配置SSL证书（推荐）

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com

# 自动续期
sudo crontab -e
# 添加: 0 3 * * * certbot renew --quiet
```

### 使用Systemd管理（备选方案）

如果不使用Docker，可以用systemd管理后端：

```bash
sudo nano /etc/systemd/system/recoach-backend.service
```

```ini
[Unit]
Description=Re:Coach Backend
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/recoach/recoach-server
Environment="PATH=/opt/recoach/recoach-server/.venv/bin"
# 对外部署必须同时设置 RECOACH_API_TOKEN，否则本服务完全没有访问控制。
# 注意 --host 0.0.0.0 会让后端直接监听所有网卡：若前面还有 nginx，
# 后端端口不应对外开放（用防火墙只放行前端端口），否则可绕过 nginx 的身份头剥离。
# 若确实需要直接访问后端，请设为 127.0.0.1（仅本机）并让 nginx 反代。
Environment="RECOACH_API_TOKEN=<在此填入你的令牌>"
ExecStart=/opt/recoach/recoach-server/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
# 启动服务
sudo systemctl enable recoach-backend
sudo systemctl start recoach-backend
sudo systemctl status recoach-backend
```

（这种方式下前端需要自己构建并托管：`npm ci && npm run build`，把 `dist/` 交给静态服务器，并自行配置 `/api` 反代与身份头剥离。前端的 `Dockerfile` + `nginx.conf` 就是一份可参照的实现。）

---

## 环境变量配置

### 完整配置参考

权威来源是 `recoach-server/.env.example`（每一项都有注释）与 `recoach-server/app/config.py` 的默认值； `tools/consistency_audit.py` 会检查两者是否同步。常用项：

```bash
# ========== 基础配置 ==========
RECOACH_DB_PATH=./recoach.db
RECOACH_DEV_USER=dev_user
RECOACH_CORS_ORIGINS=http://127.0.0.1:4173,http://localhost:4173

# ========== 访问控制 ==========
# 留空 = 开发模式：/api/v1 只接受本机回环客户端，其他来源 401
RECOACH_API_TOKEN=
RECOACH_ALLOW_LOCAL_WITHOUT_TOKEN=true
# 开发模式下额外信任的 IP / CIDR；Docker Compose 默认使用 172.28.0.0/24
RECOACH_TRUSTED_HOSTS=
# 是否暴露 /docs 与 /openapi.json；留空时令牌模式关闭、开发模式开启
# RECOACH_EXPOSE_DOCS=false

# ========== LLM配置 ==========
# 选择提供商: template | openai_compatible | deepseek | anthropic
RECOACH_LLM_PROVIDER=template

# DeepSeek配置
RECOACH_DEEPSEEK_API_KEY=
RECOACH_DEEPSEEK_BASE_URL=https://api.deepseek.com
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
RECOACH_DEEPSEEK_THINKING=enabled
# 代码默认 medium；.env.example 有意推荐 low（附实测延迟数据）
RECOACH_DEEPSEEK_REASONING_EFFORT=low

# Anthropic配置
RECOACH_ANTHROPIC_API_KEY=
RECOACH_ANTHROPIC_MODEL=claude-opus-5

# OpenAI兼容配置
RECOACH_LLM_BASE_URL=
RECOACH_LLM_API_KEY=
RECOACH_LLM_MODEL=

# LLM通用参数
RECOACH_LLM_MAX_TOKENS=10000
RECOACH_LLM_TIMEOUT=90
RECOACH_LLM_MAX_CONTINUATIONS=2
# 首字前 provider 失败时：false=模板兜底，true=直接返回 MODEL_UNAVAILABLE
RECOACH_LLM_FAIL_FAST=false

# ========== 资源限制 ==========
RECOACH_RATE_LIMIT_PER_MINUTE=30
RECOACH_MAX_CONCURRENT_TURNS=16
RECOACH_MAX_BODY_BYTES=65536

# ========== 记忆系统 ==========
RECOACH_MEMORY_ON=true
RECOACH_MEMORY_MAX_SELECTED=3
RECOACH_MEMORY_HARD_LIMIT=4
RECOACH_MEMORY_CAPSULE_TOKENS=280
RECOACH_TOOL_BUDGET=1
```

### 环境变量优先级

**应用侧**（`pydantic-settings`，即 `recoach-server/app/config.py`）：

1. 进程环境变量（最高）
2. `.env` 文件（注意 `RECOACH_DB_PATH` 这类相对路径以**启动进程时的当前目录**为基准）
3. `config.py` 中的默认值（最低）

**Compose 侧**（两者最终都会变成容器内的环境变量）：

1. `docker-compose.yml` 的 `environment`（最高，会覆盖下面这一项）
2. `env_file: ./recoach-server/.env`

所以：应用配置写进 `recoach-server/.env`；只有**容器部署特有**的值（DB 绝对路径、令牌、可信网段）才写进 compose 的 `environment`。

### 两个 .env 的分工（容易搞混）

仓库里有两个 `.env`，**职责不同，不要合并**（两个都已被 `.gitignore` 忽略）：

| 文件 | 谁读它 | 放什么 |
|---|---|---|
| `./.env`（仓库根） | `docker compose` 做变量替换 | 部署参数，如 `RECOACH_BACKEND_PORT` |
| `./recoach-server/.env` | 应用自身（也被 compose 的 `env_file` 注入容器） | 模型、密钥、限流、鉴权等应用配置 |

判断标准很简单：**"容器里跑的那个进程会读它吗？"** 会 → `recoach-server/.env`；只是给 compose 拼命令行用的 → 根目录 `.env`。

把应用配置写进根 `.env` 不会生效；把端口写成 `recoach-server/.env` 也不会被 compose 读到。

---

## 数据备份

数据库位置取决于启动方式：Docker 部署是宿主机 `./data/recoach.db`（挂载到容器 `/app/data/recoach.db`）；本地开发默认是后端目录下的 `./recoach.db`（由 `RECOACH_DB_PATH` 决定）。

### 数据库备份

```bash
# 手动备份
cp data/recoach.db data/recoach.db.backup-$(date +%Y%m%d-%H%M%S)

# 自动备份脚本
cat > /opt/recoach/backup.sh << 'BACKUPEOF'
#!/bin/bash
BACKUP_DIR="/opt/recoach/backups"
DB_FILE="/opt/recoach/data/recoach.db"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p $BACKUP_DIR
cp $DB_FILE $BACKUP_DIR/recoach.db.$TIMESTAMP

# 保留最近7天的备份
find $BACKUP_DIR -name "recoach.db.*" -mtime +7 -delete
BACKUPEOF

chmod +x /opt/recoach/backup.sh

# 添加到crontab（每天凌晨3点备份）
echo "0 3 * * * /opt/recoach/backup.sh" | sudo crontab -
```

SQLite 启用了 WAL 模式，因此**不要**在服务运行时只复制单一文件后直接回灌；要么先停掉 backend 再复制（下面的恢复流程就是这么做的），要么同时带上 `-wal` / `-shm`。

### 恢复数据

```bash
# 停止服务
docker compose down

# 恢复数据库
cp backups/recoach.db.20260904-030000 data/recoach.db

# 重启服务
docker compose up -d
```

---

## 监控和日志

### 查看日志

```bash
# Docker日志
docker compose logs -f backend
docker compose logs -f frontend

# 系统日志（systemd）
sudo journalctl -u recoach-backend -f
```

### 性能监控

使用Docker stats：

```bash
docker stats recoach-backend recoach-frontend
```

应用内部的运行指标（首字延迟、记忆检索耗时、上下文编译耗时等 p50/p95）通过 `GET /api/v1/metrics/summary` 获取，Web 界面的 Performance 视图会展示其中一部分。

### 健康检查

```bash
# 后端健康检查（始终免鉴权）
curl http://127.0.0.1:8000/health

# 自动监控脚本
cat > /opt/recoach/healthcheck.sh << 'HEALTHEOF'
#!/bin/bash
URL="http://127.0.0.1:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $URL)

if [ $RESPONSE -ne 200 ]; then
    echo "Health check failed: $RESPONSE"
    # 发送告警（可接入钉钉、邮件等）
    docker compose restart backend
fi
HEALTHEOF

chmod +x /opt/recoach/healthcheck.sh

# 每5分钟检查一次
echo "*/5 * * * * /opt/recoach/healthcheck.sh" | crontab -
```

---

## 故障排除

### 容器无法启动

```bash
# 查看详细错误
docker compose logs backend

# 检查配置
docker compose config

# 重新构建
docker compose build --no-cache
```

### 数据库锁定

```bash
# SQLite数据库锁定时
docker compose down
rm data/recoach.db-wal data/recoach.db-shm
docker compose up -d
```

### 内存不足

`docker-compose.yml` 已经给 backend 设了 `mem_limit: 1g`、前端 256m，需要调整时改这两行。注意 Compose 规范下容器用 `mem_limit`，`deploy.resources.limits` 只在 Swarm 模式生效：

```yaml
services:
  backend:
    mem_limit: 2g
```

### 后端端口 8000 起不来（Windows 常见）

Windows 会把一批端口段保留给 Hyper-V / WSL，落在这个范围内的端口**绑不上**，报错形如 `[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试`。8000 在很多机器上恰好在保留段里。

先确认：

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

如果 8000 在列表中，换一个宿主端口即可（容器内部仍监听 8000）：

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

或者用管理员权限永久释放该段（会重启 winnat，需谨慎）：

```powershell
net stop winnat
netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1
net start winnat
```

注意：这只影响宿主侧端口映射。前端入口 4173 通常不受影响，所以**即使不改，`http://127.0.0.1:4173` 依然可以用**——8000 只在需要直接访问后端 API（如 `/docs`）时才需要。

---

## 安全建议

### 访问控制（必读）

Re:Coach 的身份由 `x-user-id` 请求头表达。该头**只在调用方通过鉴权之后才可信**，鉴权由 `RECOACH_API_TOKEN` 承担：

| `RECOACH_API_TOKEN` | 行为 |
|---|---|
| 留空（默认） | **开发模式**：`/api/v1` 只接受本机回环客户端，其他来源一律 `401` |
| 已设置 | **令牌模式**：所有 `/api/v1` 请求必须带 `Authorization: Bearer <token>` |

生成令牌：

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

令牌模式面向直接调用 API 的可信客户端。当前浏览器界面没有登录页，也不会读取、保存或自动附加 `RECOACH_API_TOKEN`；因此只在后端设置令牌，会让浏览器请求全部返回 `401`。不要把令牌写入 `VITE_*` 或前端镜像，那会把共享密钥公开在浏览器产物中。

对外提供 Web 界面时，必须在外层反向代理接入登录，并由代理在服务端侧注入 Bearer 令牌与可信用户身份；同时保持后端端口不可从公网直接访问。没有这层身份代理时，只能维持下文的回环地址本地部署。

### 信任边界的真实含义

令牌是**单一共享密钥**，它解决的是"谁能访问这个实例"，**不解决"用户之间互相隔离"**：

- 持有令牌的调用方仍然可以自行设置 `x-user-id: <任意值>`，读写该身份下的数据。
- 因此当前模型适用于**单用户自用**或**全部使用者互相信任**的场景。
- 若要面向互不信任的多用户，需要在反向代理层接入真实登录（OIDC / 自建账号），把 `user_id` 从"请求头"改为"服务端根据会话推导"，并给每个用户独立凭证。改动点在 `recoach-server/app/auth.py` 与 `app/routes/sessions.py:current_user_id`。

`/docs` 与 `/openapi.json` 在令牌模式下默认关闭；需要时显式设 `RECOACH_EXPOSE_DOCS=true`。

### 本地自用（单用户）部署要点

本项目当前定位是**单用户本地自用**。这个定位下靠"只绑回环"就够了，不必配令牌：

```yaml
# docker-compose.yml
frontend:
  ports:
    - "127.0.0.1:4173:4173"   # 唯一入口，只绑回环
backend:
  ports:
    - "127.0.0.1:8000:8000"   # 不直接对外
  environment:
    - RECOACH_API_TOKEN=      # 留空 = 开发模式
    - RECOACH_TRUSTED_HOSTS=172.28.0.0/24
```

**`RECOACH_TRUSTED_HOSTS` 是本地自用的关键一项。** 前端 nginx 反代 `/api` 时，后端看到的来源是 nginx 容器的内网 IP（`172.28.0.x`），不属于回环；只认回环会让每个 API 调用都返回 `401`。所以 compose 固定了网段 `172.28.0.0/24` 并在后端显式声明它，两边必须一致。

> 这两条是**一对**：只绑回环 → 局域网进不来；声明可信网段 → 容器间反代能通。改任意一条都要同时看另一条。

不要只把入口改成 `0.0.0.0:4173` 并设置 `RECOACH_API_TOKEN`：内置 Web 客户端不会发送该令牌，结果只会是全部 API 请求 `401`。对外监听前应先配置带登录的外层反向代理，由它在服务端侧注入 Bearer 令牌；否则局域网内任何人都能访问完整应用。

### 限流与资源上限

- `RECOACH_RATE_LIMIT_PER_MINUTE`（默认 30）：每身份每分钟的计费型请求数上限。仅作用于会真实调用 LLM 的端点（创建 Turn 与创建 Fork）；完成态重放与 409 冲突不消耗配额。超限返回 `429`。
- `RECOACH_MAX_CONCURRENT_TURNS`（默认 16）：同时进行中的流式 Turn 上限。超限返回 `503 SERVICE_BUSY`。挡住"开大量 SSE 不读响应"的资源耗尽。
- `RECOACH_MAX_BODY_BYTES`（默认 65536）：请求体大小上限，超限返回 `413`。
- 以上计数都在进程内存中，**多副本部署时每个副本各算一份**。需要严格全局配额时应换成 Redis 等共享后端。

### 其他

1. **使用HTTPS** - 生产环境必须使用SSL证书
2. **保护API密钥** - 使用环境变量或密钥管理服务
3. **限制CORS** - 只允许可信域名；设成 `*` 时服务端会自动关闭 `allow_credentials`
4. **定期备份** - 设置自动备份任务
5. **更新依赖** - 后端镜像从 `requirements.lock.txt` 安装以保证可复现。**改依赖后必须重新生成锁文件**，且必须用与镜像相同版本的 Python 解析：

   ```bash
   cd recoach-server
   docker run --rm -v "$PWD:/w" -w /w python:3.10-slim \
     sh -c "pip install -q -r requirements.txt && pip freeze" > requirements.lock.txt
   ```

   > 曾经踩过的坑：该锁文件最初是在 Python 3.13 上生成的，其中的 `websockets==17.0.1` 要求 `Python>=3.11`，而镜像是 3.10，导致 `docker-compose build` 直接失败。锁文件只有在被真正安装时才暴露问题—— 之前它一直没被镜像使用，所以这个不兼容潜伏了很久。
6. **监控日志** - 设置日志告警
7. **防火墙** - 只开放必要的端口（80、443）。后端 8000 在 compose 中只绑定 `127.0.0.1`，不要改回 `0.0.0.0`——那会绕过 nginx 的身份头剥离

---

## 性能与容量

### 当前实现

- 存储是 SQLite（WAL + FTS5，FTS5 不可用时降级到 LIKE），单写入者、本地文件，适合单实例单用户场景；`GET /api/v1/metrics/summary` 提供 p50/p95 运行指标。
- 限流与并发闸门都在进程内存中，因此**不能靠多副本水平扩容获得全局配额**。

### 尚未实现（提升容量前需要先做的工作）

以下都是方向，不是现有功能：

- PostgreSQL 等外部数据库适配器：当前没有数据库后端抽象层，schema 与 SQL 直接写在 `app/db.py`。
- Redis 等共享限流 / 缓存后端：需要替换 `app/services/ratelimit.py` 与 `app/services/turn_gate.py` 的内存实现。
- 多副本部署下的会话粘性与 SSE 转发。

---

## 扩展阅读

- [快速开始](quickstart.md)
- [后端文档](../recoach-server/README.md) - 架构、API 与能力边界
- [后端 LLM 配置](../recoach-server/README_LLM_CONFIG.md)
- [前端文档](../recoach-frontend/README.md)
- [TUI 文档](../re-coach-tui/README.md)
- [API文档](http://127.0.0.1:8000/docs)
