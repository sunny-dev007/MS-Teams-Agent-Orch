# Data Analyst Agent

Additive specialist: transform a **raw Excel/CSV** from the Doc Library into an executive analytics workbook and publish it to SharePoint **`/Analytics`**.

**Default off.** `ENABLE_DATA_ANALYST_AGENT=false` — Dev coding, WhatsApp, Doc RAG ingest, Outlook, and Boards stay unchanged.

## Phrases

| Intent | Examples |
|---|---|
| `analyze_excel` | `convert excel 6`, `analyze spreadsheet 6`, `excel dashboard`, `data analyst` |

Typical flow:

```
list my documents
  → convert excel <n>   (xlsx / xlsm / csv from the # column)
  → SharePoint Analytics/<name>-analytics-<timestamp>.xlsx
```

The source library file is **not** modified. Original sheets are copied to `Raw_*` inside the new workbook.

## Workbook layers (adapted to the dataset)

1. README / Overview  
2. Executive Dashboard (KPI cards + charts)  
3. Insights (LLM, grounded in a profile — no invented KPIs)  
4. Data Quality (missing values, PII-name flags, transformation log)  
5. Interactive Analysis (Excel Table)  
6. Configuration  
7. Raw copies of source sheets  

Power Pivot and geographic maps are **not** generated natively (Excel Data Model remains a manual follow-up).

## App Service

```bash
az webapp config appsettings set -g ai-agent-rg -n whatsapp-ai-agent-sunny --settings \
  ENABLE_DATA_ANALYST_AGENT=false
```

Requires the same Graph site **write** permission as Docs Agent (`Sites.ReadWrite.All` or equivalent) plus library **read** to download the source file.

Set `true` only after a Teams test: `list my documents` then `convert excel <n>` on a small `.xlsx`.
