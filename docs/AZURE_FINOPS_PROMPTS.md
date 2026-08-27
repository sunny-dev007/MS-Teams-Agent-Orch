# Azure FinOps Agent — Prompt & Capability Guide

Use this with WhatsApp or Teams after `ENABLE_AZURE_FINOPS_AGENT=true`.  
Other agents (Dev, Outlook, Boards, Docs) stay unchanged when you use these phrases.

**SKU writes** need a second flag: `ENABLE_AZURE_FINOPS_MUTATIONS=true` (keep **false** unless you intentionally enable HITL apply).

---

## 1. What this agent is for

Personal **Azure FinOps / cloud cost** console without opening the Azure Portal:

| Capability | Needs Cost Mgmt API? | Needs write RBAC? |
|------------|----------------------|-------------------|
| List subscriptions | No | No (Reader) |
| Resource groups + inventory | No | No (Reader) |
| Cost summary (by RG / service) | Yes | No (Cost Management Reader) |
| FinOps recommendations (cost-first) | Yes (preferred) | No |
| Deep cost scan + SKU-mutable list | Yes | No to scan; Contributor for APPLY |
| Azure DevOps overview | No (uses AzDO PAT) | No |
| SKU resize plan → APPLY PLAN | Yes (quota probe) | Contributor / scoped write |

---

## 2. Start / open the portal-less console

| Prompt | What happens |
|--------|----------------|
| `azure subscriptions` | Lists subscriptions → pick by number or name |
| `azure subscription?` | Same entry |
| `cloud portal` | Same entry |
| `azure portal` | Same entry |
| `show my azure subscriptions` | Same entry |
| `list azure subscriptions` | Same entry |
| `finops` | Routes into FinOps (then pick sub if needed) |

**After the list:** reply `1` / `2` **or** the subscription name (e.g. `Pay-As-You-Go`).

---

## 3. Action menu (after a subscription is selected)

| Prompt | What happens |
|--------|----------------|
| `1` or `resource groups` / `rgs` / `inventory` | RG list (+ cost when available) |
| `2` or `costs` / `azure costs` / `cost summary` / `spending` | Cost by RG **and** by service (default window) |
| `3` or `recommendations` / `finops recommendations` / `cost saving` | Cost-first idle / high-spend tips |
| `4` or `devops overview` / `azdo overview` | AzDO projects + repo/pipeline counts |
| `5` or `deep scan` / `scan my azure` / `high cost alert` | High-cost alerts + SKU-mutable resources |
| `6` or `back` / `change sub` / `subscriptions` | Return to subscription picker |
| `menu` / `help` / `what can i do` | Re-show this menu (**stays** on current sub) |

### Natural language that also works mid-flow

These must **not** reset you to the subscription picker (fixed regression):

- `costs`, `show costs`, `how much am I spending`
- `recommendations`, `what should I optimize`, `idle resources`
- `resource groups`, `list my resources`
- `deep scan`, `expensive resources`
- `devops overview`

---

## 4. Cost prompts (direct or mid-flow)

| Prompt | What happens |
|--------|----------------|
| `azure costs` | Cost summary for current (or after you pick) subscription |
| `cost summary` / `cost breakdown` / `cost analysis` | Same |
| `billing summary` / `spending` | Same |
| `how much am I spend` | Same (NL) |
| `what is my azure bill` | Same |

**Empty vs failure (important):**

- **Query failed** → Cost Management API error / timeout / RBAC — **not** the same as ₹0 spend. Retry.
- **No rows** → true zero in-window, or costs not published yet (often 8–24h lag). If Portal shows spend, retry.

---

## 5. Recommendations / savings

| Prompt | What happens |
|--------|----------------|
| `finops recommendations` / `recommendations` | Ranked tips |
| `idle resources` / `waste` | Same path |
| `cost saving` / `optimize costs` / `what should I cut` | Same |
| `expensive resources` / `high cost` | Prefer deep scan or recommendations |

**How tips are ranked (smart behavior):**

1. **High-cost RGs / resources** from Cost Management first (e.g. `feapp` at large % of bill → HIGH).  
2. Paid App Service plans with real spend → HIGH/MEDIUM.  
3. Free / consumption SKUs (`F1`, `Y1`, …) with ~0 spend → **info** hygiene only (not bill drivers).  
4. Public IPs / disks / VMs — severity follows whether they carry spend.

Treat recommendations as **advisory**. For SKU changes use deep scan → plan → `APPLY PLAN <id>` when mutations are enabled.

---

## 6. Inventory drill-down

| Prompt | What happens |
|--------|----------------|
| `resource groups` / `inventory` | RG list for current subscription |
| `2` *(while RG list is showing)* | Open resources inside that RG |
| *(RG name)* | Same as picking that group by name |
| `menu` | Back to FinOps action menu for this subscription |

---

## 7. Deep scan + SKU HITL (mutations)

### Scan (read-only even if mutations OFF)

| Prompt | What happens |
|--------|----------------|
| `deep scan` / `scan my azure` / `scan my resources` | High-cost alerts + list of SKU-mutable plans/DBs |
| `sudden cost` / `high cost alert` | Same family |



### What you see when mutations are OFF vs ON

**OFF** (`ENABLE_AZURE_FINOPS_MUTATIONS=false` — current safe default):

- Deep scan lists SKU-mutable resources and costs (read-only).
- Menu says *SKU resize is OFF*.
- `downgrade 1 to B1` returns a clear disabled message (no Azure writes).

**ON** (`ENABLE_AZURE_FINOPS_MUTATIONS=true` + Contributor/write RBAC):

1. Deep scan (same inventory/cost tables).
2. You: `downgrade 1 to B1` (or `change sku <name> to F1`).
3. Agent replies with a **change plan** (`sku-…`) — quota notes, region unchanged, **nothing applied yet**.
4. You: `APPLY PLAN sku-xxxxxxxx` → executes the SKU change once.
5. Or: `REJECT PLAN` → cancel; Azure unchanged.

Deletes stay blocked unless `ENABLE_AZURE_FINOPS_ALLOW_DELETE=true` (keep false).

### Teams / Copilot note (dense multi-bubbles)

The FinOps API returns **one** structured reply per turn. Extra commentary under `---`, “Defect #…”, or auto “Running 5 now” usually comes from the **Copilot Studio / Teams wrapper LLM**, not the FinOps agent. Prefer short prompts (`2`, `costs`, `deep scan`) and ignore wrapper meta-text.

### Resize plan (needs `ENABLE_AZURE_FINOPS_MUTATIONS=true`)

| Prompt | What happens |
|--------|----------------|
| `downgrade 1 to B1` | Plan for mutable item #1 from last scan |
| `change sku <name> to F1` | Plan for that App Service plan / SQL DB |
| `upgrade 1 to S1` | Same, upgrade direction |
| `APPLY PLAN sku-xxxxxxxx` | **Only** this executes the pending plan |
| `REJECT PLAN` | Cancel — nothing changes |
| `delete this resource` | **Refused** (unless allow-delete flag — keep off) |

Plans include quota probe notes and state that **region does not move**.

Do **not** use coding gates (`PROCEED` / `APPROVE`) for FinOps — those are for the Dev agent.

---

## 8. Azure DevOps (light overview — not coding gates)

| Prompt | What happens |
|--------|----------------|
| `devops overview` / `azdo overview` | Org + projects + repo/pipeline counts |
| `azure devops overview` / `list azdo projects` | Same |

Still use classic paths for coding:

- `check my repos` — Dev Agent  
- `my work items` — Boards Agent  

---

## 9. Recommended recipes

### Morning cost check

```
azure subscriptions
```
→ pick Pay-As-You-Go (`2` or name) →

```
costs
```
→ then

```
recommendations
```

### Find waste before a bill spike

```
cloud portal
```
→ pick sub →

```
deep scan
```
→ optional:

```
resource groups
```

### Inventory empty / zero-cost RGs

```
azure subscriptions
```
→ pick sub → `1` or `resource groups`

### SKU downgrade (mutations ON only)

```
deep scan
```
→ `downgrade 1 to B1` → review plan →

```
APPLY PLAN sku-xxxxxxxx
```

or

```
REJECT PLAN
```

---


## 10a. Multi-agent orchestration (enterprise, additive)

| Prompt | Behavior |
|--------|----------|
| `status` | Shows coding/FinOps gate **plus** running / queued agents + bubble buffer |
| Same agent while busy | “Please wait…” + soft-queue; **other** agents still allowed |
| Other agent while FinOps busy | Allowed (parallel different agents) |
| Context buffer | Last ~20 bubbles kept (`AGENT_RECENT_BUBBLES`) |

Flags (safe defaults): `ENABLE_AGENT_RUN_ORCHESTRATION=false`, `AGENT_RECENT_BUBBLES=20`.
Opt-in via App Setting after merge so existing UX is unchanged until you enable it.  
Does **not** change coding `PROCEED`/`APPROVE` or FinOps `APPLY PLAN`.

## 10. Multi-agent hygiene

- Prefer FinOps phrases above so coding gates (`PROCEED` / `APPROVE`) are not triggered.  
- If a coding gate is open and you need FinOps: `status` or `stop`, then `azure subscriptions`.  
- Word commands **and** numbers both work after a subscription is selected.  
- Mutations stay **off** by default — inventory/cost/recs never write Azure.

---

## 11. Quick copy-paste pack

```
azure subscriptions
```

```
costs
```

```
recommendations
```

```
resource groups
```

```
deep scan
```

```
devops overview
```

```
menu
```

```
back
```

```
APPLY PLAN sku-xxxxxxxx
```

```
REJECT PLAN
```

---

## 12. Flags (App Settings)

| Setting | Safe default | Purpose |
|---------|--------------|---------|
| `ENABLE_AZURE_FINOPS_AGENT` | `false` until ready | Master on/off |
| `ENABLE_AZURE_FINOPS_MUTATIONS` | `false` | SKU APPLY PLAN writes |
| `ENABLE_AZURE_FINOPS_ALLOW_DELETE` | `false` | Keep false |
| `AZURE_FINOPS_COST_DAYS` | `30` | Cost window |
| `AZURE_FINOPS_TOP_N` | `15` | Table size |
| `AZURE_FINOPS_HIGH_COST_SHARE` | `0.25` | Alert if resource ≥ 25% of listed spend |
| `AZURE_FINOPS_HIGH_COST_ABS` | `50` | Or absolute cost threshold |

Agent overview: [`AZURE_FINOPS_AGENT.md`](AZURE_FINOPS_AGENT.md).
