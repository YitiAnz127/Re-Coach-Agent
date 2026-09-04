# Quick Start Guide

[简体中文](quickstart.md) | [English](quickstart.en.md)

This guide helps you get the Re:Coach project running within 5 minutes.

## Prerequisites

- Docker >= 20.10
- Docker Compose >= 2.0
- (Optional) Git

## Quick Start

### 1. Clone the project (if you haven't already)

```bash
git clone https://github.com/YitiAnz127/Re_Coach.git
cd Re_Coach
```

### 2. One-command start

```bash
docker-compose up -d
```

The first run builds the images and takes about 3–5 minutes.

### 3. Verify it's running

Visit the following URLs to confirm the services are healthy:

- **Frontend**: http://localhost:4173
- **Backend API docs**: http://localhost:8000/docs
- **Backend health check**: http://localhost:8000/health

### 4. Start using it

Open your browser and go to http://localhost:4173 — you should see the Re:Coach interface.

The default configuration uses **template mode** (no API key required), so you can try out the basic features.

## Configure a Real LLM

To use a real AI model, you need to configure an API key:

### Option 1: Use environment variables (recommended)

Create a `.env` file:

```bash
# DeepSeek configuration
export DEEPSEEK_API_KEY="your-api-key-here"

# Or Anthropic configuration
export ANTHROPIC_API_KEY="your-api-key-here"
```

Then edit `docker-compose.yml` and uncomment the relevant LLM configuration.

### Option 2: Edit docker-compose.yml directly

Edit `docker-compose.yml` and find the backend service's `environment` section:

```yaml
environment:
  # Uncomment and configure DeepSeek
  - RECOACH_LLM_PROVIDER=deepseek
  - RECOACH_DEEPSEEK_API_KEY=your-api-key-here
  - RECOACH_DEEPSEEK_MODEL=deepseek-v4-flash
```

Restart the services:

```bash
docker-compose restart backend
```

## Common Commands

### View logs

```bash
# View all logs
docker-compose logs -f

# Only backend logs
docker-compose logs -f backend

# Only frontend logs
docker-compose logs -f frontend
```

### Stop services

```bash
docker-compose down
```

### Restart services

```bash
docker-compose restart
```

### Clean and rebuild

```bash
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

## Local Development Mode

If you want to develop rather than just run the project:

### Backend development

```bash
cd recoach-server

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the dev server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend development

```bash
cd recoach-frontend

# Install dependencies
npm install

# Run the dev server
npm run dev
```

The dev server runs at http://localhost:5173 (note: not 4173).

## Troubleshooting

### Port already in use

If you see a port conflict error, edit the port mapping in `docker-compose.yml`:

```yaml
ports:
  - "8001:8000"  # change 8000 to 8001
```

### Frontend can't connect to the backend

Check the frontend environment variable configuration, making sure `VITE_API_BASE_URL` is correct:

```yaml
environment:
  - VITE_API_BASE_URL=http://localhost:8000/api/v1
```

If you changed the backend port, update this too.

### Database file permission issues

Make sure the `./data` directory exists and is writable:

```bash
mkdir -p data
chmod 755 data
```

### Check container status

```bash
docker-compose ps
```

You should see both containers in `healthy` status.

## Next Steps

- Check the [API docs](http://localhost:8000/docs) for backend endpoints
- Read the [Deployment Guide](deployment.md) for production deployment

## Need Help?

- Open a [GitHub Issue](https://github.com/YitiAnz127/Re_Coach/issues)
- View the project [README](../README.md)
