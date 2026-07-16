from src.services.menu_service import MenuService
from fakes import MemoryMenuRepository


def item(product_id, *, name=None, description="Configured menu description",
         category="pizza", source_category="Configured source", available=True,
         score=0, popular=False, tags=None, search_terms=None, best_for=None,
         starting_price=None, price=None, base_prices=None):
    return {
        "product_id": product_id,
        "name": name or product_id.replace("-", " ").title(),
        "description": description,
        "source_category": source_category,
        "category": category,
        "currency": "CUR",
        "available": available,
        "starting_price": starting_price,
        "price": price,
        "base_prices": base_prices or {},
        "requires_customization": False,
        "customization_group_ids": [],
        "upsell_group_ids": [],
        "tags": tags or [],
        "search_terms": search_terms or [],
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


def test_search_ranks_exact_food_terms_before_partial_chicken_matches():
    result = service([
        item("legend-ranch", name="Legend Ranch", description="Chicken pizza",
             tags=["chicken"], score=95, starting_price=750),
        item("pizza-n-wings", name="Pizza N Wings",
             description="1 Medium Classic Pizza + 6 pcs Wings + 2 Small Drinks.",
             tags=["combo", "chicken"], search_terms=["pizza n wings", "chicken"],
             score=90, popular=True, starting_price=5700),
        item("4-pcs-chicken-wings", name="4 Pcs Chicken Wings",
             description="Oven baked spicy wings, tossed with sauce.",
             tags=["chicken"], search_terms=["4 pcs chicken wings"],
             score=75, starting_price=500),
        item("6-pcs-chicken-wings", name="6 Pcs Chicken Wings",
             description="Oven baked spicy wings.",
             tags=["chicken"], search_terms=["6 pcs chicken wings"],
             score=82, popular=True, starting_price=700),
    ]).search_menu(query="chicken wings")

    assert [entry["product_id"] for entry in result.data["items"]] == [
        "6-pcs-chicken-wings",
        "4-pcs-chicken-wings",
        "pizza-n-wings",
    ]


def test_search_handles_compact_piece_count_and_wings_typo():
    result = service([
        item("pizza-n-wings", name="Pizza N Wings",
             description="1 Medium Classic Pizza + 6 pcs Wings + 2 Small Drinks.",
             tags=["combo", "chicken"], search_terms=["pizza n wings", "chicken"],
             score=90, popular=True, starting_price=5700),
        item("4-pcs-chicken-wings", name="4 Pcs Chicken Wings",
             description="Oven baked spicy wings, tossed with sauce.",
             tags=["chicken"], search_terms=["4 pcs chicken wings"],
             score=75, starting_price=500),
        item("6-pcs-chicken-wings", name="6 Pcs Chicken Wings",
             description="Oven baked spicy wings.",
             tags=["chicken"], search_terms=["6 pcs chicken wings"],
             score=82, popular=True, starting_price=700),
    ]).search_menu(query="4pcs chicken wigns")

    assert [entry["product_id"] for entry in result.data["items"]] == ["4-pcs-chicken-wings"]


def test_search_ignores_non_menu_words_without_hardcoded_stopwords():
    result = service([
        item("legend-ranch", name="Legend Ranch", description="Chicken pizza",
             tags=["chicken"], score=95, starting_price=750),
        item("4-pcs-chicken-wings", name="4 Pcs Chicken Wings",
             description="Oven baked spicy wings, tossed with sauce.",
             tags=["chicken"], search_terms=["4 pcs chicken wings"],
             score=75, starting_price=500),
    ]).search_menu(query="please can i order chicken wigns")

    assert [entry["product_id"] for entry in result.data["items"]] == ["4-pcs-chicken-wings"]


def test_unavailable_items_are_excluded_by_default():
    result = service([
        item("available", available=True),
        item("unavailable", available=False, score=100),
    ]).search_menu()
    assert [entry["product_id"] for entry in result.data["items"]] == ["available"]


def test_archived_items_are_excluded_from_customer_menu_search():
    archived = item("archived", available=True, score=100)
    archived["archived"] = True
    result = service([
        item("available", available=True),
        archived,
    ]).search_menu()
    assert [entry["product_id"] for entry in result.data["items"]] == ["available"]


def test_admin_adds_and_archives_menu_item():
    menu = service([])

    created = menu.admin_save_menu_item({
        "product_id": "new-item",
        "name": "New Item",
        "description": "Configured",
        "category": "pizza",
        "currency": "CUR",
        "available": True,
        "starting_price": 10,
        "base_prices": {},
        "requires_customization": False,
        "customization_group_ids": [],
        "upsell_group_ids": [],
        "tags": ["pizza"],
        "search_terms": [],
        "image_url": None,
        "metadata": {},
    })
    archived = menu.admin_archive_item("new-item")

    assert created["item"]["SK"] == "ITEM#new-item"
    assert archived["item"]["available"] is False
    assert archived["item"]["archived"] is True
    assert menu.search_menu().data["items"] == []


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
