# 部署指南

[简体中文](deployment.md) | [English](deployment.en.md)

本文档介绍如何在不同环境中部署Re:Coach。

## 目录

- [本地开发部署](#本地开发部署)
- [Docker部署（推荐）](#docker部署推荐)
- [生产环境部署](#生产环境部署)
- [环境变量配置](#环境变量配置)
- [数据备份](#数据备份)

---

## 本地开发部署

### 后端

```bash
cd recoach-server

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（可选）
cp .env.example .env
# 编辑 .env 文件配置API keys

# 运行开发服务器
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 前端

```bash
cd recoach-frontend

# 安装依赖
npm install

# 运行开发服务器
npm run dev
```

访问 http://localhost:4173

---

## Docker部署（推荐）

### 快速启动

```bash
# 克隆项目
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# 一键启动
docker-compose up -d

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f
```

### 配置LLM提供商

编辑 `docker-compose.yml`，取消注释并配置相应的LLM：

```yaml
# DeepSeek
- RECOACH_LLM_PROVIDER=deepseek
- RECOACH_DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}

# 或 Anthropic
- RECOACH_LLM_PROVIDER=anthropic
- RECOACH_ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

### 自定义端口

修改 `docker-compose.yml` 中的端口映射：

```yaml
services:
  backend:
    ports:
      - "8001:8000"  # 本地端口:容器端口
  frontend:
    ports:
      - "3000:4173"
```

---

## 生产环境部署

### 使用Docker Compose（单机部署）

#### 1. 准备服务器

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 安装Docker Compose
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

```bash
# 创建.env文件
cat > .env << 'ENVEOF'
# LLM配置
DEEPSEEK_API_KEY=your-actual-api-key-here
ANTHROPIC_API_KEY=your-actual-api-key-here

# 其他配置
RECOACH_CORS_ORIGINS=https://your-domain.com
ENVEOF

# 设置权限
chmod 600 .env
```

#### 4. 修改docker-compose.yml

```yaml
services:
  backend:
    # 添加重启策略
    restart: always
    
    # 配置日志轮转
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    
    # 使用环境变量
    environment:
      - RECOACH_LLM_PROVIDER=deepseek
      - RECOACH_DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
```

#### 5. 启动服务

```bash
# 构建并启动
sudo docker-compose up -d

# 查看日志
sudo docker-compose logs -f

# 检查健康状态
curl http://localhost:8000/health
```

#### 6. 配置Nginx反向代理

```bash
sudo apt install nginx -y

# 创建配置文件
sudo nano /etc/nginx/sites-available/recoach
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端
    location / {
        proxy_pass http://localhost:4173;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # 后端API
    location /api/ {
        proxy_pass http://localhost:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_cache_bypass $http_upgrade;
        
        # SSE支持
        proxy_buffering off;
        proxy_cache off;
    }

    # 健康检查
    location /health {
        proxy_pass http://localhost:8000/health;
        access_log off;
    }
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
# 安装certbot
sudo apt install certbot python3-certbot-nginx -y

# 获取证书
sudo certbot --nginx -d your-domain.com

# 自动续期
sudo crontab -e
# 添加: 0 3 * * * certbot renew --quiet
```

### 使用Systemd管理（备选方案）

如果不使用Docker，可以用systemd管理服务：

#### 后端服务

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
Environment="PATH=/opt/recoach/venv/bin"
# 对外部署必须同时设置 RECOACH_API_TOKEN，否则本服务完全没有访问控制。
# 注意 --host 0.0.0.0 会让后端直接监听所有网卡：若前面还有 nginx，
# 后端端口不应对外开放（用防火墙只放行前端端口），否则可绕过 nginx 的身份头剥离。
# 若确实需要直接访问后端，请设为 127.0.0.1（仅本机）并让 nginx 反代。
Environment="RECOACH_API_TOKEN=<在此填入你的令牌>"
ExecStart=/opt/recoach/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
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

---

## 环境变量配置

### 完整配置参考

```bash
# ========== 基础配置 ==========
RECOACH_DB_PATH=./recoach.db
RECOACH_DEV_USER=dev_user
RECOACH_CORS_ORIGINS=http://localhost:4173,https://your-domain.com

# ========== LLM配置 ==========
# 选择提供商: template | openai_compatible | deepseek | anthropic
RECOACH_LLM_PROVIDER=deepseek

# DeepSeek配置
RECOACH_DEEPSEEK_API_KEY=sk-xxx
RECOACH_DEEPSEEK_BASE_URL=https://api.deepseek.com
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
RECOACH_DEEPSEEK_THINKING=enabled
RECOACH_DEEPSEEK_REASONING_EFFORT=medium

# Anthropic配置
RECOACH_ANTHROPIC_API_KEY=sk-ant-xxx
RECOACH_ANTHROPIC_MODEL=claude-opus-5

# OpenAI兼容配置
RECOACH_LLM_BASE_URL=https://api.openai.com/v1
RECOACH_LLM_API_KEY=sk-xxx
RECOACH_LLM_MODEL=gpt-4

# LLM通用参数
RECOACH_LLM_MAX_TOKENS=6000
RECOACH_LLM_TIMEOUT=60.0
RECOACH_LLM_MAX_CONTINUATIONS=2

# ========== 记忆系统 ==========
RECOACH_MEMORY_ON=true
RECOACH_MEMORY_MAX_SELECTED=3
RECOACH_MEMORY_HARD_LIMIT=4
RECOACH_MEMORY_CAPSULE_TOKENS=280
RECOACH_TOOL_BUDGET=1
```

### 环境变量优先级

1. 系统环境变量（最高）
2. `.env` 文件
3. `docker-compose.yml` 中的environment
4. 默认值（最低）

---

## 数据备份

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

### 恢复数据

```bash
# 停止服务
docker-compose down

# 恢复数据库
cp backups/recoach.db.20260904-030000 data/recoach.db

# 重启服务
docker-compose up -d
```

---

## 监控和日志

### 查看日志

```bash
# Docker日志
docker-compose logs -f backend
docker-compose logs -f frontend

# 系统日志（systemd）
sudo journalctl -u recoach-backend -f
```

### 性能监控

使用Docker stats：

```bash
docker stats recoach-backend recoach-frontend
```

### 健康检查

```bash
# 后端健康检查
curl http://localhost:8000/health

# 自动监控脚本
cat > /opt/recoach/healthcheck.sh << 'HEALTHEOF'
#!/bin/bash
URL="http://localhost:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $URL)

if [ $RESPONSE -ne 200 ]; then
    echo "Health check failed: $RESPONSE"
    # 发送告警（可接入钉钉、邮件等）
    docker-compose restart backend
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
docker-compose logs backend

# 检查配置
docker-compose config

# 重新构建
docker-compose build --no-cache
```

### 数据库锁定

```bash
# SQLite数据库锁定时
docker-compose down
rm data/recoach.db-wal data/recoach.db-shm
docker-compose up -d
```

### 内存不足

修改 `docker-compose.yml`：

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 1G
        reservations:
          memory: 512M
```

---

## 安全建议

### 两个 .env 的分工（容易搞混）

仓库里有两个 `.env`，**职责不同，不要合并**（两个都已被 `.gitignore` 忽略）：

| 文件 | 谁读它 | 放什么 |
|---|---|---|
| `./.env`（仓库根） | `docker compose` 做变量替换 | 部署参数，如 `RECOACH_BACKEND_PORT` |
| `./recoach-server/.env` | 应用自身（也被 compose 的 `env_file` 注入容器） | 模型、密钥、限流、鉴权等应用配置 |

判断标准很简单：**"容器里跑的那个进程会读它吗？"**
会 → `recoach-server/.env`；只是给 compose 拼命令行用的 → 根目录 `.env`。

把应用配置写进根 `.env` 不会生效；把端口写成 `recoach-server/.env` 也不会被 compose 读到。

### 访问控制（必读）

Re:Coach 的身份由 `x-user-id` 请求头表达。该头**只在调用方通过鉴权之后才可信**，
鉴权由 `RECOACH_API_TOKEN` 承担：

| `RECOACH_API_TOKEN` | 行为 |
|---|---|
| 留空（默认） | **开发模式**：`/api/v1` 只接受本机回环客户端，其他来源一律 `401` |
| 已设置 | **令牌模式**：所有 `/api/v1` 请求必须带 `Authorization: Bearer <token>` |

生成令牌：

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

令牌模式面向直接调用 API 的可信客户端。当前浏览器界面没有登录页，也不会读取、
保存或自动附加 `RECOACH_API_TOKEN`；因此只在后端设置令牌，会让浏览器请求全部返回
`401`。不要把令牌写入 `VITE_*` 或前端镜像，那会把共享密钥公开在浏览器产物中。

对外提供 Web 界面时，必须在外层反向代理接入登录，并由代理在服务端侧注入 Bearer
令牌与可信用户身份；同时保持后端端口不可从公网直接访问。没有这层身份代理时，
只能维持下文的回环地址本地部署。

### 信任边界的真实含义

令牌是**单一共享密钥**，它解决的是"谁能访问这个实例"，**不解决"用户之间互相隔离"**：

- 持有令牌的调用方仍然可以自行设置 `x-user-id: <任意值>`，读写该身份下的数据。
- 因此当前模型适用于**单用户自用**或**全部使用者互相信任**的场景。
- 若要面向互不信任的多用户，需要在反向代理层接入真实登录（OIDC / 自建账号），
  把 `user_id` 从"请求头"改为"服务端根据会话推导"，并给每个用户独立凭证。
  改动点在 `recoach-server/app/auth.py` 与 `app/routes/sessions.py:current_user_id`。

`/docs` 与 `/openapi.json` 在令牌模式下默认关闭；需要时显式设
`RECOACH_EXPOSE_DOCS=true`。

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

**`RECOACH_TRUSTED_HOSTS` 是本地自用的关键一项。** 前端 nginx 反代 `/api` 时，
后端看到的来源是 nginx 容器的内网 IP（`172.28.0.x`），不属于回环；
只认回环会让每个 API 调用都返回 `401`。所以 compose 固定了网段
`172.28.0.0/24` 并在后端显式声明它，两边必须一致。

> 这两条是**一对**：只绑回环 → 局域网进不来；声明可信网段 → 容器间反代能通。
> 改任意一条都要同时看另一条。

不要只把入口改成 `0.0.0.0:4173` 并设置 `RECOACH_API_TOKEN`：内置 Web 客户端不会发送
该令牌，结果只会是全部 API 请求 `401`。对外监听前应先配置带登录的外层反向代理，
由它在服务端侧注入 Bearer 令牌；否则局域网内任何人都能访问完整应用。

#### 后端端口 8000 起不来（Windows 常见）

Windows 会把一批端口段保留给 Hyper-V / WSL，落在这个范围内的端口**绑不上**，
报错形如 `[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试`。
8000 在很多机器上恰好在保留段里（例如 7998–8097）。

先确认：

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

如果 8000 在列表中，换一个宿主端口即可（容器内部仍监听 8000）：

```bash
RECOACH_BACKEND_PORT=8100 docker-compose up -d
```

或者用管理员权限永久释放该段（会重启 winnat，需谨慎）：

```powershell
net stop winnat
netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1
net start winnat
```

注意：这只影响宿主侧端口映射。前端入口 4173 通常不受影响，
所以**即使不改，`http://localhost:4173` 依然可以用**——8000 只在需要直接访问
后端 API（如 `/docs`）时才需要。

### 限流与资源上限

- `RECOACH_RATE_LIMIT_PER_MINUTE`（默认 30）：每身份每分钟的计费型请求数上限。
  仅作用于会真实调用 LLM 的端点；完成态重放与 409 冲突不消耗配额。
  超限返回 `429`。
- `RECOACH_MAX_CONCURRENT_TURNS`（默认 16）：同时进行中的流式 Turn 上限。
  超限返回 `503 SERVICE_BUSY`。挡住"开大量 SSE 不读响应"的资源耗尽。
- `RECOACH_MAX_BODY_BYTES`（默认 65536）：请求体大小上限，超限返回 `413`。
- 以上计数都在进程内存中，**多副本部署时每个副本各算一份**。需要严格全局配额
  时应换成 Redis 等共享后端。

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

   > 曾经踩过的坑：该锁文件最初是在 Python 3.13 上生成的，其中的
   > `websockets==17.0.1` 要求 `Python>=3.11`，而镜像是 3.10，
   > 导致 `docker-compose build` 直接失败。锁文件只有在被真正安装时才暴露问题——
   > 之前它一直没被镜像使用，所以这个不兼容潜伏了很久。
6. **监控日志** - 设置日志告警
7. **防火墙** - 只开放必要的端口（80、443）。后端 8000 在 compose 中只绑定
   `127.0.0.1`，不要改回 `0.0.0.0`——那会绕过 nginx 的身份头剥离

---

## 性能优化

### 数据库优化

生产环境建议使用PostgreSQL替代SQLite：

```yaml
services:
  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=recoach
      - POSTGRES_USER=recoach
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

### 添加Redis缓存

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
```

---

## 扩展阅读

- [快速开始](quickstart.md)
- [API文档](http://localhost:8000/docs)
