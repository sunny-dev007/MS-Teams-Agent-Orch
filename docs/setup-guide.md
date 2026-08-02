# Setup Guide

Complete step-by-step guide to get the WhatsApp AI Agent running locally and in production.

---

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | 3.11+ (3.12 recommended) | Runtime |
| Azure subscription | Pay-As-You-Go | OpenAI + App Service hosting |
| Meta Developer account | — | WhatsApp Business API |
| Google Cloud project | — | Gmail + Calendar APIs |
| GitHub account | — | Repository access (optional) |
| Azure DevOps org | — | Repository + pipeline access (optional) |

---

## 1. Local Development Setup

### 1.1 Clone & Install

```bash
git clone https://Az-FullStack@dev.azure.com/Az-FullStack/Project-NIT/_git/web.Whatsapp-AI-Agent
cd web.Whatsapp-AI-Agent

python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

pip install -e ".[dev]"
```

### 1.2 Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` with your actual credentials. See [Secrets & Credentials](#3-secrets--credentials) below for how to generate each one.

### 1.3 Run the Server

```bash
uvicorn agent.main:app --host 0.0.0.0 --port 8000 --reload
```

### 1.4 Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","version":"0.1.0"}
```

---

## 2. External Service Configuration

### 2.1 Azure OpenAI

1. Go to **Azure Portal** → **Create a resource** → **Azure AI services** → **Azure OpenAI**
2. Create a resource in a supported region (e.g., East US)
3. Go to **Azure AI Foundry** → **Deployments** → **Create deployment**
4. Deploy model: `gpt-4o-mini` (cheapest option, sufficient for this use case)
5. Copy:
   - **Endpoint URL** → `AZURE_OPENAI_ENDPOINT`
   - **Key** → `OPENAI_API_KEY`
   - **Deployment name** → `AZURE_OPENAI_DEPLOYMENT`

### 2.2 WhatsApp Business API

1. Go to **[developers.facebook.com](https://developers.facebook.com)** → **My Apps** → **Create App**
2. Select **Business** type → fill in app name
3. Add **WhatsApp** product to your app
4. In **WhatsApp** → **API Setup**:
   - Note the **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - Generate a temporary access token (or create a permanent one — see below)
5. In **App Settings** → **Basic**:
   - Copy **App Secret** → `WHATSAPP_APP_SECRET`
6. Generate a random verify token:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(16))"
   ```
   Put this in `WHATSAPP_VERIFY_TOKEN`

#### WhatsApp Webhook Configuration

1. In the Meta Developer Console → **WhatsApp** → **Configuration**
2. Set **Callback URL**: `https://your-domain.com/webhooks/whatsapp`
3. Set **Verify Token**: same value as `WHATSAPP_VERIFY_TOKEN` in `.env`
4. Subscribe to: **messages**

#### Permanent WhatsApp Token (Recommended)

The temporary token expires every 24 hours. For production:

1. Go to **Meta Business Suite** → **Settings** → **Business Settings**
2. Under **Users** → **System Users** → **Add**
3. Name: "AI Agent", Role: **Admin**
4. Click **Add Assets** → select your WhatsApp app → toggle **Full Control**
5. Click **Generate Token** → select permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
6. Copy token → `WHATSAPP_ACCESS_TOKEN` (this token does not expire)

### 2.3 Gmail & Calendar OAuth

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)**
2. Create or select a project
3. Enable APIs:
   - **Gmail API** (API Library → search "Gmail API" → Enable)
   - **Google Calendar API** (API Library → search "Calendar API" → Enable)
4. Configure **OAuth Consent Screen**:
   - Go to **Google Auth Platform** → **Data access**
   - Add scopes:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/gmail.send`
     - `https://www.googleapis.com/auth/calendar.events`
5. Create **OAuth Credentials**:
   - Go to **Credentials** → **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app** (not Web)
   - Download the JSON file
6. Run the setup script:
   ```bash
   python scripts/setup_gmail_oauth.py --credentials /path/to/downloaded_credentials.json
   ```
7. Complete the browser flow → copy the printed values into `.env`:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`

### 2.4 GitHub (Optional)

1. Go to **GitHub** → **Settings** → **Developer Settings** → **Fine-grained Personal Access Tokens**
2. Create token with permissions:
   - **Contents**: Read and Write
   - **Pull Requests**: Read and Write
   - **Actions**: Read and Write (for workflow triggers)
   - **Metadata**: Read
3. Copy token → `GITHUB_TOKEN`
4. Set `GITHUB_DEFAULT_OWNER` to your GitHub username or org

### 2.5 Azure DevOps (Optional)

1. Go to **Azure DevOps** → **User Settings** (top right) → **Personal Access Tokens**
2. Create token with scopes:
   - **Code**: Read & Write
   - **Build**: Read & Execute
   - **Project and Team**: Read
3. Copy token → `AZDO_PAT`
4. Set `AZDO_ORG_URL` to `https://dev.azure.com/your-org`

---

## 3. Secrets & Credentials Reference

| Variable | Source | Required |
|----------|--------|----------|
| `AZURE_OPENAI_ENDPOINT` | Azure Portal → Cognitive Services | Yes |
| `OPENAI_API_KEY` | Azure Portal → Cognitive Services → Keys | Yes |
| `AZURE_OPENAI_DEPLOYMENT` | Azure AI Foundry → Deployments | Yes |
| `WHATSAPP_ACCESS_TOKEN` | Meta Business Suite → System User token | Yes |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta Developer Console → WhatsApp → API Setup | Yes |
| `WHATSAPP_APP_SECRET` | Meta Developer Console → App Settings → Basic | Yes |
| `WHATSAPP_VERIFY_TOKEN` | Self-generated random string | Yes |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → Credentials (Desktop type) | Yes |
| `GOOGLE_CLIENT_SECRET` | Same as above | Yes |
| `GOOGLE_REFRESH_TOKEN` | Output of `scripts/setup_gmail_oauth.py` | Yes |
| `SENDER_DISPLAY_NAME` | Your name as email sender | Yes |
| `ALLOWED_PHONE_NUMBERS` | JSON array of allowed phone numbers | Yes |
| `GITHUB_TOKEN` | GitHub → Settings → Developer Settings → PATs | Optional |
| `GITHUB_DEFAULT_OWNER` | Your GitHub username | Optional |
| `AZDO_ORG_URL` | Your Azure DevOps org URL | Optional |
| `AZDO_PAT` | Azure DevOps → User Settings → PATs | Optional |

---

## 4. Local Webhook Testing (ngrok)

For local development, use ngrok to expose your server to the internet:

```bash
# Install ngrok
brew install ngrok    # macOS
# or download from https://ngrok.com

# Start tunnel
ngrok http 8000
```

Copy the HTTPS forwarding URL (e.g., `https://xxxx.ngrok-free.app`) and set it as your WhatsApp webhook URL in Meta Developer Console:

```
https://xxxx.ngrok-free.app/webhooks/whatsapp
```

The app automatically adds the `ngrok-skip-browser-warning` header to bypass ngrok's free-tier interstitial page.

---

## 5. Running Tests

```bash
# Install test dependencies
pip install -e ".[dev]"

# Run all tests
PYTHONPATH=src python -m pytest tests/ -v

# Run specific test file
PYTHONPATH=src python -m pytest tests/test_api/test_whatsapp_webhook.py -v
```
