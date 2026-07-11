from .base import from_dynamodb, to_dynamodb


class AuditRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def append(self, event: dict) -> None:
        self.table.put_item(Item=to_dynamodb(event), ConditionExpression="attribute_not_exists(SK)")

    def list_all(self) -> list[dict]:
        kwargs = {}
        items: list[dict] = []
        while True:
            response = self.table.scan(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return from_dynamodb(items)
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
