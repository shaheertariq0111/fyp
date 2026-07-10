from .base import to_dynamodb


class AuditRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def append(self, event: dict) -> None:
        self.table.put_item(Item=to_dynamodb(event), ConditionExpression="attribute_not_exists(SK)")
