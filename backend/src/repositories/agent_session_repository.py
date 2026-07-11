from boto3.dynamodb.conditions import Attr

from .base import from_dynamodb, to_dynamodb


class AgentSessionRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, session: dict) -> None:
        self.table.put_item(
            Item=to_dynamodb(session),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get(self, agent_session_id: str) -> dict | None:
        kwargs = {"FilterExpression": Attr("agent_session_id").eq(agent_session_id)}
        while True:
            response = self.table.scan(**kwargs)
            items = response.get("Items", [])
            if items:
                return from_dynamodb(items[0])
            if "LastEvaluatedKey" not in response:
                return None
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def save(self, session: dict) -> None:
        self.table.put_item(Item=to_dynamodb(session))
