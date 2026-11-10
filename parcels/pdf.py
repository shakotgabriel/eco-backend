"""Generate a PDF receipt / waybill for a parcel shipment, with QR code."""
import io
import json
import os

import qrcode
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

# Brand colours (same as ticket PDF)
BRAND_NAVY = colors.HexColor("#0b3a75")
BRAND_LIGHT = colors.HexColor("#e7eef8")
BRAND_ACCENT = colors.HexColor("#bb1a28")
BRAND_GREEN = colors.HexColor("#2e7d32")
DARK_TEXT = colors.HexColor("#212121")
MEDIUM_TEXT = colors.HexColor("#616161")
LIGHT_TEXT = colors.HexColor("#9e9e9e")
WHITE = colors.white
BORDER_COLOR = colors.HexColor("#bdbdbd")
ROW_ALT = colors.HexColor("#fafafa")


def generate_parcel_receipt(parcel):
    """Return a BytesIO buffer containing a parcel waybill PDF."""
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

    trip = parcel.trip
    route = trip.route
    content_w = page_w - doc.leftMargin - doc.rightMargin

    # QR Code with tracking number and key details
    qr_data = json.dumps({
        "tracking": parcel.tracking_number,
        "sender": parcel.sender_name,
        "receiver": parcel.receiver_name,
        "route": f"{route.origin.name} → {route.destination.name}",
        "departure": trip.departure_time.strftime("%Y-%m-%d %H:%M"),
    })
    qr_img = qrcode.make(qr_data, box_size=5, border=1)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_element = Image(qr_buf, width=34 * mm, height=34 * mm)

    # Styles
    s_brand = ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=14, textColor=WHITE, alignment=TA_LEFT, leading=17)
    s_tagline = ParagraphStyle("tagline", fontName="Helvetica-Oblique", fontSize=7, textColor=colors.HexColor("#f6b8bd"), alignment=TA_LEFT, leading=9)
    s_doc_type = ParagraphStyle("doctype", fontName="Helvetica-Bold", fontSize=10, textColor=BRAND_ACCENT, alignment=TA_RIGHT, leading=13)
    s_tracking = ParagraphStyle("tracking", fontName="Courier-Bold", fontSize=8, textColor=colors.HexColor("#b0bec5"), alignment=TA_RIGHT, leading=10)
    s_section = ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=9, textColor=BRAND_NAVY, alignment=TA_LEFT, leading=12)
    s_label = ParagraphStyle("label", fontName="Helvetica", fontSize=6, textColor=LIGHT_TEXT, alignment=TA_LEFT, leading=7, spaceBefore=0, spaceAfter=1)
    s_value = ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=9, textColor=DARK_TEXT, alignment=TA_LEFT, leading=11)
    s_price = ParagraphStyle("price", fontName="Helvetica-Bold", fontSize=11, textColor=BRAND_GREEN, alignment=TA_LEFT, leading=14)
    s_city_big = ParagraphStyle("city_big", fontName="Helvetica-Bold", fontSize=16, textColor=BRAND_NAVY, alignment=TA_CENTER, leading=20)
    s_city_sub = ParagraphStyle("city_sub", fontName="Helvetica", fontSize=7, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=9)
    s_arrow = ParagraphStyle("arrow", fontName="Helvetica-Bold", fontSize=12, textColor=BRAND_ACCENT, alignment=TA_CENTER, leading=16)
    s_footer = ParagraphStyle("footer", fontName="Helvetica", fontSize=5.5, textColor=colors.HexColor("#cfd8dc"), alignment=TA_CENTER, leading=7)
    s_scan = ParagraphStyle("scan", fontName="Helvetica", fontSize=6, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=8)

    elements = []

    # ═══ HEADER ═══
    header_left = [
        Paragraph("ECOBUS", s_brand),
        Paragraph("Your Comfort Zone", s_tagline),
    ]
    header_right = [
        Paragraph("PARCEL WAYBILL", s_doc_type),
        Paragraph(parcel.tracking_number, s_tracking),
    ]
    header = Table([[header_left, header_right]], colWidths=[content_w * 0.6, content_w * 0.4])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (0, 0), 10),
        ("RIGHTPADDING", (-1, -1), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 0, 0]),
    ]))
    elements.append(header)

    # ═══ ROUTE ═══
    origin_name = route.origin.name.upper()
    dest_name = route.destination.name.upper()
    dep_date = trip.departure_time.strftime("%A, %d %B %Y — %H:%M")

    from_cell = [Paragraph("FROM", s_city_sub), Paragraph(origin_name, s_city_big)]
    center_cell = [Spacer(1, 2 * mm), Paragraph("───  ▶  ───", s_arrow), Paragraph(dep_date, ParagraphStyle("dep", fontName="Helvetica-Bold", fontSize=7, textColor=BRAND_NAVY, alignment=TA_CENTER, leading=9))]
    to_cell = [Paragraph("TO", s_city_sub), Paragraph(dest_name, s_city_big)]

    route_table = Table([[from_cell, center_cell, to_cell]], colWidths=[content_w * 0.35, content_w * 0.30, content_w * 0.35], rowHeights=[20 * mm])
    route_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, BRAND_NAVY),
    ]))
    elements.append(route_table)
    elements.append(Spacer(1, 2 * mm))

    # ═══ DETAILS ═══
    def _field(label, value, style_v=s_value):
        return [Paragraph(label, s_label), Paragraph(str(value or "-"), style_v)]

    weight_str = f"{parcel.weight_kg} kg" if parcel.weight_kg else "-"
    declared_str = f"{parcel.currency} {parcel.declared_value}" if parcel.declared_value else "-"

    detail_data = [
        [
            [Paragraph("SENDER", s_section)],
            [Paragraph("RECEIVER", s_section)],
            [Paragraph("PARCEL DETAILS", s_section)],
        ],
        [
            _field("NAME", parcel.sender_name),
            _field("NAME", parcel.receiver_name),
            _field("DESCRIPTION", parcel.description),
        ],
        [
            _field("PHONE", str(parcel.sender_phone)),
            _field("PHONE", str(parcel.receiver_phone)),
            _field("SIZE / WEIGHT", f"{parcel.get_size_display()} / {weight_str}"),
        ],
        [
            _field("SHIPPING FEE", f"{parcel.currency} {parcel.shipping_fee}", s_price),
            _field("DECLARED VALUE", declared_str),
            _field("STATUS", f"● {parcel.status}"),
        ],
    ]

    left_w = content_w * 0.70
    col_w = left_w / 3
    details_table = Table(detail_data, colWidths=[col_w] * 3)
    details_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BRAND_NAVY),
        ("LINEBELOW", (0, 1), (-1, 1), 0.4, BORDER_COLOR),
        ("LINEBELOW", (0, 2), (-1, 2), 0.4, BORDER_COLOR),
        ("BACKGROUND", (0, 2), (-1, 2), ROW_ALT),
    ]))

    # Right panel: QR
    right_w = content_w * 0.30
    right_data = [
        [[Paragraph("SCAN TO TRACK", s_scan)]],
        [[qr_element]],
        [[Paragraph(parcel.tracking_number, ParagraphStyle("tkn", fontName="Courier-Bold", fontSize=7, textColor=MEDIUM_TEXT, alignment=TA_CENTER, leading=9))]],
    ]
    right_table = Table(right_data, colWidths=[right_w - 4 * mm])
    right_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (0, 0), BRAND_LIGHT),
    ]))

    main_table = Table([[details_table, right_table]], colWidths=[left_w, right_w])
    main_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBEFORE", (1, 0), (1, -1), 1, BORDER_COLOR, None, (3, 3)),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
    ]))
    elements.append(main_table)
    elements.append(Spacer(1, 1 * mm))

    # ═══ FOOTER ═══
    footer = Table([[Paragraph(
        "Eco Bus Co. Ltd  ·  Juba  ·  Kampala  ·  Nairobi  ·  Bor  ·  Nimule  |  "
        "Keep this receipt until parcel is collected  |  "
        "Juba: 0926 388 115  ·  0921 307 724  ·  Kampala: +256 782 393 125  ·  Nairobi: +254 140 342 959  ·  ecobus.co.ss@gmail.com",
        s_footer,
    )]], colWidths=[content_w])
    footer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [0, 0, 6, 6]),
    ]))
    elements.append(footer)

    doc.build(elements)
    buf.seek(0)
    return buf
