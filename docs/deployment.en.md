# Deployment Guide

[简体中文](deployment.md) | [English](deployment.en.md)

This document describes how to deploy Re:Coach in different environments.

## Table of Contents

- [Local Development Deployment](#local-development-deployment)
- [Docker Deployment (Recommended)](#docker-deployment-recommended)
- [Production Deployment](#production-deployment)
- [Environment Variables](#environment-variables)
- [Data Backup](#data-backup)
- [Monitoring and Logs](#monitoring-and-logs)
- [Troubleshooting](#troubleshooting)
- [Security Recommendations](#security-recommendations)
- [Performance and Capacity](#performance-and-capacity)

---

## Local Development Deployment

### Backend

Run from the backend project root (the directory containing `app/`, `requirements.txt` and `.env.example`):

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt

# Configure environment variables (optional)
Copy-Item .env.example .env
# Edit .env to configure API keys

.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Without uv, use the standard library: `python -m venv .venv` → `.\\.venv\\Scripts\\Activate.ps1` → `python -m pip install -r requirements.txt`. On macOS / Linux use `python3 -m venv .venv` + `source .venv/bin/activate` and start with `./.venv/bin/python -m uvicorn ...`.

> This installs `requirements.txt`, not `requirements.lock.txt`: the lock file is produced by Linux `pip freeze` and drops environment markers, so its `uvloop` pin (Linux/macOS only) cannot compile on Windows and aborts the whole install. The lock file is for the image build.

### Frontend

```bash
cd recoach-frontend

# Install dependencies
npm ci

# Run the dev server
npm run dev
```

Visit http://127.0.0.1:4173. The port is pinned in `vite.config.ts` (`strictPort: true`) and matches the backend CORS allowlist, so it will not fall back to 5173; `/api` is proxied by Vite to `http://127.0.0.1:8000`.

### Terminal edition (optional)

`re-coach-tui/` is a standalone terminal application that never talks to `recoach-server`; it reads the same `RECOACH_*` environment variables:

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

---

## Docker Deployment (Recommended)

### Quick start

```bash
# Clone the repo
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent

# One-command start
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f
```

(The legacy standalone Docker Compose uses the hyphenated `docker-compose` with identical arguments.)

### Configure the LLM provider

Write application configuration into `recoach-server/.env`, then let the backend pick it up:

```bash
cp recoach-server/.env.example recoach-server/.env
# Edit recoach-server/.env, for example:
#   RECOACH_LLM_PROVIDER=deepseek
#   RECOACH_DEEPSEEK_API_KEY=your-key
docker compose up -d --force-recreate backend
```

Compose injects that file into the container through `env_file: ./recoach-server/.env` (when the file is missing it is not an error, and the built-in defaults apply).

> Do **not** put model settings in the `environment` block of `docker-compose.yml`: it takes precedence over `env_file` and silently overrides the user's own `.env`, which looks like "I edited `.env` but nothing changed".

### Custom ports

Change the backend **host** port with an environment variable — the container always listens on 8000:

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

The frontend entry port is hard-coded as `127.0.0.1:4173:4173` in `docker-compose.yml`. If you do change it, you must also update `RECOACH_CORS_ORIGINS` in the backend `.env` to the new origin, otherwise the browser cannot read the responses. Do not use `0.0.0.0:4173` — see [Access Control (required reading)](#access-control-required-reading).

---

## Production Deployment

> **Read this first**: the project is positioned as **single-user local use**. The bundled web client has no login page, so token mode is meaningless for it (it never sends the token). "Production deployment" here therefore means **running for the long term inside a trusted network**, not serving mutually untrusted users on the public internet. To publish the web UI you must terminate login in an outer reverse proxy first — see [Security Recommendations](#security-recommendations).

### Using Docker Compose (single machine)

#### 1. Prepare the server

```bash
# Update the system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Docker Compose v2 ships with Docker; for the legacy standalone binary:

```bash
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

**Application configuration** (model, keys, rate limits, memory switches) goes to `recoach-server/.env`:

```bash
cp recoach-server/.env.example recoach-server/.env
# Edit recoach-server/.env
chmod 600 recoach-server/.env
```

**Deployment parameters** (used only for Compose variable substitution) go to the repository-root `.env`:

```bash
cat > .env << 'ENVEOF'
RECOACH_BACKEND_PORT=8000
RECOACH_API_TOKEN=
RECOACH_TRUSTED_HOSTS=172.28.0.0/24
ENVEOF
chmod 600 .env
```

See [the two `.env` files](#the-two-env-files-easy-to-mix-up) for the split of responsibilities.

#### 4. Optional: adjust the Compose deployment parameters

`docker-compose.yml` already ships with settings suitable for long-running use (`restart: unless-stopped`, health checks, memory/CPU limits, `no-new-privileges`, volume mounts). To add log rotation, extend the backend service:

```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

#### 5. Start the services

```bash
docker compose up -d
docker compose logs -f

# Check health status
curl http://127.0.0.1:8000/health
```

#### 6. Configure an Nginx reverse proxy (when publishing the web UI)

The containers bind to loopback only, so exposing them requires a reverse proxy with login in front. The frontend container's own nginx already proxies `/api` to `backend:8000`, **strips the client-supplied `x-user-id`** (which blocks "declare your own identity and impersonate someone") and forwards `Authorization` for token mode — so on the host you only need to proxy the frontend entry:

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/recoach
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # Frontend (/api is forwarded to backend by the frontend container's nginx)
    location / {
        proxy_pass http://127.0.0.1:4173;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }
}
```

If you really do want the host nginx to proxy the backend `/api` as well, you must reproduce both the identity-header stripping and the SSE buffering settings yourself:

```nginx
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header x-user-id "";          # required: strip the client identity header
        proxy_set_header x-request-id $http_x_request_id;
        proxy_set_header Authorization $http_authorization;
        proxy_buffering off;                    # required: otherwise SSE events are batched
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
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
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com

# Auto-renew
sudo crontab -e
# Add: 0 3 * * * certbot renew --quiet
```

### Using Systemd (alternative)

If you don't use Docker, you can manage the backend with systemd:

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
# An externally reachable deployment must also set RECOACH_API_TOKEN, otherwise the
# service has no access control at all. Note that --host 0.0.0.0 makes the backend
# listen on every interface: if nginx sits in front, the backend port must not be
# reachable from outside (firewall only the frontend port), or the nginx
# identity-header stripping can be bypassed. If you really need direct backend
# access, bind 127.0.0.1 and let nginx proxy it.
Environment="RECOACH_API_TOKEN=<put your token here>"
ExecStart=/opt/recoach/recoach-server/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
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

(In this setup you build and host the frontend yourself: `npm ci && npm run build`, serve `dist/` from a static server, and configure the `/api` proxy plus identity-header stripping on your own. The frontend's `Dockerfile` + `nginx.conf` are a working reference.)

---

## Environment Variables

### Full configuration reference

The authoritative sources are `recoach-server/.env.example` (every entry is commented) and the defaults in `recoach-server/app/config.py`; `tools/consistency_audit.py` checks that the two stay in sync. The commonly used entries:

```bash
# ========== Basic configuration ==========
RECOACH_DB_PATH=./recoach.db
RECOACH_DEV_USER=dev_user
RECOACH_CORS_ORIGINS=http://127.0.0.1:4173,http://localhost:4173

# ========== Access control ==========
# Empty = development mode: /api/v1 only accepts loopback clients, everything else gets 401
RECOACH_API_TOKEN=
RECOACH_ALLOW_LOCAL_WITHOUT_TOKEN=true
# Extra trusted IPs / CIDRs in development mode; Docker Compose uses 172.28.0.0/24 by default
RECOACH_TRUSTED_HOSTS=
# Whether to expose /docs and /openapi.json; empty means token mode off, development mode on
# RECOACH_EXPOSE_DOCS=false

# ========== LLM configuration ==========
# Provider: template | openai_compatible | deepseek | anthropic
RECOACH_LLM_PROVIDER=template

# DeepSeek configuration
RECOACH_DEEPSEEK_API_KEY=
RECOACH_DEEPSEEK_BASE_URL=https://api.deepseek.com
RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
RECOACH_DEEPSEEK_THINKING=enabled
# The code default is medium; .env.example deliberately recommends low (with measured latency data)
RECOACH_DEEPSEEK_REASONING_EFFORT=low

# Anthropic configuration
RECOACH_ANTHROPIC_API_KEY=
RECOACH_ANTHROPIC_MODEL=claude-opus-5

# OpenAI-compatible configuration
RECOACH_LLM_BASE_URL=
RECOACH_LLM_API_KEY=
RECOACH_LLM_MODEL=

# LLM general parameters
RECOACH_LLM_MAX_TOKENS=10000
RECOACH_LLM_TIMEOUT=90
RECOACH_LLM_MAX_CONTINUATIONS=2
# When the provider fails before the first token: false = template fallback, true = MODEL_UNAVAILABLE
RECOACH_LLM_FAIL_FAST=false

# ========== Resource limits ==========
RECOACH_RATE_LIMIT_PER_MINUTE=30
RECOACH_MAX_CONCURRENT_TURNS=16
RECOACH_MAX_BODY_BYTES=65536

# ========== Memory system ==========
RECOACH_MEMORY_ON=true
RECOACH_MEMORY_MAX_SELECTED=3
RECOACH_MEMORY_HARD_LIMIT=4
RECOACH_MEMORY_CAPSULE_TOKENS=280
RECOACH_TOOL_BUDGET=1
```

### Environment variable priority

**Application side** (`pydantic-settings`, i.e. `recoach-server/app/config.py`):

1. Process environment variables (highest)
2. The `.env` file (note that relative paths such as `RECOACH_DB_PATH` resolve against the **working directory of the started process**)
3. Defaults in `config.py` (lowest)

**Compose side** (both end up as container environment variables):

1. `environment` in `docker-compose.yml` (highest; it overrides the next entry)
2. `env_file: ./recoach-server/.env`

So: application configuration belongs in `recoach-server/.env`; only values **specific to the container deployment** (absolute DB path, token, trusted networks) belong in Compose `environment`.

### The two `.env` files (easy to mix up)

There are two `.env` files in the repository with **different responsibilities — do not merge them** (both are ignored by `.gitignore`):

| File | Who reads it | What goes in it |
|---|---|---|
| `./.env` (repository root) | `docker compose`, for variable substitution | Deployment parameters such as `RECOACH_BACKEND_PORT` |
| `./recoach-server/.env` | The application itself (Compose also injects it into the container via `env_file`) | Model, keys, rate limits, auth — all application configuration |

The test is simple: **"will the process running inside the container read it?"** Yes → `recoach-server/.env`; it is only used to build the Compose command line → root `.env`.

Application configuration written to the root `.env` has no effect; a port written into `recoach-server/.env` is never seen by Compose.

---

## Data Backup

The database location depends on how you start the service: under Docker it is `./data/recoach.db` on the host (mounted at `/app/data/recoach.db` in the container); in local development it defaults to `./recoach.db` inside the backend directory (controlled by `RECOACH_DB_PATH`).

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

SQLite runs in WAL mode, so **do not** copy the single file while the service is running and restore it directly; either stop the backend first (which is what the restore flow below does) or copy `-wal` / `-shm` alongside it.

### Restore data

```bash
# Stop the services
docker compose down

# Restore the database
cp backups/recoach.db.20260904-030000 data/recoach.db

# Restart the services
docker compose up -d
```

---

## Monitoring and Logs

### View logs

```bash
# Docker logs
docker compose logs -f backend
docker compose logs -f frontend

# System logs (systemd)
sudo journalctl -u recoach-backend -f
```

### Performance monitoring

Use Docker stats:

```bash
docker stats recoach-backend recoach-frontend
```

In-application runtime metrics (time to first token, memory search time, context compile time and their p50/p95) are available from `GET /api/v1/metrics/summary`; part of them is shown in the web UI's Performance view.

### Health check

```bash
# Backend health check (always unauthenticated)
curl http://127.0.0.1:8000/health

# Automatic monitoring script
cat > /opt/recoach/healthcheck.sh << 'HEALTHEOF'
#!/bin/bash
URL="http://127.0.0.1:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $URL)

if [ $RESPONSE -ne 200 ]; then
    echo "Health check failed: $RESPONSE"
    # Send an alert (e.g. DingTalk, email)
    docker compose restart backend
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
docker compose logs backend

# Check the config
docker compose config

# Rebuild
docker compose build --no-cache
```

### Database locked

```bash
# In case of SQLite lock
docker compose down
rm data/recoach.db-wal data/recoach.db-shm
docker compose up -d
```

### Out of memory

`docker-compose.yml` already sets `mem_limit: 1g` for the backend and 256m for the frontend; adjust those two lines if needed. Note that under the Compose specification a container uses `mem_limit` — `deploy.resources.limits` only applies in Swarm mode:

```yaml
services:
  backend:
    mem_limit: 2g
```

### Backend port 8000 fails to bind (common on Windows)

Windows reserves port ranges for Hyper-V / WSL, and a port inside a reserved range **cannot be bound** — the error looks like `[WinError 10013] An attempt was made to access a socket in a way forbidden by its access permissions`. On many machines 8000 happens to sit inside one of those ranges.

Check first:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

If 8000 is in the list, change the host port (the container still listens on 8000):

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

Or permanently release that range from an elevated prompt (this restarts winnat — use with care):

```powershell
net stop winnat
netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1
net start winnat
```

Note that this only affects the host-side port mapping. The frontend entry port 4173 is usually unaffected, so **`http://127.0.0.1:4173` keeps working even if you change nothing** — 8000 is only needed when accessing the backend API directly (for example `/docs`).

---

## Security Recommendations

### Access Control (required reading)

Re:Coach expresses identity through the `x-user-id` header. That header is **only trustworthy after the caller has authenticated**, and authentication is carried by `RECOACH_API_TOKEN`:

| `RECOACH_API_TOKEN` | Behaviour |
|---|---|
| Empty (default) | **Development mode**: `/api/v1` only accepts loopback clients; everything else gets `401` |
| Set | **Token mode**: every `/api/v1` request must carry `Authorization: Bearer <token>` |

Generate a token:

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

Token mode targets trusted API clients. The bundled browser UI has no login page and never reads, stores or attaches `RECOACH_API_TOKEN`, so setting the token on the backend alone makes every browser request return `401`. Never put the token in a `VITE_*` variable or the frontend image — that publishes the shared secret in the browser bundle.

To publish the web UI you must terminate login in an outer reverse proxy that injects the backend Bearer token and the trusted user identity on the server side, and keep the backend port unreachable from the public internet. Without that identity layer, only the loopback-only local deployment described below is appropriate.

### What the trust boundary really means

The token is a **single shared secret**: it answers "who may reach this instance", and **not** "how are users isolated from each other":

- A caller holding the token can still set `x-user-id: <anything>` and read or write that identity's data.
- The current model therefore suits **single-user self-hosting** or deployments where **all users trust each other**.
- For mutually untrusted users you need real login at the reverse proxy (OIDC / your own accounts), you must derive `user_id` from the server-side session instead of a request header, and each user needs their own credential. The change points are `recoach-server/app/auth.py` and `app/routes/sessions.py:current_user_id`.

`/docs` and `/openapi.json` are closed by default in token mode; set `RECOACH_EXPOSE_DOCS=true` to expose them deliberately.

### Local (single-user) deployment essentials

The project is currently positioned as **single-user local use**. In that setting "bind loopback only" is enough, and no token is needed:

```yaml
# docker-compose.yml
frontend:
  ports:
    - "127.0.0.1:4173:4173"   # the only entry point, loopback only
backend:
  ports:
    - "127.0.0.1:8000:8000"   # not exposed directly
  environment:
    - RECOACH_API_TOKEN=      # empty = development mode
    - RECOACH_TRUSTED_HOSTS=172.28.0.0/24
```

**`RECOACH_TRUSTED_HOSTS` is the critical entry here.** When the frontend nginx proxies `/api`, the backend sees the nginx container's internal IP (`172.28.0.x`), which is not loopback; accepting only loopback would make every API call return `401`. That is why Compose pins the subnet `172.28.0.0/24` and the backend declares it explicitly, and why the two must agree.

> The two settings are **a pair**: looping back only → the LAN cannot get in; declaring the trusted subnet → container-to-container proxying works. Change one and you must check the other.

Do not simply change the entry to `0.0.0.0:4173` and set `RECOACH_API_TOKEN`: the bundled web client never sends that token, so every API request would just return `401`. Before listening externally, stand up an authenticated reverse proxy that injects the Bearer token server-side; otherwise anyone on the LAN can use the full application.

### Rate limits and resource caps

- `RECOACH_RATE_LIMIT_PER_MINUTE` (default 30): billable requests per identity per minute. It only applies to endpoints that really call the LLM (creating a Turn and creating a Fork); completed-state replays and 409 conflicts consume no quota. Exceeding it returns `429`.
- `RECOACH_MAX_CONCURRENT_TURNS` (default 16): concurrent streaming Turns. Exceeding it returns `503 SERVICE_BUSY`. This blocks the resource exhaustion caused by "open many SSE streams and never read them".
- `RECOACH_MAX_BODY_BYTES` (default 65536): request body size limit; exceeding it returns `413`.
- All counters live in process memory, so **with multiple replicas each replica counts separately**. For a strict global quota, move to a shared backend such as Redis.

### Other

1. **Use HTTPS** - a production environment must use SSL certificates
2. **Protect API keys** - use environment variables or a secrets manager
3. **Restrict CORS** - only allow trusted domains; with `*` the server automatically disables `allow_credentials`
4. **Back up regularly** - set up automatic backup tasks
5. **Update dependencies** - the backend image installs from `requirements.lock.txt` so builds are reproducible. **Regenerate the lock file after changing dependencies**, and always resolve it with the same Python version as the image:

   ```bash
   cd recoach-server
   docker run --rm -v "$PWD:/w" -w /w python:3.10-slim \
     sh -c "pip install -q -r requirements.txt && pip freeze" > requirements.lock.txt
   ```

   > A pitfall we hit: that lock file was originally generated on Python 3.13, and its `websockets==17.0.1` requires `Python>=3.11`, while the image is 3.10 — which made `docker-compose build` fail outright. A lock file only reveals such problems when it is actually installed; the image had not been using it, so the incompatibility stayed hidden for a long time.
6. **Monitor logs** - set up log alerts
7. **Firewall** - only expose the necessary ports (80, 443). The backend's 8000 is bound to `127.0.0.1` in Compose; do not change it back to `0.0.0.0`, which would bypass nginx's identity-header stripping

---

## Performance and Capacity

### What exists today

- Storage is SQLite (WAL + FTS5, falling back to LIKE when FTS5 is unavailable): a single writer on a local file, suitable for a single-instance, single-user setting. `GET /api/v1/metrics/summary` provides p50/p95 runtime metrics.
- Rate limiting and the concurrency gate both live in process memory, so **horizontal scaling with several replicas cannot provide a global quota**.

### Not implemented yet (required before scaling capacity)

These are directions, not existing features:

- An external database adapter such as PostgreSQL: there is no database abstraction layer today — the schema and SQL live directly in `app/db.py`.
- A shared rate-limit / cache backend such as Redis: it would replace the in-memory implementations in `app/services/ratelimit.py` and `app/services/turn_gate.py`.
- Session affinity and SSE forwarding across replicas.

---

## Further Reading

- [Quick Start](quickstart.en.md)
- [Backend docs](../recoach-server/README.md) - architecture, API and capability boundaries
- [Backend LLM configuration](../recoach-server/README_LLM_CONFIG.md)
- [Frontend docs](../recoach-frontend/README.md)
- [TUI docs](../re-coach-tui/README.md)
- [API Docs](http://127.0.0.1:8000/docs)
