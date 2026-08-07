from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import inspect
import re

import pytest

import src.services.receipt_pdf_renderer as renderer_module
from src.services.receipt_pdf_renderer import (
    RECEIPT_PDF_CONTENT_TYPE,
    ReceiptPdfRenderError,
    ReceiptPdfRenderer,
)


def receipt_snapshot(*, delivery=True, decimal_values=False):
    number = Decimal if decimal_values else int
    delivery_fee = number(100) if delivery else None
    return {
        "schema_version": 1,
        "order_id": "ORD-123",
        "submitted_at": "2026-08-07T12:21:00+05:00",
        "status": "submitted_to_restaurant",
        "customer_name": "Sample Customer",
        "fulfillment_method": "delivery" if delivery else "takeaway",
        "delivery_address": "42 Sample Street" if delivery else None,
        "items": [
            {
                "item_id": "INTERNAL-ITEM-ID-SECRET",
                "name": "Garden Pizza",
                "quantity": 2,
                "unit_price": number(1200),
                "line_total": number(2400),
                "display_customizations": [
                    {"group": "Size", "options": ["Large"]},
                    {
                        "group": "Toppings",
                        "options": ["Olives", "Mushrooms"],
                    },
                ],
            },
            {
                "item_id": "INTERNAL-SIDE-ID-SECRET",
                "name": "Garlic Bread",
                "quantity": 1,
                "unit_price": number(500),
                "line_total": number(500),
                "display_customizations": [],
            },
        ],
        "subtotal": number(2900),
        "delivery_fee": delivery_fee,
        "total": number(3000 if delivery else 2900),
        "currency": "PKR",
    }


def renderer(**overrides):
    return ReceiptPdfRenderer(
        merchant_name=overrides.pop("merchant_name", "Sample Restaurant"),
        **overrides,
    )


def pdf_source(content):
    return content.decode("latin-1")


def test_valid_delivery_receipt_is_in_memory_pdf_with_deterministic_result():
    service = renderer()
    snapshot = receipt_snapshot()

    first = service.render(snapshot)
    second = service.render(snapshot)

    assert first.content.startswith(b"%PDF-")
    assert first.content_type == RECEIPT_PDF_CONTENT_TYPE
    assert first.filename == "Order-Receipt-ORD-123.pdf"
    assert first == second
    assert first.content == second.content


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=2),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(order_id=""),
        lambda value: value.update(order_id=" ORD-123 "),
        lambda value: value.update(submitted_at="2026-08-07T12:21:00"),
        lambda value: value.update(status="accepted"),
        lambda value: value.update(fulfillment_method="courier"),
        lambda value: value.update(items=[]),
        lambda value: value.update(currency=""),
    ],
)
def test_invalid_snapshot_top_level_fields_are_safely_rejected(mutation):
    snapshot = receipt_snapshot()
    mutation(snapshot)

    with pytest.raises(ReceiptPdfRenderError) as error:
        renderer().render(snapshot)

    assert error.value.error_code == "RECEIPT_SNAPSHOT_INVALID"
    assert error.value.retryable is False
    assert str(error.value) == "RECEIPT_SNAPSHOT_INVALID"
    assert "ORD-123" not in str(error.value)


def test_delivery_requires_address_and_takeaway_forbids_address():
    delivery = receipt_snapshot()
    delivery["delivery_address"] = ""
    takeaway = receipt_snapshot(delivery=False)
    takeaway["delivery_address"] = "unexpected address"

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(delivery)
    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(takeaway)


@pytest.mark.parametrize("quantity", [0, -1, True, False, 1.5, "1"])
def test_invalid_quantity_is_rejected(quantity):
    snapshot = receipt_snapshot()
    snapshot["items"][0]["quantity"] = quantity

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit_price", -1),
        ("line_total", -1),
        ("unit_price", True),
        ("line_total", False),
        ("unit_price", "1200"),
    ],
)
def test_invalid_item_amount_is_rejected(field, value):
    snapshot = receipt_snapshot()
    snapshot["items"][0][field] = value

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(snapshot)


@pytest.mark.parametrize(
    "customizations",
    [
        None,
        {},
        [None],
        [{"group": "", "options": ["Large"]}],
        [{"group": "Size", "options": []}],
        [{"group": "Size", "options": [""]}],
        [{"group": "Size", "options": [123]}],
    ],
)
def test_malformed_customization_is_rejected(customizations):
    snapshot = receipt_snapshot()
    snapshot["items"][0]["display_customizations"] = customizations

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(snapshot)


def test_inconsistent_item_arithmetic_is_rejected_without_replacement():
    snapshot = receipt_snapshot()
    snapshot["items"][0]["line_total"] = 2399

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(snapshot)

    assert snapshot["items"][0]["line_total"] == 2399


def test_inconsistent_subtotal_and_total_are_rejected():
    subtotal = receipt_snapshot()
    subtotal["subtotal"] = 2899
    total = receipt_snapshot()
    total["total"] = 2999

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(subtotal)
    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(total)


@pytest.mark.parametrize("field", ["subtotal", "delivery_fee", "total"])
@pytest.mark.parametrize("value", [-1, True])
def test_invalid_top_level_amount_is_rejected(field, value):
    snapshot = receipt_snapshot()
    snapshot[field] = value

    with pytest.raises(ReceiptPdfRenderError):
        renderer().render(snapshot)


@pytest.mark.parametrize("decimal_values", [False, True])
def test_integer_and_decimal_snapshot_values_render(decimal_values):
    result = renderer().render(receipt_snapshot(decimal_values=decimal_values))

    assert result.content.startswith(b"%PDF-")


def test_trusted_content_rules_for_delivery_receipt():
    source = pdf_source(renderer().render(receipt_snapshot()).content)

    for expected in (
        "Sample Restaurant",
        "ORDER RECEIPT",
        "ORD-123",
        "07 Aug 2026, 07:21 UTC",
        "Sample Customer",
        "42 Sample Street",
        "Garden Pizza",
        "Garlic Bread",
        "Size",
        "Large",
        "Toppings",
        "Olives",
        "Mushrooms",
        "PKR 1,200",
        "PKR 2,400",
        "PKR 3,000",
        "Delivery Fee",
    ):
        assert expected in source
    for forbidden in (
        "INTERNAL-ITEM-ID-SECRET",
        "INTERNAL-SIDE-ID-SECRET",
        "Tax",
        "GST",
        "Payment",
        "Discount",
        "Transaction Reference",
        "Invoice Number",
    ):
        assert forbidden not in source


def test_optional_customer_and_takeaway_sections_are_omitted():
    snapshot = receipt_snapshot(delivery=False)
    snapshot["customer_name"] = None
    source = pdf_source(renderer().render(snapshot).content)

    assert "Customer:" not in source
    assert "Delivery Address" not in source
    assert "42 Sample Street" not in source
    assert "Delivery Fee" not in source


def test_numeric_zero_delivery_fee_is_rendered():
    snapshot = receipt_snapshot()
    snapshot["delivery_fee"] = 0
    snapshot["total"] = snapshot["subtotal"]
    source = pdf_source(renderer().render(snapshot).content)

    assert "Delivery Fee" in source
    assert "PKR 0" in source


def test_fractional_amount_uses_snapshot_currency_without_guessing_symbol():
    snapshot = receipt_snapshot(delivery=False, decimal_values=True)
    snapshot["items"] = [{
        "item_id": "private-id",
        "name": "Fractional Item",
        "quantity": 1,
        "unit_price": Decimal("2400.50"),
        "line_total": Decimal("2400.50"),
        "display_customizations": [],
    }]
    snapshot["subtotal"] = Decimal("2400.50")
    snapshot["total"] = Decimal("2400.50")
    source = pdf_source(renderer().render(snapshot).content)

    assert "PKR 2,400.50" in source
    assert "Rs" not in source
    assert "$" not in source


def test_all_dynamic_paragraph_text_uses_central_markup_escaping():
    assert ReceiptPdfRenderer._safe_text(
        'John <b>Injected</b> & Sons "quoted"\nNext'
    ) == (
        "John &lt;b&gt;Injected&lt;/b&gt; &amp; Sons &quot;quoted&quot;"
        "<br/>Next"
    )

    snapshot = receipt_snapshot()
    snapshot["customer_name"] = "John <b>Injected</b> & Sons"
    snapshot["delivery_address"] = "42 <i>Injected</i> & Safe Street"
    snapshot["items"][0]["name"] = "Pizza <font color='red'>Injected</font> & More"
    snapshot["items"][0]["display_customizations"] = [{
        "group": "Size <b>Injected</b>",
        "options": ["Large & <i>Injected</i>"],
    }]

    content = renderer().render(snapshot).content

    assert content.startswith(b"%PDF-")
    assert b"Injected" in content


def test_very_long_dynamic_text_wraps_and_paginates_safely():
    snapshot = receipt_snapshot()
    snapshot["customer_name"] = "Customer & <safe> " * 100
    snapshot["delivery_address"] = "Long <address> & section " * 300
    snapshot["items"][0]["name"] = "Very Long <item> & name " * 300
    snapshot["items"][0]["display_customizations"] = [{
        "group": "Long <group> & label " * 40,
        "options": ["Long <option> & label " * 100],
    }]

    content = renderer().render(snapshot).content

    assert content.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", content)) > 1
    assert b"Very Long" in content


def large_snapshot(item_count=90):
    snapshot = receipt_snapshot(delivery=False)
    snapshot["customer_name"] = None
    snapshot["items"] = [
        {
            "item_id": f"internal-{index}",
            "name": f"Pagination Item {index:03d}",
            "quantity": 1,
            "unit_price": 10,
            "line_total": 10,
            "display_customizations": [
                {"group": "Choice", "options": [f"Option {index:03d}"]}
            ],
        }
        for index in range(item_count)
    ]
    snapshot["subtotal"] = item_count * 10
    snapshot["total"] = item_count * 10
    return snapshot


def test_large_receipt_paginates_without_dropping_items():
    content = renderer().render(large_snapshot()).content
    source = pdf_source(content)
    page_count = len(re.findall(rb"/Type\s*/Page\b", content))

    assert page_count > 1
    for index in range(90):
        assert f"Pagination Item {index:03d}" in source
        assert f"Option {index:03d}" in source


@pytest.mark.parametrize("max_pdf_bytes", [0, -1, True, 1.5, "100"])
def test_renderer_validates_configured_maximum_size(max_pdf_bytes):
    with pytest.raises(ValueError, match="RECEIPT_PDF_MAX_BYTES_INVALID"):
        renderer(max_pdf_bytes=max_pdf_bytes)


def test_oversized_rendered_pdf_raises_safe_error():
    with pytest.raises(ReceiptPdfRenderError) as error:
        renderer(max_pdf_bytes=100).render(receipt_snapshot())

    assert error.value.error_code == "RECEIPT_PDF_TOO_LARGE"
    assert str(error.value) == "RECEIPT_PDF_TOO_LARGE"


def test_renderer_does_not_use_current_time_randomness_or_mutable_services(
    monkeypatch,
):
    real_datetime = renderer_module.datetime

    class GuardedDateTime:
        fromisoformat = staticmethod(real_datetime.fromisoformat)

        @staticmethod
        def now(*_args, **_kwargs):
            raise AssertionError("Renderer must not use current time")

    monkeypatch.setattr(renderer_module, "datetime", GuardedDateTime)
    source = inspect.getsource(renderer_module)

    result = renderer().render(receipt_snapshot())

    assert result.content.startswith(b"%PDF-")
    for forbidden in (
        "uuid",
        "random",
        "OrderService",
        "menu_repository",
        "boto3",
        "Agentflo",
        "S3",
    ):
        assert forbidden not in source


def test_renderer_writes_no_permanent_pdf_file():
    before = set(Path.cwd().glob("*.pdf"))

    renderer().render(receipt_snapshot())

    assert set(Path.cwd().glob("*.pdf")) == before


def test_filename_is_safe_and_uses_no_customer_data():
    snapshot = receipt_snapshot()
    snapshot["order_id"] = "ORD/../../123 <unsafe>"
    result = renderer().render(snapshot)

    assert result.filename == "Order-Receipt-ORD-..-..-123-unsafe.pdf"
    assert "/" not in result.filename
    assert "Sample Customer" not in result.filename
    assert "42 Sample Street" not in result.filename
