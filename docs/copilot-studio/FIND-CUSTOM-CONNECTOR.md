# Find Custom connectors in the **updated** Power Apps / Automate UI

Microsoft moved **Custom connectors** out of the old left-nav **Data** item in many tenants. If you do not see **Data → Custom connectors**, use one of the paths below.

Use environment **CopilotStudio-Dev** (same as your agent) in the top-right environment picker.

Upload file:

`docs/copilot-studio/personal-ai-agent.swagger.json`  
(Swagger **2.0** only — required for custom connectors)

---

## From your Power Automate screenshot (exact clicks)

You are currently on **Connections → New connection**. That page only lists Microsoft/third-party connectors to *connect*. It does **not** create custom connectors.

Do this:

1. In the **… More** flyout (already open in your screenshot), click **`Discover all`** at the bottom (do not stay on Connections).
2. In the Discover all search box, type: **`custom connector`** or **`Custom connectors`**.
3. Open **Custom connectors** (icon often looks like a puzzle / API plug).
4. Then: **+ New custom connector** → **Import an OpenAPI file**.

Optional: pin it — on the Custom connectors page, use **Pin to left navigation** if offered, so you do not hunt again.

### Still missing after Discover all?

Use a **Solution** instead (works even when Custom connectors is hidden):

1. Left nav → **Solutions** (visible in your screenshot).
2. Open an existing solution, or **+ New solution** (name e.g. `SunnyPersonalAI`, publisher default).
3. Inside the solution → **+ New** → **Automation** → **Connector** → **Custom**  
   (wording may be **+ New** → **Connector** → **Custom connector**).
4. Choose **Import an OpenAPI file** → upload `personal-ai-agent.swagger.json`.

### Direct URL (after any CopilotStudio-Dev page loads)

1. Look at the browser address bar for  
   `/environments/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/`
2. Open:

`https://make.powerautomate.com/environments/<paste-guid-here>/customconnectors`

or

`https://make.powerapps.com/environments/<paste-guid-here>/customconnectors`


---

## Path 2 — Power Apps (updated left menu)

1. Open **[https://make.powerapps.com](https://make.powerapps.com)**.
2. Top right: environment = **CopilotStudio-Dev**.
3. Left rail → **More** (⋯).
4. Select **Discover all** (or **Open** the full app list).
5. Search: **`Custom connectors`** → open it.  
   (It may sit under **Data** / **Dataverse** / **Connections** groups — search is reliable.)
6. **+ New custom connector** → **Import an OpenAPI file** → same file/name as above.

If the left menu shows **Tables** but not **Data**, that is the new UI — **Custom connectors is not under Tables**. Use **More → search**.

---

## Path 3 — Direct URL (skip hunting in the menu)

1. Open Power Apps and select **CopilotStudio-Dev**.
2. In the browser address bar, go to (environment id will fill after you pick env once):

   **[https://make.powerapps.com](https://make.powerapps.com)** → after the app loads, manually navigate using:

   - Click your profile / environment name once so the URL contains `/environments/<guid>/`
   - Then open:

   `https://make.powerapps.com/environments/<your-environment-guid>/customconnectors`

3. Or from Power Automate:

   `https://make.powerautomate.com/environments/<your-environment-guid>/customconnectors`

To get `<your-environment-guid>`: Power Platform Admin Center → Environments → **CopilotStudio-Dev** → copy Environment ID.

---

## Path 4 — From Copilot Studio new experience

1. Open **Sunny Personal Dev Agent**.
2. **Add a tool** → click **+ Add** (next to the search box).
3. Choose **Custom connector** / **New tool → Custom connector** if listed.  
   Studio often opens the maker portal connector wizard for you.
4. Import the same Swagger file there.

---

## After the import wizard opens — finish the connector

### 1) General
- Host should be: `whatsapp-ai-agent-sunny.azurewebsites.net`
- Base URL: `/`
- Scheme: HTTPS  
→ **Security** (or Create connector first if prompted)

### 2) Security
- Authentication type: **API Key**
- Parameter label: `Copilot API Key`  ← friendly name only
- Parameter name: must send header **`X-Copilot-Api-Key`**  
  (If the UI shows `api_key`, edit so the **Header** name is `X-Copilot-Api-Key`)
- Parameter location: **Header**

### 3) Definition
- Confirm action **PersonalAIAgent** exists (POST `/api/channels/copilot/message`).
- Validation errors: fix before save.

### 4) Create / Update connector

### 5) Test
1. Open **Test** tab → **New connection**.
2. Paste App Service value of `COPILOT_API_KEY` (not into Parameter label earlier).
3. Operation **PersonalAIAgent**:
   - `user_id` = your Entra object ID (same as allowlist)
   - `message` = `status`
   - `action` = `status`
4. **Test operation** → expect HTTP 200 and a `reply` string.

---

## Add the connector to the new Copilot agent

1. Back in Copilot Studio → **Sunny Personal Dev Agent**.
2. **Add a tool** → tab **Connectors** (not Featured).
3. Search **Sunny Personal AI Agent** or **PersonalAIAgent**.
4. Select it → connection with API key → **Add and configure**.
5. Map:
   - `message` ← user message
   - `user_id` ← signed-in user / Entra object id
   - `action` ← `message` (use `status` when user asks for status)
6. **Publish** agent → enable **Teams** channel.

---

## If Custom connectors still does not appear

| Cause | What to do |
|--------|------------|
| Wrong environment | Switch to **CopilotStudio-Dev** top-right |
| No maker rights | Ask admin for Environment Maker on that env |
| Left nav pinned only “Apps/Tables” | Use **More → search Custom connectors** or Path 3 URL |
| Policy blocks custom connectors | Use **Workflows → Agent flow → HTTP** instead (see below) |

### Workflow fallback (no custom connector menu needed)

1. Copilot Studio → **Add a tool** → **Workflows**.
2. Create an agent flow with action **HTTP**:
   - POST `https://whatsapp-ai-agent-sunny.azurewebsites.net/api/channels/copilot/message`
   - Header `X-Copilot-Api-Key` = your key
   - JSON body with `user_id`, `message`, `action`
3. Return `reply` to the agent; name the tool **PersonalAIAgent**.

---

## File to upload

Repo path on your machine:

`/Users/sunny.kushwaha/projects/Personal/My-Personal-AI-Agent/docs/copilot-studio/personal-ai-agent.swagger.json`
