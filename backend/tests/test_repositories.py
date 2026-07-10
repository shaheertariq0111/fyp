from src.repositories.cart_repository import CartRepository
from src.repositories.menu_repository import MenuRepository


class FakeTable:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeScanTable:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeDynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


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


def test_cart_repository_find_by_cart_id_paginates_scan_results():
    table = FakeScanTable([
        {"Items": [], "LastEvaluatedKey": {"PK": "user-1", "SK": "CART#old"}},
        {"Items": [{"cart_id": "target"}]},
    ])
    repository = CartRepository(FakeDynamo(table), "configured-table")

    assert repository.find_by_cart_id("target")["cart_id"] == "target"
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "user-1", "SK": "CART#old"}


def test_cart_repository_find_by_cart_item_id_paginates_scan_results():
    table = FakeScanTable([
        {"Items": [], "LastEvaluatedKey": {"PK": "user-1", "SK": "CART#old"}},
        {"Items": [{"cart_item_ids": ["target-item"]}]},
    ])
    repository = CartRepository(FakeDynamo(table), "configured-table")

    assert repository.find_by_cart_item_id("target-item")["cart_item_ids"] == ["target-item"]
    assert table.calls[1]["ExclusiveStartKey"] == {"PK": "user-1", "SK": "CART#old"}
