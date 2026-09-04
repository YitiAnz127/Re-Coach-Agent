# Deployment Guide

[简体中文](deployment.md) | [English](deployment.en.md)

This document describes how to deploy Re:Coach in different environments.

## Table of Contents

- [Local Development Deployment](#local-development-deployment)
- [Docker Deployment (Recommended)](#docker-deployment-recommended)
- [Production Deployment](#production-deployment)
- [Environment Variables](#environment-variables)
- [Data Backup](#data-backup)

---

## Local Development Deployment

### Backend

```bash
cd recoach-server

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables (optional)
cp .env.example .env
# Edit the .env file to configure API keys

# Run the dev server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd recoach-frontend

# Install dependencies
npm install

# Run the dev server
npm run dev
```

Visit http://localhost:5173

---

## Docker Deployment (Recommended)

### Quick start

```bash
# Clone the repo
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# One-command start
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f
```

### Configure the LLM provider

Edit `docker-compose.yml`, uncomment and configure the relevant LLM:

```yaml
# DeepSeek
- RECOACH_LLM_PROVIDER=deepseek
- RECOACH_DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}

# Or Anthropic
- RECOACH_LLM_PROVIDER=anthropic
- RECOACH_ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

### Custom ports

Edit the port mapping in `docker-compose.yml`:

```yaml
services:
  backend:
    ports:
      - "8001:8000"  # host:container
  frontend:
    ports:
      - "3000:4173"
```

---

## Production Deployment

### Using Docker Compose (single machine)

#### 1. Prepare the server

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

#### 2. Clone the project

```bash
cd /opt
sudo git clone https://github.com/YitiAnz127/Re-Coach-Agent.git recoach
cd recoach
```

#### 3. Configure environment variables

```bash
# Create the .env file
cat > .env << 'ENVEOF'
# LLM configuration
DEEPSEEK_API_KEY=your-actual-api-key-here
ANTHROPIC_API_KEY=your-actual-api-key-here

# Other configuration
RECOACH_CORS_ORIGINS=https://your-domain.com
ENVEOF

# Set permissions
chmod 600 .env
```

#### 4. Edit docker-compose.yml

```yaml
services:
  backend:
    # Add a restart policy
    restart: always

    # Configure log rotation
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

    # Use environment variables
    environment:
      - RECOACH_LLM_PROVIDER=deepseek
      - RECOACH_DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
```

#### 5. Start the services

```bash
# Build and start
sudo docker-compose up -d

# View logs
sudo docker-compose logs -f

# Check health status
curl http://localhost:8000/health
```

#### 6. Configure Nginx reverse proxy

```bash
sudo apt install nginx -y

# Create the config file
sudo nano /etc/nginx/sites-available/recoach
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # Frontend
    location / {
        proxy_pass http://localhost:4173;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # Backend API
    location /api/ {
        proxy_pass http://localhost:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_cache_bypass $http_upgrade;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
    }

    # Health check
    location /health {
        proxy_pass http://localhost:8000/health;
        access_log off;
    }
}
```

```bash
# Enable the config
sudo ln -s /etc/nginx/sites-available/recoach /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 7. Configure SSL certificates (recommended)

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx -y

# Get a certificate
sudo certbot --nginx -d your-domain.com

# Auto-renew
sudo crontab -e
# Add: 0 3 * * * certbot renew --quiet
```

### Using Systemd (alternative)

If you don't use Docker, you can manage the services with systemd:

#### Backend service

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
# Start the service
sudo systemctl enable recoach-backend
sudo systemctl start recoach-backend
sudo systemctl status recoach-backend
```

---

## Environment Variables

### Full configuration reference

```bash
# ========== Basic configuration ==========
RECOACH_DB_PATH=./recoach.db
RECOACH_DEV_USER=dev_user
RECOACH_CORS_ORIGINS=http://localhost:4173,https://your-domain.com

# ========== LLM configuration ==========
# Provider: template | openai_compatible | deepseek | anthropic
RECOACH_LLM_PROVIDER=deepseek

# DeepSeek configuration
RECOACH_DEEPSEEK_API_KEY=sk-xxx
RECOACH_DEEPSEEK_BASE_URL=https://api.deepseek.com
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
RECOACH_DEEPSEEK_THINKING=enabled
RECOACH_DEEPSEEK_REASONING_EFFORT=medium

# Anthropic configuration
RECOACH_ANTHROPIC_API_KEY=sk-xxx
RECOACH_ANTHROPIC_MODEL=claude-opus-5

# OpenAI-compatible configuration
RECOACH_LLM_BASE_URL=https://api.openai.com/v1
RECOACH_LLM_API_KEY=sk-xxx
RECOACH_LLM_MODEL=gpt-4

# LLM general parameters
RECOACH_LLM_MAX_TOKENS=6000
RECOACH_LLM_TIMEOUT=60.0
RECOACH_LLM_MAX_CONTINUATIONS=2

# ========== Memory system ==========
RECOACH_MEMORY_ON=true
RECOACH_MEMORY_MAX_SELECTED=3
RECOACH_MEMORY_HARD_LIMIT=4
RECOACH_MEMORY_CAPSULE_TOKENS=280
RECOACH_TOOL_BUDGET=1
```

### Environment variable priority

1. System environment variables (highest)
2. `.env` file
3. `environment` in `docker-compose.yml`
4. Default values (lowest)

---

## Data Backup

### Database backup

```bash
# Manual backup
cp data/recoach.db data/recoach.db.backup-$(date +%Y%m%d-%H%M%S)

# Automatic backup script
cat > /opt/recoach/backup.sh << 'BACKUPEOF'
#!/bin/bash
BACKUP_DIR="/opt/recoach/backups"
DB_FILE="/opt/recoach/data/recoach.db"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p $BACKUP_DIR
cp $DB_FILE $BACKUP_DIR/recoach.db.$TIMESTAMP

# Keep backups from the last 7 days
find $BACKUP_DIR -name "recoach.db.*" -mtime +7 -delete
BACKUPEOF

chmod +x /opt/recoach/backup.sh

# Add to crontab (backup daily at 3 AM)
echo "0 3 * * * /opt/recoach/backup.sh" | sudo crontab -
```

### Restore data

```bash
# Stop the services
docker-compose down

# Restore the database
cp backups/recoach.db.20260904-030000 data/recoach.db

# Restart the services
docker-compose up -d
```

---

## Monitoring and Logs

### View logs

```bash
# Docker logs
docker-compose logs -f backend
docker-compose logs -f frontend

# System logs (systemd)
sudo journalctl -u recoach-backend -f
```

### Performance monitoring

Use Docker stats:

```bash
docker stats recoach-backend recoach-frontend
```

### Health check

```bash
# Backend health check
curl http://localhost:8000/health

# Automatic monitoring script
cat > /opt/recoach/healthcheck.sh << 'HEALTHEOF'
#!/bin/bash
URL="http://localhost:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $URL)

if [ $RESPONSE -ne 200 ]; then
    echo "Health check failed: $RESPONSE"
    # Send an alert (e.g. DingTalk, email)
    docker-compose restart backend
fi
HEALTHEOF

chmod +x /opt/recoach/healthcheck.sh

# Check every 5 minutes
echo "*/5 * * * * /opt/recoach/healthcheck.sh" | crontab -
```

---

## Troubleshooting

### Containers fail to start

```bash
# View detailed errors
docker-compose logs backend

# Check the config
docker-compose config

# Rebuild
docker-compose build --no-cache
```

### Database locked

```bash
# In case of SQLite lock
docker-compose down
rm data/recoach.db-wal data/recoach.db-shm
docker-compose up -d
```

### Out of memory

Edit `docker-compose.yml`:

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

## Security Recommendations

1. **Use HTTPS** - a production environment must use SSL certificates
2. **Protect API keys** - use environment variables or a secrets manager
3. **Restrict CORS** - only allow trusted domains
4. **Back up regularly** - set up automatic backup tasks
5. **Update dependencies** - update Docker images and dependencies regularly
6. **Monitor logs** - set up log alerts
7. **Firewall** - only expose the necessary ports (80, 443)

---

## Performance Optimization

### Database optimization

For production, PostgreSQL is recommended over SQLite:

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

### Add a Redis cache

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

## Further Reading

- [Quick Start](quickstart.md)
- [API Docs](http://localhost:8000/docs)
