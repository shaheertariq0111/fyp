from src.repositories.cart_repository import CartRepository
from src.repositories.menu_repository import MenuRepository


class FakeTable:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


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
