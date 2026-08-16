# Release Agent Fabric — Microsoft Graph setup (manual steps)

Use this when enabling the **Documentation Agent**. Keep `ENABLE_DOCS_AGENT=false` on App Service until these steps succeed.

Azure CLI is available on the owner subscription (`ai-agent-rg` / `whatsapp-ai-agent-sunny`).  
**Important:** Create the Entra app in the **same tenant** as your SharePoint / Teams users (e.g. `aienterpriselabs.com`), not only the Pay-As-You-Go personal tenant if those differ.

---

## 1. Create Entra ID app (Docs Agent)

Portal: [Microsoft Entra admin center](https://entra.microsoft.com) → **App registrations** → **New registration**

| Field | Value |
|-------|--------|
| Name | `Release-Agent-Fabric-Docs` |
| Supported account types | Single tenant |
| Redirect URI | None (client credentials) |

Or Azure CLI (run after `az login` into the **correct** tenant):

```bash
az ad app create \
  --display-name "Release-Agent-Fabric-Docs" \
  --sign-in-audience AzureADMyOrg \
  --query "{appId:appId,objectId:id}" -o json

# Create service principal
APP_ID="<appId from above>"
az ad sp create --id "$APP_ID"

# Client secret (save Value once)
az ad app credential reset --id "$APP_ID" --display-name "docs-agent" --query password -o tsv

# Tenant id
az account show --query tenantId -o tsv
```

---

## 2. API permissions (application)

Entra → App → **API permissions** → **Add** → Microsoft Graph → **Application permissions**:

| Permission | Purpose |
|------------|---------|
| `Sites.Selected` | Write only to granted SharePoint sites (preferred) |
| **or** `Sites.ReadWrite.All` | Broader (faster demo; tighten later) |

Click **Grant admin consent**.

For `Sites.Selected`, grant the site (Graph / PnP). Example with Graph after you have a token with Sites.FullControl.All temporarily, or use SharePoint admin “Site app permissions”.

Minimal Sites.Selected grant (PowerShell PnP or Graph explorer as admin):

```http
POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [
    { "application": { "id": "<APP_ID>", "displayName": "Release-Agent-Fabric-Docs" } }
  ]
}
```

Find site id:

```bash
# hostname + path → site
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://graph.microsoft.com/v1.0/sites/contoso.sharepoint.com:/sites/Engineering"
```

---

## 3. App Service settings (do **not** flip until validated)

```bash
az webapp config appsettings set \
  --resource-group ai-agent-rg \
  --name whatsapp-ai-agent-sunny \
  --settings \
    MS_GRAPH_TENANT_ID="<tenant-id>" \
    MS_GRAPH_CLIENT_ID="<app-id>" \
    MS_GRAPH_CLIENT_SECRET="<secret>" \
    MS_GRAPH_SHAREPOINT_HOSTNAME="<tenant>.sharepoint.com" \
    MS_GRAPH_SHAREPOINT_SITE_PATH="/sites/YourSite" \
    ENABLE_DOCS_AGENT=false \
    ENABLE_QA_AGENT=false \
    ENABLE_RELEASE_HANDOFF=false
```

When ready for allowlisted demo only:

```bash
az webapp config appsettings set \
  -g ai-agent-rg -n whatsapp-ai-agent-sunny \
  --settings ENABLE_DOCS_AGENT=true
```

Leave `ENABLE_QA_AGENT` and `ENABLE_RELEASE_HANDOFF` false until Phase 2–3.

---

## 4. Verify

```bash
curl -sS https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/health
# Expect fabric.docs_agent_enabled / graph_configured after deploy + settings
```

In Teams (after deploy of this code):  
`write release notes for PR 123`  

- Flag off → “Documentation Agent is installed but disabled”  
- Flag on + Graph OK → SharePoint page + URL  

---

## 5. What you must do manually (checklist)

1. [ ] Confirm which Entra tenant owns SharePoint (`az account show` / portal).  
2. [ ] Create `Release-Agent-Fabric-Docs` app + secret.  
3. [ ] Admin-consent Graph permissions.  
4. [ ] Grant site access (`Sites.Selected` or choose a demo site).  
5. [ ] Paste settings into App Service (**flags stay false** until smoke test).  
6. [ ] Reply here with tenant id + site hostname/path (not the secret) so we can finish enablement.  
7. [ ] Optional: create a second app later for QA Agent (no SharePoint write).

---

## 6. OneDrive / OneNote (later)

Same app can add:

- `Files.ReadWrite.All` or Sites-scoped drive access for OneDrive  
- `Notes.ReadWrite.All` for OneNote  

Keep SharePoint-only for the leadership demo first.
