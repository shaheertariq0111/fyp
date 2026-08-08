from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


RECEIPT_PDF_CONTENT_TYPE = "application/pdf"
RECEIPT_PDF_MAX_BYTES = 5 * 1024 * 1024
RECEIPT_RENDERER_VERSION = 1


class ReceiptPdfRenderError(ValueError):
    def __init__(self, error_code: str = "RECEIPT_SNAPSHOT_INVALID") -> None:
        self.error_code = error_code
        self.retryable = False
        super().__init__(error_code)


@dataclass(frozen=True, slots=True)
class RenderedReceiptPdf:
    content: bytes
    filename: str
    content_type: str = RECEIPT_PDF_CONTENT_TYPE


class _InvariantReceiptCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 0
        super().__init__(*args, **kwargs)
        self.setAuthor("")
        self.setCreator(f"ReceiptPdfRenderer/{RECEIPT_RENDERER_VERSION}")
        self.setTitle("Order Receipt")
        self.setSubject("")
        self.setKeywords("")


class ReceiptPdfRenderer:
    def __init__(
        self,
        *,
        merchant_name: str,
        max_pdf_bytes: int = RECEIPT_PDF_MAX_BYTES,
    ) -> None:
        if not isinstance(merchant_name, str) or not merchant_name.strip():
            raise ValueError("RECEIPT_MERCHANT_NAME_REQUIRED")
        if type(max_pdf_bytes) is not int or max_pdf_bytes < 1:
            raise ValueError("RECEIPT_PDF_MAX_BYTES_INVALID")
        self.merchant_name = merchant_name.strip()
        self.max_pdf_bytes = max_pdf_bytes
        self._styles = self._build_styles()

    def render(self, snapshot: Mapping[str, Any]) -> RenderedReceiptPdf:
        receipt = self._validate_snapshot(snapshot)
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=18 * mm,
            title="Order Receipt",
            author="",
            creator=f"ReceiptPdfRenderer/{RECEIPT_RENDERER_VERSION}",
            subject="",
        )
        document.build(
            self._story(receipt),
            canvasmaker=_InvariantReceiptCanvas,
            onFirstPage=self._page_footer,
            onLaterPages=self._page_footer,
        )
        content = buffer.getvalue()
        if len(content) > self.max_pdf_bytes:
            raise ReceiptPdfRenderError("RECEIPT_PDF_TOO_LARGE")
        return RenderedReceiptPdf(
            content=content,
            filename=self.filename_for_order_id(receipt["order_id"]),
        )

    def _story(self, receipt: dict[str, Any]) -> list[Any]:
        styles = self._styles
        story: list[Any] = [
            Paragraph(self._safe_text(self.merchant_name), styles["merchant"]),
            Paragraph("ORDER RECEIPT", styles["title"]),
            Spacer(1, 4 * mm),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")),
            Spacer(1, 4 * mm),
        ]

        metadata = [
            [
                Paragraph("Order ID", styles["label"]),
                Paragraph(self._safe_text(receipt["order_id"]), styles["value"]),
            ],
            [
                Paragraph("Submitted", styles["label"]),
                Paragraph(self._safe_text(receipt["submitted_display"]), styles["value"]),
            ],
            [
                Paragraph("Fulfillment", styles["label"]),
                Paragraph(
                    self._safe_text(receipt["fulfillment_method"].title()),
                    styles["value"],
                ),
            ],
        ]
        story.append(Table(
            metadata,
            colWidths=[34 * mm, 120 * mm],
            splitInRow=1,
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2 * mm),
            ]),
        ))

        if receipt["customer_name"] is not None:
            story.extend([
                Spacer(1, 2 * mm),
                Paragraph(
                    f"<b>Customer:</b> {self._safe_text(receipt['customer_name'])}",
                    styles["body"],
                ),
            ])
        if receipt["fulfillment_method"] == "delivery":
            story.extend([
                Spacer(1, 1.5 * mm),
                Paragraph("<b>Delivery Address</b>", styles["body"]),
                Paragraph(
                    self._safe_text(receipt["delivery_address"]),
                    styles["body"],
                ),
            ])

        story.extend([
            Spacer(1, 6 * mm),
            self._item_header(),
        ])
        for item in receipt["items"]:
            story.extend(self._item_story(item, receipt["currency"]))

        story.extend([
            Spacer(1, 5 * mm),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")),
            Spacer(1, 3 * mm),
            self._totals_table(receipt),
            Spacer(1, 8 * mm),
            Paragraph("Thank you for your order", styles["footer_message"]),
        ])
        return story

    def _item_header(self) -> Table:
        styles = self._styles
        return Table(
            [[
                Paragraph("Item", styles["table_header"]),
                Paragraph("Qty", styles["table_header_center"]),
                Paragraph("Unit Price", styles["table_header_right"]),
                Paragraph("Line Total", styles["table_header_right"]),
            ]],
            colWidths=[76 * mm, 16 * mm, 31 * mm, 31 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
            ]),
        )

    def _item_story(self, item: dict[str, Any], currency: str) -> list[Any]:
        styles = self._styles
        item_table = Table(
            [[
                Paragraph(self._safe_text(item["name"]), styles["item_name"]),
                Paragraph(str(item["quantity"]), styles["table_center"]),
                Paragraph(
                    self._safe_text(self._amount(currency, item["unit_price"])),
                    styles["table_right"],
                ),
                Paragraph(
                    self._safe_text(self._amount(currency, item["line_total"])),
                    styles["table_right"],
                ),
            ]],
            colWidths=[76 * mm, 16 * mm, 31 * mm, 31 * mm],
            splitInRow=1,
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
            ]),
        )
        story: list[Any] = [item_table]
        for customization in item["display_customizations"]:
            options = ", ".join(customization["options"])
            story.append(Paragraph(
                (
                    f"<b>{self._safe_text(customization['group'])}:</b> "
                    f"{self._safe_text(options)}"
                ),
                styles["customization"],
            ))
        return story

    def _totals_table(self, receipt: dict[str, Any]) -> Table:
        styles = self._styles
        rows = [[
            Paragraph("Subtotal", styles["total_label"]),
            Paragraph(
                self._safe_text(self._amount(receipt["currency"], receipt["subtotal"])),
                styles["total_value"],
            ),
        ]]
        if receipt["delivery_fee"] is not None:
            rows.append([
                Paragraph("Delivery Fee", styles["total_label"]),
                Paragraph(
                    self._safe_text(
                        self._amount(receipt["currency"], receipt["delivery_fee"])
                    ),
                    styles["total_value"],
                ),
            ])
        rows.append([
            Paragraph("Total", styles["grand_total_label"]),
            Paragraph(
                self._safe_text(self._amount(receipt["currency"], receipt["total"])),
                styles["grand_total_value"],
            ),
        ])
        return Table(
            rows,
            colWidths=[112 * mm, 42 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.7 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.7 * mm),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#0F172A")),
            ]),
        )

    @staticmethod
    def _page_footer(pdf_canvas, document) -> None:
        pdf_canvas.saveState()
        pdf_canvas.setFont("Helvetica", 8)
        pdf_canvas.setFillColor(colors.HexColor("#64748B"))
        pdf_canvas.drawString(18 * mm, 10 * mm, "Order Receipt")
        pdf_canvas.drawRightString(
            A4[0] - 18 * mm,
            10 * mm,
            f"Page {document.page}",
        )
        pdf_canvas.restoreState()

    @staticmethod
    def _safe_text(value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        escaped = escape(normalized, {'"': "&quot;", "'": "&#39;"})
        return escaped.replace("\n", "<br/>")

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ReceiptPdfRenderError()
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ReceiptPdfRenderError() from None
        if not number.is_finite() or number < 0:
            raise ReceiptPdfRenderError()
        return number

    @classmethod
    def _amount(cls, currency: str, value: Decimal) -> str:
        if value == value.to_integral_value():
            formatted = f"{value:,.0f}"
        else:
            decimal_places = max(2, -value.as_tuple().exponent)
            formatted = f"{value:,.{decimal_places}f}"
        return f"{currency} {formatted}"

    @classmethod
    def _validate_snapshot(cls, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(snapshot, Mapping)
            or type(snapshot.get("schema_version")) is not int
            or snapshot.get("schema_version") != 1
        ):
            raise ReceiptPdfRenderError()

        order_id = cls._canonical_text(snapshot.get("order_id"))
        submitted_at = cls._aware_timestamp(snapshot.get("submitted_at"))
        if snapshot.get("status") != "submitted_to_restaurant":
            raise ReceiptPdfRenderError()
        fulfillment_method = snapshot.get("fulfillment_method")
        if fulfillment_method not in {"delivery", "takeaway"}:
            raise ReceiptPdfRenderError()

        customer_name = snapshot.get("customer_name")
        if customer_name is not None:
            customer_name = cls._display_text(customer_name)
        delivery_address = snapshot.get("delivery_address")
        if fulfillment_method == "delivery":
            delivery_address = cls._display_text(delivery_address)
        elif delivery_address is not None:
            raise ReceiptPdfRenderError()

        raw_items = snapshot.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ReceiptPdfRenderError()
        items = [cls._validate_item(item) for item in raw_items]

        subtotal = cls._decimal(snapshot.get("subtotal"))
        raw_delivery_fee = snapshot.get("delivery_fee")
        delivery_fee = (
            None if raw_delivery_fee is None else cls._decimal(raw_delivery_fee)
        )
        total = cls._decimal(snapshot.get("total"))
        currency = cls._display_text(snapshot.get("currency"))

        if sum((item["line_total"] for item in items), Decimal(0)) != subtotal:
            raise ReceiptPdfRenderError()
        if subtotal + (delivery_fee if delivery_fee is not None else Decimal(0)) != total:
            raise ReceiptPdfRenderError()

        return {
            "order_id": order_id,
            "submitted_at": submitted_at,
            "submitted_display": submitted_at.astimezone(timezone.utc).strftime(
                "%d %b %Y, %H:%M UTC"
            ),
            "customer_name": customer_name,
            "fulfillment_method": fulfillment_method,
            "delivery_address": delivery_address,
            "items": items,
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "total": total,
            "currency": currency,
        }

    @classmethod
    def _validate_item(cls, item: Any) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            raise ReceiptPdfRenderError()
        name = cls._display_text(item.get("name"))
        quantity = item.get("quantity")
        if type(quantity) is not int or quantity < 1:
            raise ReceiptPdfRenderError()
        unit_price = cls._decimal(item.get("unit_price"))
        line_total = cls._decimal(item.get("line_total"))
        if unit_price * quantity != line_total:
            raise ReceiptPdfRenderError()

        raw_customizations = item.get("display_customizations")
        if not isinstance(raw_customizations, list):
            raise ReceiptPdfRenderError()
        customizations = []
        for customization in raw_customizations:
            if not isinstance(customization, Mapping):
                raise ReceiptPdfRenderError()
            group = cls._display_text(customization.get("group"))
            raw_options = customization.get("options")
            if not isinstance(raw_options, list) or not raw_options:
                raise ReceiptPdfRenderError()
            options = [cls._display_text(option) for option in raw_options]
            customizations.append({"group": group, "options": options})
        return {
            "name": name,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
            "display_customizations": customizations,
        }

    @staticmethod
    def _display_text(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ReceiptPdfRenderError()
        return value.strip()

    @staticmethod
    def _canonical_text(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
        ):
            raise ReceiptPdfRenderError()
        return value

    @staticmethod
    def _aware_timestamp(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ReceiptPdfRenderError()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ReceiptPdfRenderError() from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ReceiptPdfRenderError()
        return parsed

    @staticmethod
    def filename_for_order_id(order_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", order_id).strip("._-")
        if not safe:
            safe = hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:16]
        return f"Order-Receipt-{safe[:100]}.pdf"

    @staticmethod
    def _build_styles() -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        dark = colors.HexColor("#0F172A")
        muted = colors.HexColor("#475569")
        return {
            "merchant": ParagraphStyle(
                "ReceiptMerchant",
                parent=base["Heading1"],
                fontName="Helvetica-Bold",
                fontSize=18,
                leading=22,
                alignment=TA_CENTER,
                textColor=dark,
                spaceAfter=2 * mm,
            ),
            "title": ParagraphStyle(
                "ReceiptTitle",
                parent=base["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=14,
                alignment=TA_CENTER,
                textColor=muted,
            ),
            "label": ParagraphStyle(
                "ReceiptLabel",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=12,
                textColor=muted,
            ),
            "value": ParagraphStyle(
                "ReceiptValue",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                textColor=dark,
            ),
            "body": ParagraphStyle(
                "ReceiptBody",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=13,
                textColor=dark,
            ),
            "table_header": ParagraphStyle(
                "ReceiptTableHeader",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=10,
                alignment=TA_LEFT,
                textColor=dark,
            ),
            "table_header_center": ParagraphStyle(
                "ReceiptTableHeaderCenter",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=10,
                alignment=TA_CENTER,
                textColor=dark,
            ),
            "table_header_right": ParagraphStyle(
                "ReceiptTableHeaderRight",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=10,
                alignment=TA_RIGHT,
                textColor=dark,
            ),
            "item_name": ParagraphStyle(
                "ReceiptItemName",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=12,
                textColor=dark,
            ),
            "table_center": ParagraphStyle(
                "ReceiptTableCenter",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                alignment=TA_CENTER,
                textColor=dark,
            ),
            "table_right": ParagraphStyle(
                "ReceiptTableRight",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                alignment=TA_RIGHT,
                textColor=dark,
            ),
            "customization": ParagraphStyle(
                "ReceiptCustomization",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=8,
                leading=11,
                leftIndent=4 * mm,
                rightIndent=2 * mm,
                textColor=muted,
                spaceBefore=0.8 * mm,
                spaceAfter=0.8 * mm,
            ),
            "total_label": ParagraphStyle(
                "ReceiptTotalLabel",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                alignment=TA_RIGHT,
                textColor=muted,
            ),
            "total_value": ParagraphStyle(
                "ReceiptTotalValue",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                alignment=TA_RIGHT,
                textColor=dark,
            ),
            "grand_total_label": ParagraphStyle(
                "ReceiptGrandTotalLabel",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=14,
                alignment=TA_RIGHT,
                textColor=dark,
            ),
            "grand_total_value": ParagraphStyle(
                "ReceiptGrandTotalValue",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=14,
                alignment=TA_RIGHT,
                textColor=dark,
            ),
            "footer_message": ParagraphStyle(
                "ReceiptFooterMessage",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=8,
                leading=11,
                alignment=TA_CENTER,
                textColor=muted,
            ),
        }
