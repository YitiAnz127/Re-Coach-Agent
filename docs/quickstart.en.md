# Quick Start Guide

[简体中文](quickstart.md) | [English](quickstart.en.md)

This guide helps you get the Re:Coach project running within 5 minutes.

## Prerequisites

- Docker >= 20.10
- Docker Compose >= 2.0 (invoked as `docker compose`; the standalone `docker-compose` binary is equivalent)
- (Optional) Git

## Quick Start

### 1. Clone the project (if you haven't already)

```bash
git clone https://github.com/YitiAnz127/Re-Coach-Agent.git
cd Re-Coach-Agent
```

### 2. One-command start

```bash
docker compose up -d
```

The first run builds the images and takes about 3–5 minutes.

### 3. Verify it's running

Visit the following URLs to confirm the services are healthy:

- **Frontend**: http://127.0.0.1:4173
- **Backend API docs**: http://127.0.0.1:8000/docs
- **Backend health check**: http://127.0.0.1:8000/health

Both ports are bound to loopback (`127.0.0.1`) only, and that is deliberate: the bundled web client
holds no access token, so no other machine can reach the app by default. Read the
[access-control section of the deployment guide](deployment.en.md#access-control-required-reading)
before exposing it.

### 4. Start using it

Open your browser and go to http://127.0.0.1:4173 — you should see the Re:Coach interface.

The default configuration uses **template mode** (`RECOACH_LLM_PROVIDER=template`, no API key
required), which exercises the full protocol and memory loop, but the answer is only a teaching
skeleton.

## Configure a Real LLM

To use a real AI model, you need to configure an API key.

Application configuration is read only from `recoach-server/.env`. Do **not** put model settings in
the `environment` block of `docker-compose.yml` — that takes precedence over `env_file` and will
silently override your own `.env`, producing "I edited `.env` but nothing changed".

```bash
# 1. Create the config from the template (backend project root)
cp recoach-server/.env.example recoach-server/.env

# 2. Edit recoach-server/.env: pick a provider and fill in the key, e.g. DeepSeek:
#    RECOACH_LLM_PROVIDER=deepseek
#    RECOACH_DEEPSEEK_API_KEY=your-key
#    RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash

# 3. Make the backend pick it up
docker compose up -d --force-recreate backend
```

Compose injects that file into the container (`env_file: ./recoach-server/.env`; when it is missing
the built-in defaults are used instead). Supported providers and every field are documented in the
[LLM configuration guide](../recoach-server/README_LLM_CONFIG.md).

> The `.env` at the repository root is a **different** file: `docker compose` uses it only for variable
> substitution (for example `RECOACH_BACKEND_PORT`), and it is never injected into a container. See
> [the two `.env` files](deployment.en.md#the-two-env-files-easy-to-mix-up).

## Common Commands

### View logs

```bash
# View all logs
docker compose logs -f

# Only backend logs
docker compose logs -f backend

# Only frontend logs
docker compose logs -f frontend
```

### Stop services

```bash
docker compose down
```

### Restart services

```bash
docker compose restart
```

### Clean and rebuild

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

## Local Development Mode

If you want to develop rather than just run the project:

### Backend development

Run from the backend project root (the directory containing `app/`, `requirements.txt` and `.env.example`):

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

On macOS / Linux replace the first two lines with
`python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt` and start with
`./.venv/bin/python -m uvicorn ...`.

> This installs `requirements.txt`, not `requirements.lock.txt`: the lock file is produced by Linux
> `pip freeze` and drops environment markers, so its `uvloop` pin (Linux/macOS only) cannot compile on
> Windows and aborts the whole install. The lock file is for the image build.

### Frontend development

Run from the frontend project root:

```bash
npm ci
npm run dev
```

The dev server is pinned to http://127.0.0.1:4173 (`strictPort: true` in `vite.config.ts`, matching the
backend CORS allowlist; it will not fall back to 5173). `/api` is proxied by Vite to
`http://127.0.0.1:8000`, and with no environment variables set the frontend talks to the real backend.

### Terminal edition (optional)

`re-coach-tui/` is a standalone terminal application that needs no running backend:

```bash
cd re-coach-tui
npm install
npm run build
npm start
```

## Troubleshooting

### Backend port 8000 fails to bind (common on Windows)

Windows reserves port ranges for Hyper-V / WSL, and a port inside a reserved range cannot be bound —
the error looks like `[WinError 10013]`. Check first:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

If 8000 is in the list, change the **host** port (the container still listens on 8000):

```bash
RECOACH_BACKEND_PORT=8100 docker compose up -d
```

### Frontend can't connect to the backend

The frontend uses the same-origin `/api/v1` by default. Under Docker the frontend nginx proxies it to
`backend:8000`; in local development Vite proxies it to `http://127.0.0.1:8000`. To point somewhere
else, set `VITE_API_BASE_URL` in the frontend `.env.local` (the repo ships no `.env.example` there, so
create the file yourself; you must restart the dev server after changing it).

### Database file permission issues

Make sure the `./data` directory exists and is writable:

```bash
mkdir -p data
chmod 755 data
```

### Check container status

```bash
docker compose ps
```

You should see both containers in `healthy` status.

### The page shows "local demo"

That means the frontend is in demo mode. Check whether `.env.local` in the frontend project sets
`VITE_DEMO_MODE=true`, remove it (or set `false`), then restart `npm run dev`.

## Next Steps

- Check the [API docs](http://127.0.0.1:8000/docs) for backend endpoints
- Read the [Deployment Guide](deployment.en.md) for production deployment and access control
- Read the [Backend docs](../recoach-server/README.md) for what is and is not implemented

## Need Help?

- Open a [GitHub Issue](https://github.com/YitiAnz127/Re-Coach-Agent/issues)
- View the project [README](../README.md)
