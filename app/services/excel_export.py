import io
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from app.services.pipeline import PipelineResult


# ── Style Constants ──

FONT_BODY = Font(name="Arial", size=10)
FONT_HEADER = Font(name="Arial", size=10, bold=True, color="FFFFFF")
FONT_GROUP_HEADER = Font(name="Arial", size=10, bold=True, color="1F4E79")
FONT_TITLE = Font(name="Arial", size=14, bold=True, color="1F4E79")
FONT_SUBTITLE = Font(name="Arial", size=10, color="666666")
FONT_LINK = Font(name="Arial", size=10, color="0563C1", underline="single")
FONT_ERROR = Font(name="Arial", size=10, color="CC0000")
FONT_SCORE = Font(name="Arial", size=11, bold=True)

FILL_HEADER = PatternFill("solid", fgColor="1F4E79")
FILL_LEAD_INFO = PatternFill("solid", fgColor="D6E4F0")
FILL_COMPANY = PatternFill("solid", fgColor="E2EFDA")
FILL_ROLE = PatternFill("solid", fgColor="FCE4D6")
FILL_PAIN = PatternFill("solid", fgColor="FFF2CC")
FILL_HOOK = PatternFill("solid", fgColor="E4DFEC")
FILL_OUTREACH = PatternFill("solid", fgColor="D9E2F3")
FILL_META = PatternFill("solid", fgColor="F2F2F2")
FILL_ALT_ROW = PatternFill("solid", fgColor="F8F9FA")

ALIGN_WRAP = Alignment(wrap_text=True, vertical="top")
ALIGN_CENTER = Alignment(horizontal="center", vertical="top")
ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=True)

THIN_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


# ── Column Definitions ──
# (header_label, group_fill, column_width)

COLUMNS = [
    # Lead Info
    ("Name", FILL_LEAD_INFO, 20),
    ("Company", FILL_LEAD_INFO, 20),
    ("Email", FILL_LEAD_INFO, 25),
    ("LinkedIn", FILL_LEAD_INFO, 15),
    # Company Snapshot
    ("What They Do", FILL_COMPANY, 40),
    ("Size & Stage", FILL_COMPANY, 25),
    ("Recent News", FILL_COMPANY, 45),
    # Prospect Role
    ("Likely Priorities", FILL_ROLE, 40),
    ("Key Responsibilities", FILL_ROLE, 40),
    # Pain Signals
    ("Pain Signal 1", FILL_PAIN, 40),
    ("Evidence 1", FILL_PAIN, 35),
    ("Pain Signal 2", FILL_PAIN, 40),
    ("Evidence 2", FILL_PAIN, 35),
    ("Pain Signal 3", FILL_PAIN, 40),
    ("Evidence 3", FILL_PAIN, 35),
    # Personalization
    ("Personalization Hook", FILL_HOOK, 40),
    ("Why It Matters", FILL_HOOK, 40),
    # Outreach
    ("Recommended Angle", FILL_OUTREACH, 45),
    ("Talking Points", FILL_OUTREACH, 50),
    # Meta
    ("Confidence (1-10)", FILL_META, 16),
    ("Data Gaps", FILL_META, 40),
    ("Source URL 1", FILL_META, 45),
    ("Source URL 2", FILL_META, 45),
    ("Error", FILL_META, 30),
]


def _extract_row(lead_result) -> list:
    """Extract a flat row from a LeadResult for the spreadsheet."""
    lead = lead_result.lead
    research = lead_result.research or {}
    source_urls = lead_result.source_urls or []

    company = research.get("company_snapshot", {})
    role = research.get("prospect_role", {})
    pains = research.get("pain_signals", [])
    hook = research.get("personalization_hook", {})
    outreach = research.get("outreach_angle", {})

    # Flatten pain signals (up to 3)
    pain_cells = []
    for i in range(3):
        if i < len(pains):
            pain_cells.append(pains[i].get("challenge", ""))
            pain_cells.append(pains[i].get("evidence", ""))
        else:
            pain_cells.append("")
            pain_cells.append("")

    # Join talking points with numbered list
    talking_points = outreach.get("talking_points", [])
    tp_text = "\n".join(f"{i+1}. {tp}" for i, tp in enumerate(talking_points))

    # Join data gaps
    data_gaps = research.get("data_gaps", [])
    gaps_text = "\n".join(f"• {g}" for g in data_gaps)

    return [
        # Lead Info
        lead.get("name", ""),
        lead.get("company", ""),
        lead.get("email", ""),
        lead.get("linkedin", ""),
        # Company Snapshot
        company.get("what_they_do", ""),
        company.get("size_and_stage", ""),
        company.get("recent_news", ""),
        # Prospect Role
        role.get("likely_priorities", ""),
        role.get("key_responsibilities", ""),
        # Pain Signals (flattened)
        *pain_cells,
        # Personalization
        hook.get("hook", ""),
        hook.get("why_it_matters", ""),
        # Outreach
        outreach.get("recommended_angle", ""),
        tp_text,
        # Meta
        research.get("confidence_score", ""),
        gaps_text,
        source_urls[0] if len(source_urls) > 0 else "",
        source_urls[1] if len(source_urls) > 1 else "",
        lead_result.error or "",
    ]


def export_pipeline_results(pipeline_result: PipelineResult) -> io.BytesIO:
    """Generate a well-structured Excel workbook from pipeline results."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Lead Research"

    # ── Title Row ──
    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = "LeadIntel — Prospect Research Report"
    title_cell.font = FONT_TITLE
    title_cell.alignment = Alignment(vertical="center")

    ws.merge_cells("A2:E2")
    subtitle_cell = ws["A2"]
    subtitle_cell.value = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Leads: {pipeline_result.total}  |  Processed: {pipeline_result.processed}  |  Failed: {pipeline_result.failed}  |  Duration: {pipeline_result.duration_s}s"
    subtitle_cell.font = FONT_SUBTITLE

    # ── Group Header Row (Row 4) ──
    group_spans = [
        ("Lead Info", 1, 4, FILL_LEAD_INFO),
        ("Company Snapshot", 5, 7, FILL_COMPANY),
        ("Prospect Role", 8, 9, FILL_ROLE),
        ("Pain Signals", 10, 15, FILL_PAIN),
        ("Personalization", 16, 17, FILL_HOOK),
        ("Outreach Strategy", 18, 19, FILL_OUTREACH),
        ("Meta", 20, 24, FILL_META),
    ]

    for label, start_col, end_col, fill in group_spans:
        if start_col == end_col:
            cell = ws.cell(row=4, column=start_col, value=label)
        else:
            ws.merge_cells(
                start_row=4, start_column=start_col,
                end_row=4, end_column=end_col,
            )
            cell = ws.cell(row=4, column=start_col, value=label)
        cell.font = FONT_GROUP_HEADER
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # ── Column Header Row (Row 5) ──
    for col_idx, (header, _, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=5, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = ALIGN_HEADER
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── Data Rows (starting Row 6) ──
    for row_offset, lead_result in enumerate(pipeline_result.results):
        row_num = 6 + row_offset
        row_data = _extract_row(lead_result)
        use_alt = row_offset % 2 == 1

        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_num, column=col_idx, value=value)
            cell.font = FONT_BODY
            cell.alignment = ALIGN_WRAP
            cell.border = THIN_BORDER

            if use_alt:
                cell.fill = FILL_ALT_ROW

            # Confidence score color coding
            if col_idx == 20 and isinstance(value, int):
                cell.alignment = ALIGN_CENTER
                cell.font = FONT_SCORE
                if value >= 8:
                    cell.fill = PatternFill("solid", fgColor="C6EFCE")
                elif value >= 5:
                    cell.fill = PatternFill("solid", fgColor="FFEB9C")
                else:
                    cell.fill = PatternFill("solid", fgColor="FFC7CE")

            # Source URLs as clickable hyperlinks
            if col_idx in (22, 23) and value:
                cell.hyperlink = value
                cell.font = FONT_LINK

            # LinkedIn as clickable hyperlink
            if col_idx == 4 and value:
                cell.hyperlink = value
                cell.value = "Profile"
                cell.font = FONT_LINK

            # Error column red
            if col_idx == 24 and value:
                cell.font = FONT_ERROR

    # ── Freeze Panes ──
    ws.freeze_panes = "E6"

    # ── Row height for data rows ──
    for row_num in range(6, 6 + len(pipeline_result.results)):
        ws.row_dimensions[row_num].height = 80

    ws.row_dimensions[4].height = 25
    ws.row_dimensions[5].height = 30

    # ── Auto-filter ──
    last_col = get_column_letter(len(COLUMNS))
    last_row = 5 + len(pipeline_result.results)
    ws.auto_filter.ref = f"A5:{last_col}{last_row}"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
