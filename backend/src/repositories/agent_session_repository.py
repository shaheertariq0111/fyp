from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from .base import from_dynamodb, to_dynamodb


SUPPORT_STATE_FIELDS = (
    "pending_support_intent",
    "pending_order_id",
    "pending_complaint_description",
    "pending_support_updated_at",
)
VERIFIED_ORDER_FIELDS = (
    "verified_order_id",
    "verified_order_status",
    "verified_order_at",
)
WHATSAPP_ORDER_STATE_FIELDS = (
    "offered_menu_items",
    "whatsapp_menu_query",
    "shown_menu_item_ids",
    "whatsapp_menu_has_more",
    "whatsapp_order_state_updated_at",
)


class SupportStateConflictError(RuntimeError):
    pass


class SessionNotFoundError(RuntimeError):
    pass


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

    def get_owned(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict | None:
        try:
            return self._get_owned_session(customer_id, agent_session_id)
        except SessionNotFoundError:
            return None

    def save(self, session: dict) -> None:
        self.table.put_item(Item=to_dynamodb(session))

    def get_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        session = self._get_owned_session(customer_id, agent_session_id)
        return {
            field: session[field]
            for field in SUPPORT_STATE_FIELDS
            if field in session
        }

    def get_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        session = self._get_owned_session(customer_id, agent_session_id)
        return {
            field: session[field]
            for field in VERIFIED_ORDER_FIELDS
            if field in session
        }

    def get_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        session = self._get_owned_session(customer_id, agent_session_id)
        return {
            field: session[field]
            for field in WHATSAPP_ORDER_STATE_FIELDS
            if field in session
        }

    def update_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        offered_menu_items: list[dict],
        menu_query: str | None,
        shown_menu_item_ids: list[str],
        menu_has_more: bool,
        updated_at: str,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)
        self._update_support_attributes(
            session,
            update_expression=(
                "SET #items = :items, #menu_query = :menu_query, "
                "#shown_ids = :shown_ids, #has_more = :has_more, "
                "#updated_at = :updated_at"
            ),
            condition_expression=(
                "attribute_exists(#pk) "
                "AND #customer_id = :customer_id "
                "AND #agent_session_id = :agent_session_id"
            ),
            names={
                "#pk": "PK",
                "#customer_id": "customer_id",
                "#agent_session_id": "agent_session_id",
                "#items": "offered_menu_items",
                "#menu_query": "whatsapp_menu_query",
                "#shown_ids": "shown_menu_item_ids",
                "#has_more": "whatsapp_menu_has_more",
                "#updated_at": "whatsapp_order_state_updated_at",
            },
            values={
                ":customer_id": customer_id,
                ":agent_session_id": agent_session_id,
                ":items": offered_menu_items,
                ":menu_query": menu_query or "",
                ":shown_ids": shown_menu_item_ids,
                ":has_more": menu_has_more,
                ":updated_at": updated_at,
            },
        )

    def clear_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)
        self._update_support_attributes(
            session,
            update_expression=(
                "REMOVE #items, #menu_query, #shown_ids, #has_more, #updated_at"
            ),
            condition_expression=(
                "attribute_exists(#pk) "
                "AND #customer_id = :customer_id "
                "AND #agent_session_id = :agent_session_id"
            ),
            names={
                "#pk": "PK",
                "#customer_id": "customer_id",
                "#agent_session_id": "agent_session_id",
                "#items": "offered_menu_items",
                "#menu_query": "whatsapp_menu_query",
                "#shown_ids": "shown_menu_item_ids",
                "#has_more": "whatsapp_menu_has_more",
                "#updated_at": "whatsapp_order_state_updated_at",
            },
            values={
                ":customer_id": customer_id,
                ":agent_session_id": agent_session_id,
            },
        )

    def update_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        order_id: str,
        status: str,
        verified_at: str,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)
        names = {
            "#pk": "PK",
            "#customer_id": "customer_id",
            "#agent_session_id": "agent_session_id",
            "#order_id": "verified_order_id",
            "#status": "verified_order_status",
            "#verified_at": "verified_order_at",
        }
        values = {
            ":customer_id": customer_id,
            ":agent_session_id": agent_session_id,
            ":order_id": order_id,
            ":status": status,
            ":verified_at": verified_at,
        }
        self._update_support_attributes(
            session,
            update_expression=(
                "SET #order_id = :order_id, #status = :status, "
                "#verified_at = :verified_at"
            ),
            condition_expression=(
                "attribute_exists(#pk) "
                "AND #customer_id = :customer_id "
                "AND #agent_session_id = :agent_session_id"
            ),
            names=names,
            values=values,
        )

    def clear_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        expected_verified_at: str | None,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)
        names = {
            "#pk": "PK",
            "#customer_id": "customer_id",
            "#agent_session_id": "agent_session_id",
            "#order_id": "verified_order_id",
            "#status": "verified_order_status",
            "#verified_at": "verified_order_at",
        }
        values = {
            ":customer_id": customer_id,
            ":agent_session_id": agent_session_id,
        }
        condition = (
            "attribute_exists(#pk) "
            "AND #customer_id = :customer_id "
            "AND #agent_session_id = :agent_session_id AND "
        )
        if expected_verified_at is None:
            condition += "attribute_not_exists(#verified_at)"
        else:
            condition += "#verified_at = :expected_verified_at"
            values[":expected_verified_at"] = expected_verified_at
        self._update_support_attributes(
            session,
            update_expression="REMOVE #order_id, #status, #verified_at",
            condition_expression=condition,
            names=names,
            values=values,
        )

    def update_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
        expected_updated_at: str | None,
        intent: str,
        order_id: str | None,
        description: str | None,
        updated_at: str,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)

        names = {
            "#pk": "PK",
            "#customer_id": "customer_id",
            "#agent_session_id": "agent_session_id",
            "#intent": "pending_support_intent",
            "#order_id": "pending_order_id",
            "#description": "pending_complaint_description",
            "#updated_at": "pending_support_updated_at",
        }
        values = {
            ":customer_id": customer_id,
            ":agent_session_id": agent_session_id,
            ":intent": intent,
            ":updated_at": updated_at,
        }
        set_parts = ["#intent = :intent", "#updated_at = :updated_at"]
        remove_parts = []
        if order_id is None:
            remove_parts.append("#order_id")
        else:
            set_parts.append("#order_id = :order_id")
            values[":order_id"] = order_id
        if description is None:
            remove_parts.append("#description")
        else:
            set_parts.append("#description = :description")
            values[":description"] = description

        condition = (
            "attribute_exists(#pk) "
            "AND #customer_id = :customer_id "
            "AND #agent_session_id = :agent_session_id AND "
        )
        if expected_updated_at is None:
            condition += "attribute_not_exists(#updated_at)"
        else:
            condition += "#updated_at = :expected_updated_at"
            values[":expected_updated_at"] = expected_updated_at

        update_expression = f"SET {', '.join(set_parts)}"
        if remove_parts:
            update_expression += f" REMOVE {', '.join(remove_parts)}"
        self._update_support_attributes(
            session,
            update_expression=update_expression,
            condition_expression=condition,
            names=names,
            values=values,
        )

    def clear_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
        expected_updated_at: str | None = None,
    ) -> None:
        session = self._get_owned_session(customer_id, agent_session_id)

        names = {
            "#pk": "PK",
            "#customer_id": "customer_id",
            "#agent_session_id": "agent_session_id",
            "#intent": "pending_support_intent",
            "#order_id": "pending_order_id",
            "#description": "pending_complaint_description",
            "#updated_at": "pending_support_updated_at",
        }
        values = {
            ":customer_id": customer_id,
            ":agent_session_id": agent_session_id,
        }
        condition = (
            "attribute_exists(#pk) "
            "AND #customer_id = :customer_id "
            "AND #agent_session_id = :agent_session_id AND "
        )
        if expected_updated_at is None:
            condition += "attribute_not_exists(#updated_at)"
        else:
            condition += "#updated_at = :expected_updated_at"
            values[":expected_updated_at"] = expected_updated_at

        self._update_support_attributes(
            session,
            update_expression=(
                "REMOVE #intent, #order_id, #description, #updated_at"
            ),
            condition_expression=condition,
            names=names,
            values=values,
        )

    def _get_owned_session(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        key = {
            "PK": f"CUSTOMER#{customer_id}",
            "SK": f"SESSION#{agent_session_id}",
        }
        response = self.table.get_item(
            Key=to_dynamodb(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            raise SessionNotFoundError
        session = from_dynamodb(item)
        if (
            session.get("PK") != key["PK"]
            or session.get("SK") != key["SK"]
            or session.get("customer_id") != customer_id
            or session.get("agent_session_id") != agent_session_id
        ):
            raise SessionNotFoundError
        return session

    def _update_support_attributes(
        self,
        session: dict,
        *,
        update_expression: str,
        condition_expression: str,
        names: dict,
        values: dict,
    ) -> None:
        kwargs = {
            "Key": to_dynamodb(
                {
                    "PK": session["PK"],
                    "SK": session["SK"],
                }
            ),
            "UpdateExpression": update_expression,
            "ConditionExpression": condition_expression,
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = to_dynamodb(values)
        try:
            self.table.update_item(**kwargs)
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                self._get_owned_session(
                    session["customer_id"],
                    session["agent_session_id"],
                )
                raise SupportStateConflictError from exc
            raise
