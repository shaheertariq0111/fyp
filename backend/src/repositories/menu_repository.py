from boto3.dynamodb.conditions import Attr, Key

from .base import from_dynamodb, to_dynamodb


class MenuRepository:
    def __init__(self, dynamodb, table_name: str, restaurant_id: str):
        self.table = dynamodb.Table(table_name)
        self.menu_pk = f"MENU#{restaurant_id}"

    def search(self, *, available_only: bool = True) -> list[dict]:
        expression = Key("PK").eq(self.menu_pk) & Key("SK").begins_with("ITEM#")
        kwargs = {"KeyConditionExpression": expression}
        if available_only:
            kwargs["FilterExpression"] = (
                Attr("available").eq(True)
                & (Attr("archived").not_exists() | Attr("archived").eq(False))
            )
        items: list[dict] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(from_dynamodb(response.get("Items", [])))
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def get_item(self, item_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": self.menu_pk, "SK": f"ITEM#{item_id}"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def list_entities(self, entity_type: str) -> list[dict]:
        prefixes = {
            "menu_item": "ITEM#",
            "category": "CATEGORY#",
            "option_group": "OPTION_GROUP#",
            "upsell_group": "UPSELL_GROUP#",
        }
        prefix = prefixes[entity_type]
        kwargs = {
            "KeyConditionExpression": Key("PK").eq(self.menu_pk) & Key("SK").begins_with(prefix)
        }
        items: list[dict] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return from_dynamodb(items)
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def get_entity(self, entity_type: str, entity_id: str) -> dict | None:
        prefixes = {
            "menu_item": "ITEM",
            "category": "CATEGORY",
            "option_group": "OPTION_GROUP",
            "upsell_group": "UPSELL_GROUP",
        }
        response = self.table.get_item(
            Key={"PK": self.menu_pk, "SK": f"{prefixes[entity_type]}#{entity_id}"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def save_entity(self, entity: dict) -> None:
        self.table.put_item(Item=to_dynamodb(entity))

    def get_option_group(self, group_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": self.menu_pk, "SK": f"OPTION_GROUP#{group_id}"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def get_upsell_group(self, group_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": self.menu_pk, "SK": f"UPSELL_GROUP#{group_id}"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))
