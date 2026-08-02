# Troubleshooting

Common issues and their solutions.

---

## WhatsApp Issues

### "Something went wrong" response

**Cause:** An agent threw an unhandled exception.

**Fix:** Check server logs for the traceback:
```bash
# Local
# Check terminal output

# Azure
az webapp log tail --name whatsapp-ai-agent-sunny --resource-group ai-agent-rg
```

### WhatsApp webhook not receiving messages

**Possible causes:**
1. **Webhook URL not set** — Go to Meta Developer Console → WhatsApp → Configuration → verify callback URL
2. **Verify token mismatch** — `WHATSAPP_VERIFY_TOKEN` must match what's in Meta console
3. **ngrok not running** (local) — Restart ngrok and update the webhook URL in Meta console
4. **App not running** (Azure) — Check `/health` endpoint and restart if needed

### 401 Unauthorized from WhatsApp API

**Cause:** Access token expired (temporary tokens last 24 hours).

**Fix:** 
- Generate a permanent token via System User (see [Setup Guide](setup-guide.md#permanent-whatsapp-token-recommended))
- Or regenerate temporary token in Meta Developer Console → WhatsApp → API Setup
- Update `WHATSAPP_ACCESS_TOKEN` in `.env` or App Settings

### Messages not reaching my phone

**Cause:** The "messages" field is not subscribed in webhook configuration.

**Fix:** Meta Developer Console → WhatsApp → Configuration → Webhook fields → ensure "messages" is checked.

---

## Gmail Issues

### "invalid_scope" error when sending email

**Cause:** The OAuth refresh token was generated without the `gmail.send` scope.

**Fix:**
1. Ensure `gmail.send` scope is added in Google Cloud Console → Google Auth Platform → Data access
2. Re-run the setup script to generate a new token with both scopes:
   ```bash
   python scripts/setup_gmail_oauth.py --credentials /path/to/credentials.json
   ```
3. Update `GOOGLE_REFRESH_TOKEN` in `.env`
4. Restart the server

### "unauthorized_client" error

**Cause:** Refresh token was generated with a different OAuth client than what's configured.

**Fix:** Make sure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` match the credentials used to generate the refresh token. If in doubt, regenerate the token.

### "redirect_uri_mismatch" error

**Cause:** Using a "Web application" type OAuth client instead of "Desktop".

**Fix:** Create a new OAuth client with type "Desktop app" in Google Cloud Console → Credentials.

### Emails landing in spam

**Possible causes:**
1. **Missing display name** — Set `SENDER_DISPLAY_NAME` in `.env`
2. **Gmail "Send mail as" name** — Go to Gmail Settings → Accounts and Import → Send mail as → verify your name
3. **App in testing mode** — In Google Cloud Console → Google Auth Platform → Audience → consider publishing the app (even as internal)
4. **Sending to yourself** — Gmail sometimes flags self-sent programmatic emails. Test with a different recipient.

---

## Azure Deployment Issues

### GLIBC_2.33 not found

**Cause:** Python wheels built on CI Ubuntu have newer glibc than App Service Debian Bullseye.

**Fix:** Deploy source only and let Oryx build on App Service:
```
SCM_DO_BUILD_DURING_DEPLOYMENT=true
ENABLE_ORYX_BUILD=true
```
Do NOT include `.python_packages/` or pre-built `antenv/` in the deployment zip.

### App fails to start after deploy

**Check:**
1. Startup file set correctly: `bash infra/startup.sh`
2. `WEBSITES_PORT=8000` is set
3. Check Oryx build logs: Azure Portal → App Service → Deployment Center → Logs
4. Give Oryx time: initial build can take 5-10 minutes. Set `WEBSITES_CONTAINER_START_TIME_LIMIT=600`

### Health check returns 403 after deploy

**Cause:** App Service stopped during deployment.

**Fix:**
```bash
az webapp start --name whatsapp-ai-agent-sunny --resource-group ai-agent-rg
```

### GitPython warnings / errors

**Cause:** `git` binary not available on App Service.

**Fix:** Already handled — the code falls back to HTTP-based git operations. Set `GIT_PYTHON_REFRESH=quiet` to suppress warnings.

---

## LLM / Agent Issues

### Router classifies everything as "general"

**Possible causes:**
1. Azure OpenAI deployment not responding — check endpoint and key
2. Prompt file not found — ensure `src/agent/prompts/router_classify.txt` exists in deployment
3. API key expired — regenerate in Azure Portal

### Developer agent produces empty changes

**Cause:** LLM response didn't contain valid JSON.

**Fix:** Check logs for the raw LLM response. May need to adjust `developer_codegen.txt` prompt or increase `max_tokens`.

### Review loop stuck (3 iterations then fails)

**Cause:** Reviewer keeps requesting changes that developer can't satisfy.

**Fix:** After 3 iterations, the system now proceeds to human approval regardless. Review the `reviewer_system.txt` prompt for overly strict criteria.

---

## Database Issues

### "no such table" errors

**Cause:** Database schema not initialized.

**Fix:** The app auto-creates tables on startup via `init_db()`. Delete `agent.db` and restart:
```bash
rm agent.db
uvicorn agent.main:app --host 0.0.0.0 --port 8000
```

### ConversationSession missing columns

**Cause:** Schema changed after initial DB creation.

**Fix:** The `core/session.py` has retry logic that drops and recreates the table on schema mismatch. If that fails, delete the DB and restart.
