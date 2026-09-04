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

访问 http://localhost:5173

---

## Docker部署（推荐）

### 快速启动

```bash
# 克隆项目
git clone https://github.com/YitiAnz127/Re_Coach.git
cd Re_Coach

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
sudo git clone https://github.com/YitiAnz127/Re_Coach.git recoach
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

1. **使用HTTPS** - 生产环境必须使用SSL证书
2. **保护API密钥** - 使用环境变量或密钥管理服务
3. **限制CORS** - 只允许可信域名
4. **定期备份** - 设置自动备份任务
5. **更新依赖** - 定期更新Docker镜像和依赖包
6. **监控日志** - 设置日志告警
7. **防火墙** - 只开放必要的端口（80、443）

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
