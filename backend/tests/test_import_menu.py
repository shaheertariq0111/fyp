from decimal import Decimal

import pytest

from src.scripts.import_menu import load_payload, normalize_records


def normalize(record_or_payload):
    return list(normalize_records(record_or_payload, "restaurant", "branch"))


def menu_item(**overrides):
    record = {
        "entity_type": "menu_item",
        "product_id": "item-001",
        "name": "Configured item",
        "category": "configured-category",
        "currency": "CUR",
        "available": True,
        "starting_price": 10,
    }
    record.update(overrides)
    return record


def test_valid_menu_item_gets_keys_defaults_and_timestamps():
    record = normalize([menu_item()])[0]
    assert record["PK"] == "MENU#restaurant"
    assert record["SK"] == "ITEM#item-001"
    assert record["description"] == ""
    assert record["source_category"] == "configured-category"
    assert record["requires_customization"] is False
    assert record["customization_group_ids"] == []
    assert record["upsell_group_ids"] == []
    assert record["tags"] == ["configured-category"]
    assert record["image_url"] is None
    assert record["metadata"] == {
        "recommendation_score": 0,
        "is_popular": False,
        "best_for": [],
        "serves": None,
        "display_reason": None,
    }
    assert record["created_at"]
    assert record["updated_at"]


def test_valid_option_group_validates_type_options_and_defaults_required():
    record = normalize([{
        "entity_type": "option_group",
        "option_group_id": "dynamic-group",
        "name": "Dynamic group",
        "type": "single_select",
        "question": "Choose",
        "options": [{"option_id": "dynamic-option", "label": "Dynamic"}],
    }])[0]
    assert record["SK"] == "OPTION_GROUP#dynamic-group"
    assert record["required"] is False

    with pytest.raises(ValueError, match="Unsupported option_group type"):
        normalize([{**record, "type": "unsupported"}])
    with pytest.raises(ValueError, match="options must be a list"):
        normalize([{**record, "options": "not-a-list"}])


def test_valid_upsell_group_validates_items_and_gets_defaults():
    record = normalize([{
        "entity_type": "upsell_group",
        "upsell_group_id": "dynamic-upsell",
        "question": "Add something?",
        "items": ["item-001"],
    }])[0]
    assert record["SK"] == "UPSELL_GROUP#dynamic-upsell"
    assert record["trigger_categories"] == []
    assert record["max_suggestions"] == 3

    with pytest.raises(ValueError, match="items must be a list"):
        normalize([{**record, "items": "item-001"}])


def test_valid_category_gets_key_and_default_sort_order():
    record = normalize([{
        "entity_type": "category", "category_id": "configured-category",
        "name": "Configured category",
    }])[0]
    assert record["SK"] == "CATEGORY#configured-category"
    assert record["sort_order"] == 999


@pytest.mark.parametrize("invalid_value", [None, "unsupported"])
def test_invalid_or_missing_entity_type_error_includes_value(invalid_value):
    source = {} if invalid_value is None else {"entity_type": invalid_value}
    with pytest.raises(ValueError, match=repr(invalid_value)):
        normalize([source])


def test_menu_item_requires_at_least_one_pricing_field():
    source = menu_item()
    source.pop("starting_price")
    with pytest.raises(ValueError, match="at least one pricing field"):
        normalize([source])


def test_accepts_wrapped_records_and_raw_list():
    raw = normalize([menu_item()])
    wrapped = normalize({"records": [menu_item()]})
    assert raw[0]["SK"] == wrapped[0]["SK"]


def test_preserves_created_at_and_always_refreshes_updated_at():
    record = normalize([menu_item(created_at="preserved", updated_at="stale")])[0]
    assert record["created_at"] == "preserved"
    assert record["updated_at"] != "stale"


def test_json_float_prices_are_loaded_as_decimal(tmp_path):
    path = tmp_path / "menu.json"
    path.write_text('{"records":[{"price":10.25}]}')
    payload = load_payload(path)
    assert payload["records"][0]["price"] == Decimal("10.25")


@pytest.mark.parametrize("metadata", ["not-an-object", [], 1])
def test_menu_item_metadata_must_be_an_object(metadata):
    with pytest.raises(ValueError, match="metadata must be an object"):
        normalize([menu_item(metadata=metadata)])


@pytest.mark.parametrize("score", ["high", True, []])
def test_recommendation_score_must_be_numeric(score):
    with pytest.raises(ValueError, match="recommendation_score must be numeric"):
        normalize([menu_item(metadata={"recommendation_score": score})])


def test_metadata_best_for_must_be_a_list():
    with pytest.raises(ValueError, match="best_for must be a list"):
        normalize([menu_item(metadata={"best_for": "spicy"})])


def test_complete_recommendation_metadata_is_preserved():
    metadata = {
        "recommendation_score": Decimal("80.5"),
        "is_popular": True,
        "best_for": ["spicy", "classic"],
        "serves": "1-2",
        "display_reason": "Configured recommendation reason.",
    }
    record = normalize([menu_item(metadata=metadata)])[0]
    assert record["metadata"] == metadata
