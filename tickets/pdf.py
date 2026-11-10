"""Generate a modern PDF boarding pass for a ticket, with QR code and watermark."""
import io
import json
import os

import qrcode
from PIL import Image as PILImage, ImageEnhance
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ─── Brand colours ──────────────────────────────────────────────────────────
BRAND_NAVY = colors.HexColor("#0b3a75")
BRAND_LIGHT = colors.HexColor("#e7eef8")
BRAND_ACCENT = colors.HexColor("#bb1a28")
BRAND_GREEN = colors.HexColor("#2e7d32")
BRAND_ORANGE = colors.HexColor("#a0111d")
DARK_TEXT = colors.HexColor("#212121")
MEDIUM_TEXT = colors.HexColor("#616161")
LIGHT_TEXT = colors.HexColor("#9e9e9e")
WHITE = colors.white
BORDER_COLOR = colors.HexColor("#bdbdbd")
ROW_ALT = colors.HexColor("#fafafa")

# Path to the logo image
LOGO_PATH = os.path.join(os.path.dirname(__file__), "ecobus-logo.png")


def _make_watermark():
    """Create a semi-transparent version of the logo for the full-page background."""
    try:
        img = PILImage.open(LOGO_PATH).convert("RGBA")
        # ~18% opacity — visible but won't overpower bold text
        alpha = img.split()[3]
        alpha = ImageEnhance.Brightness(alpha).enhance(0.18)
        img.putalpha(alpha)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        return None


def _draw_watermark(canvas, doc):
    """Draw the logo covering the full page as a background watermark."""
    from reportlab.lib.utils import ImageReader

    canvas.saveState()
    watermark_buf = _make_watermark()
    if watermark_buf:
        page_w, page_h = doc.pagesize
        # Cover the entire page
        canvas.drawImage(
            ImageReader(watermark_buf), 0, 0, width=page_w, height=page_h,
            mask="auto", preserveAspectRatio=False,
        )
    # Draw a thin crimson accent line at the very top
    canvas.setStrokeColor(colors.HexColor("#bb1a28"))
    canvas.setLineWidth(3)
    canvas.line(doc.leftMargin, doc.pagesize[1] - 5 * mm,
                doc.pagesize[0] - doc.rightMargin, doc.pagesize[1] - 5 * mm)
    canvas.restoreState()


def generate_ticket_pdf(ticket):
    """Return a BytesIO buffer containing a modern boarding-pass PDF."""
    buf = io.BytesIO()
    page_w, page_h = landscape(A5)
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A5),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=8 * mm,
    )

    booking = ticket.booking
    trip = booking.trip
    route = trip.route
    seat_num = ticket.seat.seat_number if ticket.seat else "Open"

    # ── QR Code (HMAC-signed for tamper-proof on-bus verification) ──────────
    from .signing import build_signed_qr_payload
    qr_data = build_signed_qr_payload(ticket)
    qr_img = qrcode.make(qr_data, box_size=5, border=1)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_element = Image(qr_buf, width=36 * mm, height=36 * mm)

    # ── Paragraph styles ────────────────────────────────────────────────────
    s_brand = ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=15, textColor=WHITE, alignment=TA_LEFT, leading=18)
    s_tagline = ParagraphStyle("tagline", fontName="Helvetica-Oblique", fontSize=7, textColor=colors.HexColor("#f6b8bd"), alignment=TA_LEFT, leading=9)
    s_bp = ParagraphStyle("bp", fontName="Helvetica-Bold", fontSize=10, textColor=BRAND_ACCENT, alignment=TA_RIGHT, leading=13)
    s_bp_sub = ParagraphStyle("bp_sub", fontName="Helvetica", fontSize=7, textColor=colors.HexColor("#b0bec5"), alignment=TA_RIGHT, leading=9)
    s_city_big = ParagraphStyle("city_big", fontName="Helvetica-Bold", fontSize=20, textColor=BRAND_NAVY, alignment=TA_CENTER, leading=24)
    s_city_sub = ParagraphStyle("city_sub", fontName="Helvetica", fontSize=7, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=9)
    s_arrow = ParagraphStyle("arrow", fontName="Helvetica-Bold", fontSize=14, textColor=BRAND_ACCENT, alignment=TA_CENTER, leading=18)
    s_date_center = ParagraphStyle("date_center", fontName="Helvetica-Bold", fontSize=8, textColor=BRAND_NAVY, alignment=TA_CENTER, leading=10)
    s_label = ParagraphStyle("label", fontName="Helvetica", fontSize=6, textColor=LIGHT_TEXT, alignment=TA_LEFT, leading=7, spaceBefore=0, spaceAfter=1)
    s_value = ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=9, textColor=DARK_TEXT, alignment=TA_LEFT, leading=11)
    s_value_lg = ParagraphStyle("value_lg", fontName="Helvetica-Bold", fontSize=16, textColor=BRAND_NAVY, alignment=TA_CENTER, leading=19)
    s_value_class = ParagraphStyle("value_class", fontName="Helvetica-Bold", fontSize=10, textColor=BRAND_ORANGE, alignment=TA_CENTER, leading=13)
    s_price = ParagraphStyle("price", fontName="Helvetica-Bold", fontSize=12, textColor=BRAND_GREEN, alignment=TA_LEFT, leading=15)
    s_footer = ParagraphStyle("footer", fontName="Helvetica", fontSize=5.5, textColor=colors.HexColor("#cfd8dc"), alignment=TA_CENTER, leading=7)
    s_scan = ParagraphStyle("scan", fontName="Helvetica", fontSize=6, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=8)
    s_ticket_id = ParagraphStyle("ticket_id", fontName="Courier-Bold", fontSize=7, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=9)

    elements = []
    content_w = page_w - doc.leftMargin - doc.rightMargin  # ~184mm

    # ════════════════════════════════════════════════════════════════════════
    # HEADER BAR — brand + tagline | boarding pass label
    # ════════════════════════════════════════════════════════════════════════
    header_left = [
        Paragraph("ECOBUS", s_brand),
        Paragraph("Your Comfort Zone", s_tagline),
    ]
    header_right = [
        Paragraph("BOARDING PASS", s_bp),
        Paragraph(ticket.ticket_number, s_bp_sub),
    ]
    header_data = [[header_left, header_right]]
    header = Table(header_data, colWidths=[content_w * 0.62, content_w * 0.38])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (-1, -1), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 0, 0]),
    ]))
    elements.append(header)

    # ════════════════════════════════════════════════════════════════════════
    # ROUTE SECTION — large city names with arrow + departure date
    # ════════════════════════════════════════════════════════════════════════
    origin_name = route.origin.name.upper()
    dest_name = route.destination.name.upper()
    origin_country = route.origin.country if hasattr(route.origin, "country") else ""
    dest_country = route.destination.country if hasattr(route.destination, "country") else ""
    dep_date = trip.departure_time.strftime("%A, %d %B %Y")

    from_cell = [
        Paragraph("FROM", s_city_sub),
        Paragraph(origin_name, s_city_big),
        Paragraph(str(origin_country), s_city_sub),
    ]
    center_cell = [
        Spacer(1, 2 * mm),
        Paragraph("───  ▶  ───", s_arrow),
        Paragraph(dep_date, s_date_center),
    ]
    to_cell = [
        Paragraph("TO", s_city_sub),
        Paragraph(dest_name, s_city_big),
        Paragraph(str(dest_country), s_city_sub),
    ]

    route_data = [[from_cell, center_cell, to_cell]]
    route_table = Table(route_data, colWidths=[content_w * 0.38, content_w * 0.24, content_w * 0.38], rowHeights=[24 * mm])
    route_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BRAND_NAVY),
    ]))
    elements.append(route_table)

    elements.append(Spacer(1, 2 * mm))

    # ════════════════════════════════════════════════════════════════════════
    # MAIN SECTION — Details left | Seat+QR right
    # ════════════════════════════════════════════════════════════════════════

    def _field(label, value, style_v=s_value):
        return [Paragraph(label, s_label), Paragraph(str(value), style_v)]

    dep_time = trip.departure_time.strftime("%H:%M")
    arr_time = trip.arrival_time.strftime("%H:%M")
    dep_short = trip.departure_time.strftime("%d %b %Y, %H:%M")

    status_color = BRAND_GREEN if ticket.status == "VALID" else BRAND_ACCENT
    s_status = ParagraphStyle("status", parent=s_value, textColor=status_color)

    # Left detail rows (3 rows × 3 cols)
    detail_data = [
        [
            _field("PASSENGER", f"{ticket.first_name} {ticket.last_name}"),
            _field("DEPARTURE", dep_short),
            _field("ARRIVAL", arr_time),
        ],
        [
            _field("ID / PASSPORT", ticket.id_number),
            _field("BUS", str(trip.bus)),
            _field("PHONE", str(ticket.phone_number)),
        ],
        [
            _field("BOOKING REF", booking.booking_reference),
            _field("PRICE", f"{route.currency} {ticket.price}", s_price),
            _field("STATUS", f"● {ticket.status}", s_status),
        ],
    ]

    left_w = content_w * 0.70
    detail_col = left_w / 3
    details_table = Table(detail_data, colWidths=[detail_col] * 3, rowHeights=[13 * mm] * 3)
    details_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BORDER_COLOR),
        ("LINEBELOW", (0, 1), (-1, 1), 0.4, BORDER_COLOR),
        ("BACKGROUND", (0, 1), (-1, 1), ROW_ALT),
    ]))

    # Right panel: Seat number + Class + QR
    right_w = content_w * 0.30
    right_data = [
        [[
            Paragraph("SEAT", s_label),
            Paragraph(str(seat_num), s_value_lg),
        ]],
        [[
            Paragraph(ticket.seat_class, s_value_class),
        ]],
        [[qr_element]],
        [[Paragraph("Scan QR to verify", s_scan)]],
    ]
    right_table = Table(right_data, colWidths=[right_w - 4 * mm])
    right_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("BACKGROUND", (0, 0), (0, 1), BRAND_LIGHT),
        ("ROUNDEDCORNERS", [4, 4, 0, 0]),
    ]))

    # Combine left + right
    main_data = [[details_table, right_table]]
    main_table = Table(main_data, colWidths=[left_w, right_w])
    main_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LINEBEFORE", (1, 0), (1, -1), 1, BORDER_COLOR, None, (3, 3)),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("ROUNDEDCORNERS", [0, 0, 0, 0]),
    ]))
    elements.append(main_table)

    elements.append(Spacer(1, 1 * mm))

    # ════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════════════════════════════
    footer_data = [[
        Paragraph(
            "Eco Bus Co. Ltd  ·  Juba  ·  Kampala  ·  Nairobi  ·  Bor  ·  Nimule  |  "
            "Please arrive 30 minutes before departure  ·  Carry a valid photo ID  |  "
            "Juba: 0926 388 115  ·  0921 307 724  ·  Kampala: +256 782 393 125  ·  Nairobi: +254 140 342 959  ·  ecobus.co.ss@gmail.com",
            s_footer,
        )
    ]]
    footer = Table(footer_data, colWidths=[content_w])
    footer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [0, 0, 6, 6]),
    ]))
    elements.append(footer)

    doc.build(elements, onFirstPage=_draw_watermark, onLaterPages=_draw_watermark)
    buf.seek(0)
    return buf
