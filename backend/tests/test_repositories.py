import pytest
from botocore.exceptions import ClientError

from src.repositories.cart_repository import CartCreationConflictError, CartRepository
from src.repositories.menu_repository import MenuRepository


class FakeTable:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeQueryTable:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeDynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


class ConditionalConflictTable:
    def put_item(self, **_kwargs):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )


def test_menu_repository_uses_restaurant_partition():
    table = FakeTable({"Item": {"product_id": "item"}})
    repository = MenuRepository(FakeDynamo(table), "configured-table", "configured-restaurant")
    assert repository.get_item("item")["product_id"] == "item"
    assert table.calls[0]["Key"] == {
        "PK": "MENU#configured-restaurant",
        "SK": "ITEM#item",
    }


def test_cart_repository_uses_user_partition():
    table = FakeTable({"Item": {"cart_id": "cart"}})
    repository = CartRepository(FakeDynamo(table), "configured-table")
    assert repository.get("user", "cart")["cart_id"] == "cart"
    assert table.calls[0]["Key"] == {"PK": "user", "SK": "CART#cart"}


def test_cart_repository_maps_conditional_create_failure_to_domain_conflict():
    repository = CartRepository(FakeDynamo(ConditionalConflictTable()), "table")

    with pytest.raises(CartCreationConflictError):
        repository.create({"PK": "user", "SK": "CART#cart"})


def test_cart_repository_find_by_cart_id_uses_owner_partition_key():
    table = FakeTable({"Item": {"cart_id": "target"}})
    repository = CartRepository(FakeDynamo(table), "configured-table")

    assert repository.find_by_cart_id("user-1", "target")["cart_id"] == "target"
    assert table.calls[0]["Key"] == {"PK": "user-1", "SK": "CART#target"}


def test_cart_repository_find_by_cart_item_id_queries_only_owner_partition():
    table = FakeQueryTable([
        {"Items": [], "LastEvaluatedKey": {"PK": "user-1", "SK": "CART#old"}},
        {"Items": [{"cart_item_ids": ["target-item"]}]},
    ])
    repository = CartRepository(FakeDynamo(table), "configured-table")

    assert repository.find_by_cart_item_id("user-1", "target-item")["cart_item_ids"] == ["target-item"]
    assert "KeyConditionExpression" in table.calls[0]
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "user-1", "SK": "CART#old"}
