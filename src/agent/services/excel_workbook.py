"""Transform a raw Excel/CSV workbook into a layered analytics workbook.

Feature: Data Analyst Agent (ENABLE_DATA_ANALYST_AGENT). Deterministic layout;
LLM supplies grounded insights only. Original sheets are copied, never destroyed.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

NAVY = "1B365D"
TEAL = "0F6C8C"
WHITE = "FFFFFF"
LIGHT = "F4F7FB"
CARD = "E8EEF6"
GREEN = "1F7A4D"
RED = "B42318"
AMBER = "B54708"
GRAY = "5C6773"
THIN = Border(
    left=Side(style="thin", color="D0D7E2"),
    right=Side(style="thin", color="D0D7E2"),
    top=Side(style="thin", color="D0D7E2"),
    bottom=Side(style="thin", color="D0D7E2"),
)
PII_RE = re.compile(
    r"(email|e-mail|ssn|social.?security|phone|mobile|passport|dob|date.?of.?birth|"
    r"national.?id|aadhaar|pan\b|address|ip.?address)",
    re.I,
)
MAX_PROFILE_ROWS = 4000
MAX_CHART_CATS = 12


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _header_font() -> Font:
    return Font(name="Calibri", bold=True, color=WHITE, size=11)


def _title_font(size: int = 18) -> Font:
    return Font(name="Calibri", bold=True, color=NAVY, size=size)


def infer_kind(values: list[Any]) -> str:
    nonempty = [v for v in values if v not in (None, "")]
    if not nonempty:
        return "empty"
    dates = 0
    nums = 0
    for v in nonempty[:80]:
        if isinstance(v, datetime):
            dates += 1
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            nums += 1
            continue
        s = str(v).strip()
        try:
            datetime.fromisoformat(s.replace("Z", "+00:00")[:19])
            dates += 1
            continue
        except ValueError:
            pass
        try:
            float(str(s).replace(",", "").replace("%", ""))
            nums += 1
        except ValueError:
            pass
    n = len(nonempty[:80])
    if dates >= max(3, n * 0.5):
        return "date"
    if nums >= max(3, n * 0.6):
        return "number"
    uniq = {str(v).strip().lower() for v in nonempty}
    if len(uniq) <= max(2, min(20, len(nonempty) * 0.3)):
        return "category"
    return "text"


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def profile_workbook(wb: Workbook) -> dict[str, Any]:
    sheets: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(h).strip() if h is not None else f"Column_{i+1}" for i, h in enumerate(rows[0])]
        data = [r for r in rows[1:] if any(c not in (None, "") for c in r)]
        truncated = len(data) > MAX_PROFILE_ROWS
        data = data[:MAX_PROFILE_ROWS]
        columns: list[dict[str, Any]] = []
        for idx, name in enumerate(headers):
            col_vals = [r[idx] if idx < len(r) else None for r in data]
            kind = infer_kind(col_vals)
            missing = sum(1 for v in col_vals if v in (None, ""))
            nums = [_to_float(v) for v in col_vals]
            nums_ok = [n for n in nums if n is not None]
            sample = [col_vals[i] for i in range(min(5, len(col_vals)))]
            col: dict[str, Any] = {
                "name": name,
                "index": idx,
                "kind": kind,
                "missing": missing,
                "distinct": len({str(v).strip().lower() for v in col_vals if v not in (None, "")}),
                "sample": [str(s) if s is not None else "" for s in sample],
                "possibly_pii": bool(PII_RE.search(name)),
            }
            if nums_ok:
                col["min"] = min(nums_ok)
                col["max"] = max(nums_ok)
                col["avg"] = sum(nums_ok) / len(nums_ok)
                col["sum"] = sum(nums_ok)
                col["count"] = len(nums_ok)
            columns.append(col)
        sheets.append(
            {
                "name": ws.title,
                "rows": len(data),
                "truncated": truncated,
                "columns": columns,
                "duplicates": max(0, len(data) - len({tuple(r) for r in data})),
            }
        )
    primary = max(sheets, key=lambda s: s["rows"]) if sheets else None
    return {
        "sheet_count": len(wb.worksheets),
        "sheets": sheets,
        "primary": primary["name"] if primary else "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _style_header_row(ws: Worksheet, row: int, cols: int, fill: str = NAVY) -> None:
    for c in range(1, cols + 1):
        cell = ws.cell(row, c)
        cell.fill = _fill(fill)
        cell.font = _header_font()
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = THIN


def _autosize(ws: Worksheet, cols: int, min_w: int = 12, max_w: int = 36) -> None:
    for c in range(1, cols + 1):
        letter = get_column_letter(c)
        width = min_w
        for row in ws.iter_rows(min_col=c, max_col=c, max_row=min(ws.max_row or 1, 40), values_only=True):
            val = row[0]
            if val is not None:
                width = max(width, min(max_w, len(str(val)) + 2))
        ws.column_dimensions[letter].width = width


def _write_nav(ws: Worksheet) -> None:
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Sunny AI · Data Analyst"
    ws["A1"].font = Font(name="Calibri", italic=True, color=GRAY, size=10)
    ws["A2"] = "README | Dashboard | Insights | Quality | Analysis | Config | Raw"
    ws["A2"].font = Font(name="Calibri", color=TEAL, size=10)


def _copy_raw_sheets(src: Workbook, dest: Workbook) -> None:
    for ws in src.worksheets:
        name = f"Raw_{ws.title}"[:31]
        n = 1
        while name in dest.sheetnames:
            name = f"Raw_{ws.title[:24]}_{n}"[:31]
            n += 1
        target = dest.create_sheet(name)
        for row in ws.iter_rows():
            for cell in row:
                t = target.cell(cell.row, cell.column, cell.value)
                if cell.has_style:
                    t.number_format = cell.number_format
        if ws.max_row and ws.max_column:
            _style_header_row(target, 1, ws.max_column, NAVY)
            _autosize(target, ws.max_column)
            target.freeze_panes = "A2"
            target.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"


def _primary_sheet(profile: dict[str, Any]) -> dict[str, Any] | None:
    name = profile.get("primary")
    for s in profile.get("sheets") or []:
        if s.get("name") == name:
            return s
    sheets = profile.get("sheets") or []
    return sheets[0] if sheets else None


def _kpi_rows(primary: dict[str, Any]) -> list[tuple[str, str, str]]:
    cards: list[tuple[str, str, str]] = []
    cards.append(("Records", str(primary.get("rows") or 0), "row count (profile)"))
    for col in primary.get("columns") or []:
        if col.get("kind") == "number" and col.get("sum") is not None:
            cards.append((f"Total {col['name']}", f"{col['sum']:,.2f}", "sum of numeric field"))
            cards.append((f"Avg {col['name']}", f"{col['avg']:,.2f}", "mean of numeric field"))
            if len(cards) >= 6:
                break
    if len(cards) < 4:
        for col in primary.get("columns") or []:
            if col.get("kind") == "category":
                cards.append((f"Distinct {col['name']}", str(col.get("distinct") or 0), "cardinality"))
            if len(cards) >= 6:
                break
    return cards[:6]


def _build_readme(ws: Worksheet, title: str, profile: dict[str, Any], insights: dict[str, Any]) -> None:
    _write_nav(ws)
    ws.merge_cells("A4:F4")
    ws["A4"] = f"Analytics application — {title}"
    ws["A4"].font = _title_font(20)
    ws.merge_cells("A5:F8")
    ws["A5"] = (
        (insights.get("business_summary") or "Dataset profiled and redesigned into layered analytics sheets.")
        + "\n\nArchitecture: README → Executive Dashboard → Insights → Data Quality → "
        "Interactive Analysis → Configuration → Raw Data (unmodified copy)."
        "\nRaw source is preserved. Dashboard numbers are derived from the profiled table."
        "\nPower Pivot / geographic maps are not created natively; use Excel Data Model refresh if needed."
    )
    ws["A5"].alignment = Alignment(wrap_text=True, vertical="top")
    ws["A10"] = "How to use"
    ws["A10"].font = Font(name="Calibri", bold=True, size=14, color=NAVY)
    steps = [
        "Open Executive_Dashboard for at-a-glance KPIs and charts.",
        "Read Insights for grounded observations only (no fabricated conclusions).",
        "Check Data_Quality before sharing externally.",
        "Use Interactive_Analysis filters (Excel Table) to drill into records.",
        "Never edit Raw_* sheets if you need an audit trail — copy first.",
    ]
    for i, line in enumerate(steps, start=11):
        ws[f"A{i}"] = f"{i - 10}. {line}"
    ws.column_dimensions["A"].width = 110
    ws.row_dimensions[5].height = 90


def _build_dashboard(ws: Worksheet, primary: dict[str, Any], src: Workbook) -> None:
    _write_nav(ws)
    ws.merge_cells("A4:H4")
    ws["A4"] = "Executive Dashboard"
    ws["A4"].font = _title_font()
    ws["A5"] = "What changed, what drives it, and where to look next — sourced from the primary table."
    ws["A5"].font = Font(name="Calibri", italic=True, color=GRAY)

    cards = _kpi_rows(primary)
    for i, (name, value, hint) in enumerate(cards):
        col = 1 + (i % 3) * 3
        row = 7 + (i // 3) * 4
        ws.merge_cells(start_row=row, start_column=col, end_row=row + 2, end_column=col + 2)
        cell = ws.cell(row, col, f"{name}\n{value}")
        cell.fill = _fill(CARD)
        cell.font = Font(name="Calibri", bold=True, color=NAVY, size=13)
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.cell(row + 3, col, hint).font = Font(name="Calibri", size=8, color=GRAY)

    # Category ranking for bar chart
    cat_col = next((c for c in primary.get("columns") or [] if c.get("kind") == "category"), None)
    num_col = next((c for c in primary.get("columns") or [] if c.get("kind") == "number"), None)
    date_col = next((c for c in primary.get("columns") or [] if c.get("kind") == "date"), None)

    src_ws = src[primary["name"]] if primary.get("name") in src.sheetnames else src.worksheets[0]
    rows = list(src_ws.iter_rows(values_only=True))
    data_rows = [r for r in rows[1:] if any(c not in (None, "") for c in r)][:MAX_PROFILE_ROWS]

    start = 20
    ws.cell(start, 1, "Segment")
    ws.cell(start, 2, "Value")
    _style_header_row(ws, start, 2, TEAL)
    if cat_col and num_col:
        idx_c, idx_n = cat_col["index"], num_col["index"]
        agg: dict[str, float] = {}
        for r in data_rows:
            key = str(r[idx_c] if idx_c < len(r) and r[idx_c] not in (None, "") else "Unknown")
            val = _to_float(r[idx_n] if idx_n < len(r) else None) or 0.0
            agg[key] = agg.get(key, 0.0) + val
        ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CHART_CATS]
        for i, (k, v) in enumerate(ranked, start=1):
            ws.cell(start + i, 1, k)
            ws.cell(start + i, 2, v)
        last = start + len(ranked)
        chart = BarChart()
        chart.type = "col"
        chart.title = f"{num_col['name']} by {cat_col['name']}"
        chart.y_axis.title = num_col["name"]
        data = Reference(ws, min_col=2, min_row=start, max_row=last)
        cats = Reference(ws, min_col=1, min_row=start + 1, max_row=last)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.shape = 4
        chart.dataLabels = DataLabelList()
        chart.dataLabels.showVal = False
        chart.width = 15
        chart.height = 8
        ws.add_chart(chart, "E20")
    elif date_col and num_col:
        idx_d, idx_n = date_col["index"], num_col["index"]
        series: list[tuple[Any, float]] = []
        for r in data_rows:
            d = r[idx_d] if idx_d < len(r) else None
            v = _to_float(r[idx_n] if idx_n < len(r) else None)
            if d not in (None, "") and v is not None:
                series.append((d, v))
        series = series[:MAX_CHART_CATS * 4]
        for i, (d, v) in enumerate(series, start=1):
            ws.cell(start + i, 1, d)
            ws.cell(start + i, 2, v)
        last = start + len(series)
        chart = LineChart()
        chart.title = f"{num_col['name']} over time"
        data = Reference(ws, min_col=2, min_row=start, max_row=last)
        cats = Reference(ws, min_col=1, min_row=start + 1, max_row=last)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.width = 15
        chart.height = 8
        ws.add_chart(chart, "E20")
    _autosize(ws, 8)


def _build_insights(ws: Worksheet, insights: dict[str, Any]) -> None:
    _write_nav(ws)
    ws.merge_cells("A4:F4")
    ws["A4"] = "AI-style Insights (grounded)"
    ws["A4"].font = _title_font()
    headers = ["Key Insight", "Driver", "Business Implication"]
    for i, h in enumerate(headers, start=1):
        ws.cell(6, i, h)
    _style_header_row(ws, 6, 3, TEAL)
    rows = insights.get("insights") or []
    if not rows:
        rows = [
            {
                "key_insight": "Insufficient structured evidence for additional claims.",
                "driver": "Profile did not support extra KPIs.",
                "business_implication": "Validate source data quality before executive distribution.",
            }
        ]
    for i, item in enumerate(rows[:8], start=7):
        ws.cell(i, 1, item.get("key_insight") or "")
        ws.cell(i, 2, item.get("driver") or "")
        ws.cell(i, 3, item.get("business_implication") or "")
        for c in range(1, 4):
            ws.cell(i, c).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(i, c).border = THIN
    flags = insights.get("quality_flags") or []
    ws.cell(17, 1, "Quality flags")
    ws["A17"].font = Font(bold=True, color=AMBER)
    ws.merge_cells("A18:C20")
    ws["A18"] = "\n".join(f"- {f}" for f in flags) or "No material flags from the analyst model."
    ws["A18"].alignment = Alignment(wrap_text=True, vertical="top")
    _autosize(ws, 3, min_w=28, max_w=48)


def _build_quality(ws: Worksheet, profile: dict[str, Any], insights: dict[str, Any]) -> None:
    _write_nav(ws)
    ws["A4"] = "Data Quality"
    ws["A4"].font = _title_font()
    headers = ["Sheet", "Column", "Kind", "Missing", "Distinct", "PII risk", "Notes"]
    for i, h in enumerate(headers, start=1):
        ws.cell(6, i, h)
    _style_header_row(ws, 6, 7, NAVY)
    r = 7
    for sheet in profile.get("sheets") or []:
        total = sheet.get("rows") or 0
        for col in sheet.get("columns") or []:
            missing = col.get("missing") or 0
            ws.cell(r, 1, sheet.get("name"))
            ws.cell(r, 2, col.get("name"))
            ws.cell(r, 3, col.get("kind"))
            ws.cell(r, 4, missing)
            ws.cell(r, 5, col.get("distinct"))
            ws.cell(r, 6, "Yes" if col.get("possibly_pii") else "No")
            note = ""
            if total and missing / max(total, 1) > 0.2:
                note = "High missing rate — flagged, not deleted"
            if col.get("possibly_pii"):
                note = (note + "; ").strip("; ") + "Potential PII — do not publish broadly"
            ws.cell(r, 7, note)
            if col.get("possibly_pii"):
                ws.cell(r, 6).font = Font(color=RED, bold=True)
            r += 1
    last = max(r - 1, 7)
    ws.conditional_formatting.add(
        f"D7:D{last}",
        ColorScaleRule(start_type="min", start_color="C6F4D6", end_type="max", end_color="F4C7C3"),
    )
    sensitive = insights.get("sensitive_fields") or []
    ws.cell(last + 2, 1, "Transformation log")
    ws.cell(last + 2, 1).font = Font(bold=True, color=NAVY)
    ws.cell(last + 3, 1, "Original sheets copied to Raw_* without deletion.")
    ws.cell(last + 4, 1, "Types inferred from sampled values; unsupported assumptions were not applied.")
    if sensitive:
        ws.cell(last + 5, 1, "Sensitive fields called out: " + ", ".join(str(s) for s in sensitive))
    _autosize(ws, 7)


def _build_analysis(ws: Worksheet, primary: dict[str, Any], src: Workbook) -> None:
    _write_nav(ws)
    ws["A4"] = "Interactive Analysis"
    ws["A4"].font = _title_font()
    ws["A5"] = "Excel Table — filter, sort, and drill to records. Totals stay on the Dashboard."
    src_ws = src[primary["name"]] if primary.get("name") in src.sheetnames else src.worksheets[0]
    rows = list(src_ws.iter_rows(values_only=True))
    if not rows:
        return
    headers = [str(h) if h is not None else f"Col{i}" for i, h in enumerate(rows[0], start=1)]
    for i, h in enumerate(headers, start=1):
        ws.cell(7, i, h)
    _style_header_row(ws, 7, len(headers), TEAL)
    body = [r for r in rows[1:] if any(c not in (None, "") for c in r)][:MAX_PROFILE_ROWS]
    for ri, row in enumerate(body, start=8):
        for ci in range(len(headers)):
            ws.cell(ri, ci + 1, row[ci] if ci < len(row) else None)
    end_row = 7 + len(body)
    if body:
        ref = f"A7:{get_column_letter(len(headers))}{end_row}"
        table = Table(displayName="AnalysisTable", ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        ws.add_table(table)
        ws.freeze_panes = "A8"
    _autosize(ws, len(headers))


def _build_config(ws: Worksheet, profile: dict[str, Any]) -> None:
    _write_nav(ws)
    ws["A4"] = "Configuration / Parameters"
    ws["A4"].font = _title_font()
    ws["A6"] = "Parameter"
    ws["B6"] = "Value"
    _style_header_row(ws, 6, 2)
    rows = [
        ("Primary sheet", profile.get("primary") or ""),
        ("Profiled at (UTC)", profile.get("generated_at") or ""),
        ("Max profile rows", str(MAX_PROFILE_ROWS)),
        ("Refresh guidance", "Replace Raw_* from source, then re-run Data Analyst Agent"),
        ("Native gaps", "Power Pivot / 3D maps — use Excel Data Model if the dataset grows"),
    ]
    for i, (k, v) in enumerate(rows, start=7):
        ws.cell(i, 1, k)
        ws.cell(i, 2, v)
    _autosize(ws, 2, min_w=24, max_w=70)


def build_analytics_workbook(
    source_bytes: bytes,
    *,
    title: str,
    insights: dict[str, Any] | None = None,
    filename: str = "workbook.xlsx",
) -> bytes:
    insights = insights or {}
    if filename.lower().endswith(".csv"):
        src = Workbook()
        ws = src.active
        ws.title = "RawCSV"
        import csv

        text = source_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for r_i, row in enumerate(reader, start=1):
            for c_i, val in enumerate(row, start=1):
                ws.cell(r_i, c_i, val)
    else:
        src = load_workbook(io.BytesIO(source_bytes), data_only=True)
    profile = profile_workbook(src)
    dest = Workbook()
    dest.remove(dest.active)
    readme = dest.create_sheet("README", 0)
    dash = dest.create_sheet("Executive_Dashboard", 1)
    ins = dest.create_sheet("Insights", 2)
    quality = dest.create_sheet("Data_Quality", 3)
    analysis = dest.create_sheet("Interactive_Analysis", 4)
    config = dest.create_sheet("Configuration", 5)
    _copy_raw_sheets(src, dest)
    primary = _primary_sheet(profile) or {"name": src.worksheets[0].title, "rows": 0, "columns": []}
    _build_readme(readme, title, profile, insights)
    _build_dashboard(dash, primary, src)
    _build_insights(ins, insights)
    _build_quality(quality, profile, insights)
    _build_analysis(analysis, primary, src)
    _build_config(config, profile)
    buf = io.BytesIO()
    dest.save(buf)
    return buf.getvalue()


def parse_insights_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", raw)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
