from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from src.infrastructure.config import get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource


REQUIRED_ENTITY_FIELDS = {
    "menu_item": {"product_id", "name", "category", "currency", "available"},
    "option_group": {"option_group_id", "name", "type", "question", "options"},
    "upsell_group": {"upsell_group_id", "question", "items"},
    "category": {"category_id", "name"},
}
OPTION_GROUP_TYPES = {"single_select", "multi_select"}
MENU_ITEM_PRICE_FIELDS = {"price", "base_prices", "starting_price"}


def _record_key(record: dict[str, Any]) -> str:
    entity_type = record["entity_type"]
    if entity_type == "menu_item":
        return f"ITEM#{record['product_id']}"
    if entity_type == "option_group":
        return f"OPTION_GROUP#{record['option_group_id']}"
    if entity_type == "upsell_group":
        return f"UPSELL_GROUP#{record['upsell_group_id']}"
    if entity_type == "category":
        return f"CATEGORY#{record['category_id']}"
    raise ValueError(f"Unsupported menu entity_type: {entity_type}")


def normalize_records(payload: Any, restaurant_id: str, branch_id: str) -> Iterable[dict]:
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Menu import JSON must be a list or an object containing records")
    for index, source in enumerate(records):
        if not isinstance(source, dict):
            raise ValueError(f"Menu record at index {index} must be an object")
        record = dict(source)
        entity_type = record.get("entity_type")
        if entity_type not in REQUIRED_ENTITY_FIELDS:
            raise ValueError(f"Unsupported menu entity_type: {entity_type!r}")
        missing = REQUIRED_ENTITY_FIELDS[entity_type] - record.keys()
        if missing:
            raise ValueError(f"{entity_type} record is missing fields: {sorted(missing)}")

        if entity_type == "menu_item":
            if not any(field in record and record[field] is not None
                       for field in MENU_ITEM_PRICE_FIELDS):
                raise ValueError(
                    "menu_item record must include at least one pricing field: "
                    "price, base_prices, or starting_price"
                )
            record.setdefault("description", "")
            record.setdefault("source_category", record["category"])
            record.setdefault("requires_customization", False)
            record.setdefault("customization_group_ids", [])
            record.setdefault("upsell_group_ids", [])
            record.setdefault("tags", [record["category"]])
            record.setdefault("image_url", None)
            record.setdefault("metadata", {})
            if not isinstance(record["metadata"], dict):
                raise ValueError("menu_item metadata must be an object")
            metadata = record["metadata"]
            score = metadata.setdefault("recommendation_score", 0)
            if isinstance(score, bool) or not isinstance(score, (int, float, Decimal)):
                raise ValueError("metadata.recommendation_score must be numeric")
            is_popular = metadata.setdefault("is_popular", False)
            if not isinstance(is_popular, bool):
                raise ValueError("metadata.is_popular must be boolean")
            best_for = metadata.setdefault("best_for", [])
            if not isinstance(best_for, list):
                raise ValueError("metadata.best_for must be a list")
            serves = metadata.setdefault("serves", None)
            if serves is not None and not isinstance(serves, str):
                raise ValueError("metadata.serves must be a string or null")
            display_reason = metadata.setdefault("display_reason", None)
            if display_reason is not None and not isinstance(display_reason, str):
                raise ValueError("metadata.display_reason must be a string or null")
        elif entity_type == "option_group":
            if record["type"] not in OPTION_GROUP_TYPES:
                raise ValueError(
                    f"Unsupported option_group type: {record['type']!r}; "
                    f"expected one of {sorted(OPTION_GROUP_TYPES)}"
                )
            if not isinstance(record["options"], list):
                raise ValueError("option_group options must be a list")
            record.setdefault("required", False)
        elif entity_type == "upsell_group":
            if not isinstance(record["items"], list):
                raise ValueError("upsell_group items must be a list")
            record.setdefault("trigger_categories", [])
            record.setdefault("max_suggestions", 3)
        elif entity_type == "category":
            record.setdefault("sort_order", 999)

        now = datetime.now(timezone.utc).isoformat()
        record.setdefault("created_at", now)
        record["updated_at"] = now
        record.update(
            PK=f"MENU#{restaurant_id}",
            SK=_record_key(record),
            restaurant_id=restaurant_id,
            branch_id=record.get("branch_id", branch_id),
        )
        yield record


def load_payload(path: Path) -> Any:
    return json.loads(path.read_text(), parse_float=Decimal)


def import_menu(path: Path) -> int:
    settings = get_settings()
    payload = load_payload(path)
    table = get_dynamodb_resource(settings).Table(settings.menu_table_name)
    count = 0
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
        for record in normalize_records(payload, settings.restaurant_id, settings.branch_id):
            batch.put_item(Item=record)
            count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import normalized menu records")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"Imported {import_menu(args.path)} menu records")
