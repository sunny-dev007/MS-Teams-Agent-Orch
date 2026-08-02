# API Reference

All HTTP endpoints exposed by the WhatsApp AI Agent.

**Base URL (Production):** `https://whatsapp-ai-agent-sunny.azurewebsites.net`
**Base URL (Local):** `http://localhost:8000`

---

## Endpoints

### GET /health

Health check endpoint.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `deep` | bool | `false` | If `true`, also validates WhatsApp access token |

**Response (200):**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "whatsapp": "ok"          // only present when deep=true
}
```

**Response (200, degraded):**
```json
{
  "status": "degraded",
  "version": "0.1.0",
  "whatsapp": "token_expired"
}
```

---

### GET /webhooks/whatsapp

WhatsApp webhook verification (Meta challenge handshake).

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `hub.mode` | string | Must be `"subscribe"` |
| `hub.challenge` | int | Challenge value to echo back |
| `hub.verify_token` | string | Must match `WHATSAPP_VERIFY_TOKEN` |

**Response (200):** Returns the challenge integer.
**Response (403):** Token mismatch.

---

### POST /webhooks/whatsapp

Receive incoming WhatsApp messages and approval responses.

**Headers:**
| Header | Description |
|--------|-------------|
| `X-Hub-Signature-256` | HMAC-SHA256 signature: `sha256=<hex_digest>` |

**Request Body:** Meta webhook payload (see [Meta documentation](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components))

**Processing Logic:**
1. Verify HMAC-SHA256 signature (if `WHATSAPP_APP_SECRET` is set)
2. Check sender phone against allowlist
3. Parse message text
4. Match against approval patterns:
   - `APPROVE <task-id>` → resume graph with approved status
   - `REJECT <task-id>` → resume graph with rejected status
5. Otherwise → trigger `handle_whatsapp_message` as background task

**Response (200):**
```json
{"status": "ok"}
```

---

### POST /webhooks/gmail

Gmail Pub/Sub push notification receiver.

**Request Body:**
```json
{
  "message": {
    "data": "<base64-encoded JSON>",
    "messageId": "...",
    "publishTime": "..."
  },
  "subscription": "projects/.../subscriptions/..."
}
```

The base64-decoded `data` contains:
```json
{
  "emailAddress": "user@gmail.com",
  "historyId": "12345"
}
```

**Response (200):**
```json
{"status": "ok"}
```

---

### GET /portal

Serves the web portal HTML page.

**Response (200):** HTML page with agent status and capabilities overview.

### GET /

Redirects to `/portal` (307).

---

### GET /tasks/

List recent tasks.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | `20` | Maximum number of tasks to return |

**Response (200):**
```json
[
  {
    "task_id": "a6ea4138",
    "status": "deployed",
    "source": "whatsapp",
    "intent": "code_change",
    "repo_url": "https://github.com/user/repo",
    "pr_url": "https://github.com/user/repo/pull/42",
    "error": null,
    "created_at": "2026-08-01T10:30:00",
    "updated_at": "2026-08-01T10:35:00"
  }
]
```

---

### GET /tasks/{task_id}

Get task detail by ID.

**Response (200):** Single `TaskResponse` object.
**Response (404):**
```json
{"detail": "Task not found"}
```

---

## Webhook Payload Examples

### WhatsApp Incoming Message

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "WABA_ID",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {
          "display_phone_number": "15551234567",
          "phone_number_id": "PHONE_NUMBER_ID"
        },
        "messages": [{
          "from": "919643877357",
          "id": "wamid.xxx",
          "timestamp": "1690000000",
          "text": {"body": "check my emails"},
          "type": "text"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

### WhatsApp Approval Message

```json
{
  "messages": [{
    "text": {"body": "APPROVE a6ea4138"}
  }]
}
```

Pattern: `APPROVE <task-id>` or `REJECT <task-id>` (case-insensitive, task-id is optional for single pending task).

---

## Error Responses

| Status | Meaning |
|--------|---------|
| 200 | Success (webhooks always return 200 to avoid Meta retries) |
| 403 | Webhook verification failed (bad verify token or HMAC) |
| 404 | Task not found |
| 500 | Internal server error (logged, WhatsApp user notified) |
