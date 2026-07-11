from boto3.dynamodb.conditions import Key

from .base import from_dynamodb, to_dynamodb


class CustomerRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, customer: dict) -> None:
        self.table.put_item(
            Item=to_dynamodb(customer),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get(self, customer_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": f"CUSTOMER#{customer_id}", "SK": "PROFILE"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def get_by_phone_hash(self, phone_hash: str) -> dict | None:
        response = self.table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"PHONE#{phone_hash}"),
            Limit=1,
        )
        items = response.get("Items", [])
        return from_dynamodb(items[0]) if items else None

    def list_all(self) -> list[dict]:
        kwargs = {}
        items: list[dict] = []
        while True:
            response = self.table.scan(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return from_dynamodb(items)
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def save(self, customer: dict) -> None:
        self.table.put_item(Item=to_dynamodb(customer))
