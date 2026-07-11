from src.services.menu_service import MenuService
from fakes import MemoryMenuRepository


def item(product_id, *, available=True, score=0, popular=False, tags=None,
         best_for=None, starting_price=None, price=None, base_prices=None):
    return {
        "product_id": product_id,
        "name": product_id.replace("-", " ").title(),
        "description": "Configured menu description",
        "source_category": "Configured source",
        "category": "pizza",
        "currency": "CUR",
        "available": available,
        "starting_price": starting_price,
        "price": price,
        "base_prices": base_prices or {},
        "requires_customization": False,
        "customization_group_ids": [],
        "upsell_group_ids": [],
        "tags": tags or [],
        "metadata": {
            "recommendation_score": score,
            "is_popular": popular,
            "best_for": best_for or [],
            "serves": None,
            "display_reason": f"Reason for {product_id}",
        },
    }


def service(items):
    return MenuService(MemoryMenuRepository(items, []))


def test_recommendation_query_sorts_by_metadata_score():
    result = service([
        item("lower", score=20, popular=True, starting_price=5),
        item("higher", score=90, starting_price=10),
    ]).search_menu()
    assert [entry["product_id"] for entry in result.data["items"]] == ["higher", "lower"]
    assert result.data["items"][0]["metadata"]["display_reason"] == "Reason for higher"


def test_search_menu_limit_caps_returned_items_after_sorting():
    result = service([
        item(f"item-{index}", score=index, starting_price=10 + index)
        for index in range(8)
    ]).search_menu(limit=5)

    assert len(result.data["items"]) == 5
    assert [entry["product_id"] for entry in result.data["items"]] == [
        "item-7", "item-6", "item-5", "item-4", "item-3",
    ]


def test_search_matches_tags_and_metadata_best_for():
    menu = service([
        item("tag-match", tags=["spicy"], best_for=["classic"]),
        item("metadata-match", tags=["pizza"], best_for=["vegetarian"]),
        item("no-match", tags=["mild"], best_for=["family"]),
    ])
    query_result = menu.search_menu(query="recommend vegetarian")
    tag_result = menu.search_menu(tags=["classic"])
    assert [entry["product_id"] for entry in query_result.data["items"]] == ["metadata-match"]
    assert [entry["product_id"] for entry in tag_result.data["items"]] == ["tag-match"]


def test_unavailable_items_are_excluded_by_default():
    result = service([
        item("available", available=True),
        item("unavailable", available=False, score=100),
    ]).search_menu()
    assert [entry["product_id"] for entry in result.data["items"]] == ["available"]


def test_max_price_uses_starting_price_then_price_then_minimum_base_price():
    result = service([
        item("starting", starting_price=9, price=99),
        item("fixed", price=10),
        item("sized", base_prices={"configured-a": 8, "configured-b": 12}),
        item("expensive", base_prices={"configured-a": 11}),
    ]).search_menu(max_price=10)
    assert {entry["product_id"] for entry in result.data["items"]} == {
        "starting", "fixed", "sized",
    }
