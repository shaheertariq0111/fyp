from boto3.dynamodb.conditions import Attr, Key

from .base import from_dynamodb


class MenuRepository:
    def __init__(self, dynamodb, table_name: str, restaurant_id: str):
        self.table = dynamodb.Table(table_name)
        self.menu_pk = f"MENU#{restaurant_id}"

    def search(self, *, available_only: bool = True) -> list[dict]:
        expression = Key("PK").eq(self.menu_pk) & Key("SK").begins_with("ITEM#")
        kwargs = {"KeyConditionExpression": expression}
        if available_only:
            kwargs["FilterExpression"] = Attr("available").eq(True)
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
