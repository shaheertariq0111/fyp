from copy import deepcopy

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

    def list_page(
        self,
        *,
        limit: int,
        exclusive_start_key: dict | None = None,
    ) -> dict:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("customer page limit must be between 1 and 100")
        kwargs = {"Limit": limit}
        if exclusive_start_key is not None:
            kwargs["ExclusiveStartKey"] = deepcopy(exclusive_start_key)
        response = self.table.scan(**kwargs)
        return {
            "items": deepcopy(from_dynamodb(response.get("Items", []))),
            "last_evaluated_key": deepcopy(
                from_dynamodb(response.get("LastEvaluatedKey"))
            ),
        }

    def save(self, customer: dict) -> None:
        self.table.put_item(Item=to_dynamodb(customer))
