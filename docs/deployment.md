# Deployment Guide

Deploying the WhatsApp AI Agent to Azure App Service with Azure DevOps CI/CD pipelines.

---

## Infrastructure

| Resource | Name | Details |
|----------|------|---------|
| Resource Group | `ai-agent-rg` | Central India |
| App Service Plan | `ai-agent-plan` | B1 Linux, Central India |
| Web App | `whatsapp-ai-agent-sunny` | Python 3.12, Always On |
| Source Repo | `web.Whatsapp-AI-Agent` | Azure DevOps: Az-FullStack/Project-NIT |

**Production URL:** `https://whatsapp-ai-agent-sunny.azurewebsites.net`

---

## CI/CD Pipeline

The pipeline is defined in `azure-pipelines.yml` (root) and kept in sync with `infra/azure-deploy.yml`.

### Pipeline Stages

#### Stage 1: Build & Test

1. Install Python 3.12
2. Install dependencies from `requirements.txt` and `pyproject.toml`
3. Run pytest (continue on error — don't block deploy on flaky tests)
4. rsync source files into a staging directory (excluding `.git`, `.venv`, `.env`, `agent.db`, `workspaces/`, `__pycache__/`)
5. Create zip artifact
6. Publish to pipeline artifacts

#### Stage 2: Deploy

1. Download the artifact zip
2. Configure App Service settings:
   - `SCM_DO_BUILD_DURING_DEPLOYMENT=true` — Oryx builds on App Service
   - `ENABLE_ORYX_BUILD=true` — use Oryx build system
   - `WEBSITES_PORT=8000` — tell App Service which port to proxy
   - `WEBSITES_CONTAINER_START_TIME_LIMIT=600` — allow 10 min for Oryx build
   - `GIT_PYTHON_REFRESH=quiet` — suppress GitPython warnings when git binary is missing
3. Set startup command: `bash infra/startup.sh`
4. ZIP deploy (async to avoid CLI timeout)
5. Poll `/health` endpoint (up to 48 attempts, 15s apart = 12 minutes)

### Why Oryx Remote Build?

The pipeline ships **source only** and lets Oryx build the Python virtualenv (`antenv`) directly on App Service. This avoids a common failure:

> Pre-building wheels on Ubuntu CI with newer glibc → deploying to App Service's Debian Bullseye → `GLIBC_2.33 not found` errors from `cryptography` and `pydantic` C-extensions.

By building on App Service itself, the compiled extensions match the host's glibc version.

---

## Startup Script

`infra/startup.sh`:

1. Finds the `antenv` virtual environment (searches multiple possible locations)
2. Activates it
3. Sets `PYTHONPATH=src`
4. Launches: `uvicorn agent.main:app --host 0.0.0.0 --port 8000`

---

## App Settings (Environment Variables)

All secrets are stored as Azure App Service Application Settings (encrypted at rest).

### Required Settings

```
AZURE_OPENAI_ENDPOINT=https://xxx.cognitiveservices.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-10-21
OPENAI_API_KEY=<key>

WHATSAPP_VERIFY_TOKEN=<token>
WHATSAPP_ACCESS_TOKEN=<permanent-system-user-token>
WHATSAPP_PHONE_NUMBER_ID=<id>
WHATSAPP_APP_SECRET=<secret>

GOOGLE_CLIENT_ID=<id>
GOOGLE_CLIENT_SECRET=<secret>
GOOGLE_REFRESH_TOKEN=<token>
SENDER_DISPLAY_NAME=Sunny Kushwaha

ALLOWED_PHONE_NUMBERS=["919643877357"]
LOG_LEVEL=INFO
DATABASE_URL=sqlite+aiosqlite:///./agent.db
```

### Platform Settings

```
SCM_DO_BUILD_DURING_DEPLOYMENT=true
ENABLE_ORYX_BUILD=true
WEBSITES_PORT=8000
WEBSITES_CONTAINER_START_TIME_LIMIT=600
GIT_PYTHON_REFRESH=quiet
```

### Setting Values via CLI

```bash
# Set a single setting
az webapp config appsettings set \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg \
  --settings KEY=value

# Set multiple
az webapp config appsettings set \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg \
  --settings KEY1=value1 KEY2=value2
```

---

## Manual Deployment

If CI/CD is not set up, deploy manually:

```bash
# Create deployment zip (excluding dev/runtime files)
zip -r deploy.zip . \
  -x ".env" "*.db*" ".venv/*" "__pycache__/*" ".git/*" \
     "tmp/*" "workspaces/*" "agent.db*" ".DS_Store" \
     "*.egg-info/*" "sample-apps/*"

# Deploy
az webapp deploy \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg \
  --type zip \
  --src-path deploy.zip

# Verify
curl https://whatsapp-ai-agent-sunny.azurewebsites.net/health
```

---

## Monitoring

### Logs

```bash
# Stream live logs
az webapp log tail \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg

# Download log files
az webapp log download \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg \
  --log-file logs.zip
```

### Health Check

```bash
# Basic check
curl https://whatsapp-ai-agent-sunny.azurewebsites.net/health

# Deep check (validates WhatsApp token)
curl "https://whatsapp-ai-agent-sunny.azurewebsites.net/health?deep=true"
```

### Restart

```bash
az webapp restart \
  --name whatsapp-ai-agent-sunny \
  --resource-group ai-agent-rg
```

---

## Scaling Considerations

The current setup is designed for **single-user personal use**. To scale:

| Component | Current | Scaled |
|-----------|---------|--------|
| Database | SQLite (file-based) | PostgreSQL on Azure |
| Task runner | FastAPI BackgroundTasks | Celery + Redis |
| App Service | B1 (1 core, 1.75 GB) | B2/S1 or Container Apps |
| Checkpointer | AsyncSqliteSaver | AsyncPostgresSaver |
| Git operations | Temp directories | Persistent Azure File Share |
