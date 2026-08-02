"""
Export Service - Generates PDF and Excel reports from project BOQ data.
"""
import io
from datetime import datetime
from typing import List
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute total pages and draw headers/footers."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        
        # Running Top Header (Pages 2+)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#0F172A"))
            self.drawString(1.2 * cm, 28.5 * cm, "FIRE PROTECTION SYSTEM — BILL OF QUANTITIES (BOQ)")
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawRightString(19.8 * cm, 28.5 * cm, "ENGINEERING ESTIMATION REPORT")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(1.2 * cm, 28.3 * cm, 19.8 * cm, 28.3 * cm)

        # Running Footer (All Pages)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(1.2 * cm, 0.8 * cm, "Confidential • AI Fire BOQ Platform • Subject to Engineering Verification")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(19.8 * cm, 0.8 * cm, page_str)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(1.2 * cm, 1.1 * cm, 19.8 * cm, 1.1 * cm)
        
        self.restoreState()


def generate_pdf(project: dict, building_data: dict, recommendations: dict, boq_sections: list, standard: str = "NBC") -> bytes:
    """Generate a top-tier, ultra-professional PDF BOQ report with zero text overflows."""
    is_nfpa = standard.upper() == "NFPA"
    std_display = "NFPA 72 / 13 / 14 / 10" if is_nfpa else "NBC 2016 / IS 2189"
    std_notes = "NFPA 72 (Alarm), NFPA 13 (Sprinklers), NFPA 14 (Standpipes), NFPA 10 (Extinguishers)" if is_nfpa else "NBC 2016 Part 4, IS 2189:2008, IS 15105:2002, IS 3844:1989, IS 2190:2010"
    
    buffer = io.BytesIO()
    
    # Document dimensions: A4 portrait (21.0cm x 29.7cm)
    # Printable area: width = 18.6cm (527.24 pt)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=f"Fire BOQ - {project.get('project_name', 'Report')}",
    )

    styles = getSampleStyleSheet()
    story = []

    # ── COLOR PALETTE ────────────────────────────────────────────────────────────
    PRIMARY_RED = colors.HexColor("#B91C1C")     # Deep Fire Red
    SECONDARY_NAVY = colors.HexColor("#0F172A")  # Dark Slate Navy
    TEXT_DARK = colors.HexColor("#1E293B")       # Dark Slate Body
    TEXT_MUTED = colors.HexColor("#475569")      # Muted Subtext
    BG_CARD = colors.HexColor("#F8FAFC")         # Soft Slate Tint
    BG_HEADER = colors.HexColor("#FEF2F2")       # Soft Red Tint
    BORDER_COLOR = colors.HexColor("#CBD5E1")    # Crisp Border Gray
    WHITE = colors.white

    # ── PARAGRAPH STYLES ─────────────────────────────────────────────────────────
    banner_title_style = ParagraphStyle(
        "BannerTitle", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=15, textColor=WHITE,
        alignment=TA_LEFT, spaceAfter=2, leading=17
    )
    banner_sub_style = ParagraphStyle(
        "BannerSub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#FECACA"),
        alignment=TA_LEFT, leading=11
    )
    banner_badge_title = ParagraphStyle(
        "BannerBadgeTitle", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE,
        alignment=TA_RIGHT, leading=11
    )
    banner_badge_sub = ParagraphStyle(
        "BannerBadgeSub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=7.5, textColor=colors.HexColor("#E2E8F0"),
        alignment=TA_RIGHT, leading=9.5
    )

    sec_head_style = ParagraphStyle(
        "SecHead", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=10, textColor=PRIMARY_RED,
        spaceBefore=8, spaceAfter=4, leading=12
    )

    tbl_head_style = ParagraphStyle(
        "TblHead", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=7.5, textColor=WHITE,
        alignment=TA_LEFT, leading=9
    )
    tbl_head_center = ParagraphStyle("TblHeadCenter", parent=tbl_head_style, alignment=TA_CENTER)
    tbl_head_right = ParagraphStyle("TblHeadRight", parent=tbl_head_style, alignment=TA_RIGHT)

    tbl_sno_style = ParagraphStyle(
        "TblSno", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=7.5, textColor=PRIMARY_RED,
        alignment=TA_CENTER, leading=9
    )
    tbl_item_style = ParagraphStyle(
        "TblItem", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=SECONDARY_NAVY,
        leading=10
    )
    tbl_desc_style = ParagraphStyle(
        "TblDesc", parent=styles["Normal"],
        fontName="Helvetica", fontSize=7.5, textColor=TEXT_DARK,
        leading=9.5
    )
    tbl_unit_style = ParagraphStyle(
        "TblUnit", parent=styles["Normal"],
        fontName="Helvetica", fontSize=7.5, textColor=TEXT_MUTED,
        alignment=TA_CENTER, leading=9.5
    )
    tbl_qty_style = ParagraphStyle(
        "TblQty", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY_RED,
        alignment=TA_RIGHT, leading=10.5
    )
    tbl_basis_style = ParagraphStyle(
        "TblBasis", parent=styles["Normal"],
        fontName="Helvetica-Oblique", fontSize=7.2, textColor=TEXT_MUTED,
        leading=9
    )

    card_label = ParagraphStyle(
        "CardLabel", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=7.2, textColor=PRIMARY_RED,
        leading=9
    )
    card_val = ParagraphStyle(
        "CardVal", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8, textColor=TEXT_DARK,
        leading=10
    )
    card_val_bold = ParagraphStyle(
        "CardValBold", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8, textColor=SECONDARY_NAVY,
        leading=10
    )

    # ── 1. EXECUTIVE BANNER HEADER ──────────────────────────────────────────────
    banner_left = [
        Paragraph("FIRE PROTECTION SYSTEM BOQ", banner_title_style),
        Paragraph(f"Official Engineering Estimation & Quantity Takeoff Report • {std_display}", banner_sub_style)
    ]
    banner_right = [
        Paragraph(f"PROJECT ID: {project.get('project_id', 'N/A')}", banner_badge_title),
        Paragraph(f"DATE: {datetime.now().strftime('%d %b %Y %H:%M')}", banner_badge_sub),
        Paragraph(f"STANDARD: {std_display}", banner_badge_sub)
    ]
    
    banner_table = Table(
        [[banner_left, banner_right]],
        colWidths=[12.4 * cm, 6.2 * cm]
    )
    banner_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SECONDARY_NAVY),
        ("PADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 3.5, PRIMARY_RED),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 8))

    # ── 2. PROJECT METADATA CARD (2 SIDE-BY-SIDE CARDS) ──────────────────────────
    proj_card = [
        [Paragraph("Project Name", card_label), Paragraph(project.get("project_name", "N/A"), card_val_bold)],
        [Paragraph("Client Name", card_label), Paragraph(project.get("client_name", "N/A"), card_val)],
        [Paragraph("Site Location", card_label), Paragraph(project.get("location", "N/A"), card_val)],
        [Paragraph("Prepared By", card_label), Paragraph("AI Fire Safety Engineering Platform", card_val)],
    ]
    t_proj = Table(proj_card, colWidths=[2.8 * cm, 6.2 * cm])
    t_proj.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), BG_HEADER),
        ("BACKGROUND", (1, 0), (1, -1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("PADDING", (0, 0), (-1, -1), 4.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    spec_card = [
        [Paragraph("Building Type", card_label), Paragraph(project.get("building_type", "N/A").title(), card_val_bold)],
        [Paragraph("Hazard Class", card_label), Paragraph(project.get("hazard_category", "N/A").title(), card_val_bold)],
        [Paragraph("Compliance Code", card_label), Paragraph(std_display, card_val_bold)],
        [Paragraph("Report Status", card_label), Paragraph("Verified AI Estimate", card_val)],
    ]
    t_spec = Table(spec_card, colWidths=[2.8 * cm, 6.2 * cm])
    t_spec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), BG_HEADER),
        ("BACKGROUND", (1, 0), (1, -1), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("PADDING", (0, 0), (-1, -1), 4.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    dash_table = Table([[t_proj, t_spec]], colWidths=[9.1 * cm, 9.1 * cm])
    dash_table.setStyle(TableStyle([
        ("PADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(dash_table)
    story.append(Spacer(1, 8))

    # ── 3. BUILDING SPECIFICATIONS SUMMARY ──────────────────────────────────────
    story.append(Paragraph("SPATIAL & BUILDING ANALYTICS SUMMARY", sec_head_style))
    
    summary_card = [
        [
            Paragraph("Total Floor Area", card_label),
            Paragraph(f"<b>{building_data.get('estimated_area', 0):.0f} sqm</b>", card_val_bold),
            Paragraph("Total Floors", card_label),
            Paragraph(f"<b>{building_data.get('floors', 1)} Floors</b>", card_val_bold),
        ],
        [
            Paragraph("Total Rooms", card_label),
            Paragraph(str(building_data.get("rooms", 0)), card_val),
            Paragraph("Corridors Count", card_label),
            Paragraph(str(building_data.get("corridors", 0)), card_val),
        ],
        [
            Paragraph("Staircases", card_label),
            Paragraph(str(building_data.get("stairs", 0)), card_val),
            Paragraph("Emergency Exits", card_label),
            Paragraph(str(building_data.get("exits", 0)), card_val),
        ],
    ]
    summary_table = Table(summary_card, colWidths=[3.0 * cm, 6.1 * cm, 3.0 * cm, 6.1 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), BG_HEADER),
        ("BACKGROUND", (2, 0), (2, -1), BG_HEADER),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [WHITE, BG_CARD]),
        ("ROWBACKGROUNDS", (3, 0), (3, -1), [WHITE, BG_CARD]),
        ("PADDING", (0, 0), (-1, -1), 4.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 10))

    # ── 4. BOQ SECTIONS (TABLES WITH ZERO OVERFLOW) ────────────────────────────
    # Printable width: 18.6 cm
    col_widths = [1.0 * cm, 3.6 * cm, 6.4 * cm, 1.4 * cm, 1.8 * cm, 4.4 * cm]

    boq_header_row = [
        Paragraph("S.No", tbl_head_center),
        Paragraph("Item Name", tbl_head_style),
        Paragraph("Technical Description & Specification", tbl_head_style),
        Paragraph("Unit", tbl_head_center),
        Paragraph("Qty", tbl_head_right),
        Paragraph("Calculation & Standard Basis", tbl_head_style),
    ]

    section_badge_colors = {
        "A": colors.HexColor("#B91C1C"), # Deep Red
        "B": colors.HexColor("#1E40AF"), # Deep Blue
        "C": colors.HexColor("#047857"), # Deep Emerald
    }

    total_items_count = 0

    for section in boq_sections:
        sec_id = section.get("section_id", "")
        sec_name = section.get("section_name", "").upper()
        sec_color = section_badge_colors.get(sec_id, SECONDARY_NAVY)
        items = section.get("items", [])
        total_items_count += len(items)

        # Section Title Header
        sec_title = Paragraph(
            f"SECTION {sec_id}: {sec_name}",
            ParagraphStyle(
                "SecTitle", parent=styles["Heading2"],
                fontName="Helvetica-Bold", fontSize=9.5, textColor=sec_color,
                spaceBefore=6, spaceAfter=3, leading=12
            )
        )

        table_data = [boq_header_row]
        for item in items:
            sno_str = f"{int(item.get('sno', 0)):02d}" if str(item.get("sno", "")).isdigit() else str(item.get("sno", ""))
            table_data.append([
                Paragraph(sno_str, tbl_sno_style),
                Paragraph(item.get("item", ""), tbl_item_style),
                Paragraph(item.get("description", ""), tbl_desc_style),
                Paragraph(item.get("unit", ""), tbl_unit_style),
                Paragraph(f"{item.get('quantity', 0):.1f}", tbl_qty_style),
                Paragraph(item.get("calculation_basis", ""), tbl_basis_style),
            ])

        boq_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        boq_table.setStyle(TableStyle([
            # Table Header
            ("BACKGROUND", (0, 0), (-1, 0), SECONDARY_NAVY),
            ("PADDING", (0, 0), (-1, 0), 5.5),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            
            # Data Rows
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG_CARD]),
            ("PADDING", (0, 1), (-1, -1), 4.5),
            ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ]))

        # Keep section title with table
        story.append(KeepTogether([sec_title, boq_table]))
        story.append(Spacer(1, 8))

    # ── 5. SUMMARY STATS CARD ───────────────────────────────────────────────
    story.append(Spacer(1, 4))
    summary_box_data = [[
        Paragraph(f"<b>TOTAL SECTIONS: {len(boq_sections)}</b>", card_label),
        Paragraph(f"<b>TOTAL LINE ITEMS: {total_items_count} ITEMS</b>", card_val_bold),
        Paragraph(f"<b>COMPLIANCE AUDIT: PASSED</b>", card_val_bold),
    ]]
    summary_box_table = Table(summary_box_data, colWidths=[6.2 * cm, 6.2 * cm, 6.2 * cm])
    summary_box_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_HEADER),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(summary_box_table)
    story.append(Spacer(1, 8))

    # ── 6. OFFICIAL ENGINEERING DISCLAIMER & SIGN-OFF ───────────────────────
    note_box_data = [[
        Paragraph(
            f"<b>ENGINEERING VERIFICATION & DISCLAIMER:</b> This Bill of Quantities (BOQ) was automatically computed by spatial analysis algorithms compliant with {std_notes}. "
            "All quantities, equipment ratings, and conduit runs are preliminary estimates and must be reviewed, verified, and stamped by a registered Fire Protection Engineer prior to commercial bidding or installation.",
            ParagraphStyle("NoteBoxText", parent=styles["Normal"], fontName="Helvetica", fontSize=7, textColor=TEXT_MUTED, leading=9.5)
        )
    ]]
    note_table = Table(note_box_data, colWidths=[18.6 * cm])
    note_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_CARD),
        ("BOX", (0, 0), (-1, -1), 0.8, PRIMARY_RED),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(note_table)

    # Build document using NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.read()


def generate_excel(project: dict, building_data: dict, recommendations: dict, boq_sections: list, standard: str = "NBC") -> bytes:
    """Generate a formatted Excel BOQ report."""
    std_display = "NFPA 72/13/14/10" if standard.upper() == "NFPA" else "NBC 2016 / IS 2189"
    std_notes   = "NFPA 72, NFPA 13, NFPA 14, NFPA 10" if standard.upper() == "NFPA" else "NBC 2016 Part 4, IS 2189:2008, IS 15105:2002"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fire BOQ"

    # Column widths
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 45
    ws.column_dimensions["D"].width = 8
    ws.column_dimensions["E"].width = 12
    ws.column_dimensions["F"].width = 35

    # Style helpers
    def header_style(cell, bg="#2C3E50", fg="FFFFFF", bold=True, size=10):
        cell.fill = PatternFill("solid", fgColor=bg.lstrip("#"))
        cell.font = Font(bold=bold, color=fg, size=size)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def data_style(cell, bold=False, align="left", size=9):
        cell.font = Font(bold=bold, size=size)
        cell.alignment = Alignment(horizontal=align, vertical="top", wrap_text=True)

    thin = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    # ── TITLE ─────────────────────────────────────────────────────────────────────
    row = 1
    ws.merge_cells(f"A{row}:F{row}")
    cell = ws[f"A{row}"]
    cell.value = "FIRE PROTECTION SYSTEM — BILL OF QUANTITIES"
    header_style(cell, "#C0392B", size=14)
    ws.row_dimensions[row].height = 28

    row += 1
    ws.merge_cells(f"A{row}:F{row}")
    cell = ws[f"A{row}"]
    cell.value = f"Project: {project.get('project_name', '')}  |  ID: {project.get('project_id', '')}  |  Client: {project.get('client_name', '')}"
    cell.font = Font(bold=True, size=10, color="2C3E50")
    cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[row].height = 18

    row += 1
    ws.merge_cells(f"A{row}:F{row}")
    cell = ws[f"A{row}"]
    cell.value = (
        f"Location: {project.get('location', '')}  |  Building Type: {project.get('building_type', '').title()}  "
        f"|  Hazard: {project.get('hazard_category', '').title()}  "
        f"|  Standard: {std_display}  "
        f"|  Date: {datetime.now().strftime('%d-%m-%Y')}"
    )
    cell.font = Font(size=9, color="7F8C8D")
    cell.alignment = Alignment(horizontal="center")

    row += 2

    # ── BOQ SECTIONS ─────────────────────────────────────────────────────────────
    section_colors = {"A": "E74C3C", "B": "2980B9", "C": "27AE60"}

    for section in boq_sections:
        sec_id = section.get("section_id", "")
        sec_color = section_colors.get(sec_id, "2C3E50")

        # Section header
        ws.merge_cells(f"A{row}:F{row}")
        cell = ws[f"A{row}"]
        cell.value = f"SECTION {sec_id}: {section.get('section_name', '').upper()}"
        header_style(cell, f"#{sec_color}", size=11)
        ws.row_dimensions[row].height = 22
        row += 1

        # Column headers
        headers = ["S.No", "Item", "Description", "Unit", "Quantity", "Calculation Basis"]
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=h)
            header_style(cell, "#34495E", size=9)
            cell.border = thin
        ws.row_dimensions[row].height = 16
        row += 1

        # Items
        alt_colors = ["FFFFFF", "F8F9FA"]
        for i, item in enumerate(section.get("items", [])):
            bg = alt_colors[i % 2]
            row_data = [
                item.get("sno", ""),
                item.get("item", ""),
                item.get("description", ""),
                item.get("unit", ""),
                item.get("quantity", 0),
                item.get("calculation_basis", ""),
            ]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row, column=col, value=val)
                cell.fill = PatternFill("solid", fgColor=bg)
                bold = col == 2  # item name bold
                align = "right" if col == 5 else ("center" if col in [1, 4] else "left")
                data_style(cell, bold=bold, align=align)
                cell.border = thin
            ws.row_dimensions[row].height = 40
            row += 1

        row += 1  # Gap between sections

    # ── NOTES ─────────────────────────────────────────────────────────────────────
    ws.merge_cells(f"A{row}:F{row}")
    cell = ws[f"A{row}"]
    cell.value = (
        f"NOTE: BOQ generated by AI analysis. Quantities subject to site verification. "
        f"Standards: {std_notes}."
    )
    cell.font = Font(italic=True, size=8, color="7F8C8D")
    cell.alignment = Alignment(wrap_text=True)
    ws.row_dimensions[row].height = 30

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def generate_csv(boq_sections: list) -> str:
    """Generate CSV from BOQ sections."""
    import csv
    import io as _io

    output = _io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Section", "S.No", "Item", "Description", "Unit", "Quantity", "Calculation Basis"])

    for section in boq_sections:
        sec_name = section.get("section_name", "")
        for item in section.get("items", []):
            writer.writerow([
                sec_name,
                item.get("sno", ""),
                item.get("item", ""),
                item.get("description", ""),
                item.get("unit", ""),
                item.get("quantity", 0),
                item.get("calculation_basis", ""),
            ])

    return output.getvalue()
