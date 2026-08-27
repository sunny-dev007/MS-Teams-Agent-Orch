# Azure FinOps Agent

Enterprise-style **portal-less Azure + DevOps visibility** agent for Sunny’s Personal AI Agent list.

**Default: OFF** (`ENABLE_AZURE_FINOPS_AGENT=false`). Existing Dev / WhatsApp / Outlook / Boards behavior is unchanged until you enable it.

Optional **SKU mutations** are a second flag, also **OFF** by default (`ENABLE_AZURE_FINOPS_MUTATIONS=false`).

## Best name

**Azure FinOps Agent** (also responds to *cloud portal*, *azure subscriptions*, *azure costs*, *deep scan*).

## What it does

1. List **Azure subscriptions** the app identity can see  
2. After you pick a subscription:
   - Resource groups + inventory  
   - Cost summary (by RG / service)  
   - FinOps recommendations (idle / high-cost heuristics)  
   - **Deep cost scan** — high-cost alerts + SKU-mutable resources  
   - Light **Azure DevOps** org overview (projects / repo & pipeline counts via existing PAT)
3. With mutations enabled (HITL only):
   - Prepare a **SKU change plan** (quota probe, region note, impact)  
   - User must reply **`APPLY PLAN <id>`** to execute  
   - **`REJECT PLAN`** cancels — nothing is changed  
   - **Delete/remove is refused** unless `ENABLE_AZURE_FINOPS_ALLOW_DELETE=true` (keep false)

## Try prompts

- `azure subscriptions`
- `cloud portal`
- `azure costs`
- `finops recommendations`
- `deep scan` / `scan my azure`
- `devops overview`

SKU HITL (mutations ON):

- `downgrade 1 to B1` (after deep scan)
- `change sku <plan-name> to F1`
- `APPLY PLAN sku-xxxxxxxx`
- `REJECT PLAN`

Then reply with a **number** (or name) for subscription / menu / resource group.

## Isolation / safety

| Guarantee | How |
|-----------|-----|
| No impact when OFF | Early `skipped` return; graph node still registered |
| Mutations OFF by default | Separate flag; no Azure writes until APPLY PLAN |
| No coding-gate theft | Planner + channel fabric bypass; use **APPLY PLAN** / **REJECT PLAN** (not PROCEED/APPROVE) |
| Mid-flow word commands | `costs` / `recommendations` / `menu` keep FinOps awaiting (do not reset to subscription picker) |
| Cost empty vs failure | API errors are labeled separately from true zero-spend / no rows |
| Cost-first tips | Recommendations rank real spend drivers above free F1/Y1 hygiene noise |
| No secrets in git | ARM SP / Graph app secret via App Settings / `.env` only |
| No silent deletes | Delete/remove blocked unless allow-delete flag |
| Region | SKU resize plans state region does **not** move |

## Credentials

Prefer a dedicated app registration with:

- Azure RBAC: **Reader** on subscriptions (or management group)  
- Azure RBAC: **Cost Management Reader** (or Cost Management Contributor read)  
- For mutations: **Contributor** (or narrow write on App Service plans / SQL) on target subscriptions  
- Optional: reuse `MS_GRAPH_TENANT_ID` / `MS_GRAPH_CLIENT_ID` / `MS_GRAPH_CLIENT_SECRET` if that same app is granted Azure RBAC

Optional overrides:

```bash
ENABLE_AZURE_FINOPS_AGENT=true
ENABLE_AZURE_FINOPS_MUTATIONS=false
ENABLE_AZURE_FINOPS_ALLOW_DELETE=false
AZURE_FINOPS_HIGH_COST_SHARE=0.25
AZURE_FINOPS_HIGH_COST_ABS=50
AZURE_ARM_TENANT_ID=
AZURE_ARM_CLIENT_ID=
AZURE_ARM_CLIENT_SECRET=
AZURE_FINOPS_COST_DAYS=30
AZURE_FINOPS_TOP_N=15
```

AzDO overview uses existing `AZDO_ORG_URL` + `AZDO_PAT` (same as Boards/Dev). If unset, inventory/cost still work; DevOps overview reports “not configured”.

## App Service enable checklist

1. Deploy code with FinOps + mutations flags still `false`  
2. Create/assign Reader + Cost Management Reader to the app  
3. Set `ENABLE_AZURE_FINOPS_AGENT=true` (+ ARM or Graph secrets)  
4. Restart App Service; verify health fabric  
5. Teams/WhatsApp: `azure subscriptions` → pick sub → costs / recommendations / deep scan  
6. Only when ready for writes: grant Contributor (or scoped write), set `ENABLE_AZURE_FINOPS_MUTATIONS=true`, restart  
7. Always: plan → **APPLY PLAN \<id\>** — never auto-apply  

## Health

Copilot `/health` fabric includes:

- `azure_finops_agent_enabled`
- `azure_finops_arm_configured`
- `azure_finops_mutations_enabled`
- `azure_finops_allow_delete`

## Prompt cheat sheet

Full prompt catalog (entry, menu, NL mid-flow, cost vs failure, recipes, flags):

→ [`docs/AZURE_FINOPS_PROMPTS.md`](AZURE_FINOPS_PROMPTS.md)
