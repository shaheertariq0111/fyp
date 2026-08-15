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
    assert result.data["has_more"] is True
    assert result.grounding.authoritative_domains == ["menu"]
    assert result.grounding.presentation.max_items == 5


def test_configured_customer_result_limit_bounds_default_search_page():
    menu = MenuService(
        MemoryMenuRepository([
            item("item-1", name="First", starting_price=10),
            item("item-2", name="Second", starting_price=11),
            item("item-3", name="Third", starting_price=12),
        ], []),
        customer_result_limit=2,
    )

    result = menu.search_menu()

    assert len(result.data["items"]) == 2
    assert result.data["has_more"] is True
    assert result.grounding.presentation.max_items == 2
    assert result.grounding.required_next_effect == "item_selected"
    assert len(result.grounding.offered_options) == 2


def test_list_menu_categories_returns_customer_facing_database_categories():
    repository = MemoryMenuRepository([], [])
    repository.categories = {
        "pizza": {
            "category_id": "pizza",
            "name": "Pizza",
            "description": "Configured category",
            "sort_order": 2,
            "available": True,
        },
        "wings": {
            "category_id": "wings",
            "name": "Wings",
            "sort_order": 1,
            "available": True,
        },
        "hidden": {
            "category_id": "hidden",
            "name": "Hidden",
            "sort_order": 0,
            "available": False,
        },
    }
    result = MenuService(repository, customer_result_limit=2).list_menu_categories()

    assert result.data == {
        "categories": [
            {"category_id": "wings", "name": "Wings", "sort_order": 1},
            {
                "category_id": "pizza",
                "name": "Pizza",
                "description": "Configured category",
                "sort_order": 2,
            },
        ],
        "has_more": False,
    }
    assert result.next_action == "present_menu_categories"
    assert result.grounding.authoritative_domains == ["menu"]
    assert [option.label for option in result.grounding.offered_options] == [
        "Wings",
        "Pizza",
    ]
    assert result.grounding.exact_customer_text == (
        "You can start with these menu categories:\n"
        "1. Wings\n"
        "2. Pizza\n"
        "Which one sounds good?"
    )


def test_list_menu_categories_falls_back_to_available_item_categories():
    result = service([
        item("pizza", name="Pizza Item", category="pizza", source_category="Pizza"),
        item("wings", name="Wing Item", category="wings", source_category="Wings"),
        item(
            "archived",
            name="Archived",
            category="hidden",
            source_category="Hidden",
            available=False,
        ),
    ]).list_menu_categories()

    assert [category["name"] for category in result.data["categories"]] == [
        "Pizza",
        "Wings",
    ]
    assert "Hidden" not in result.grounding.exact_customer_text


def test_search_menu_exact_customer_text_uses_only_returned_limited_records():
    menu = MenuService(
        MemoryMenuRepository([
            item("first", name="First", score=10, starting_price=10),
            item("second", name="Second", score=20, starting_price=11),
            item("third", name="Third", score=30, starting_price=12),
            item("fourth", name="Fourth", score=40, starting_price=13),
        ], []),
        customer_result_limit=2,
    )

    result = menu.search_menu()

    assert [entry["product_id"] for entry in result.data["items"]] == [
        "fourth",
        "third",
    ]
    assert result.data["has_more"] is True
    assert result.grounding.exact_customer_text == (
        "Here are the current menu options I found:\n"
        "1. Fourth - from CUR 13\n"
        "2. Third - from CUR 12\n"
        "More matching items are available. "
        "Would you like to see more, or choose one of these?"
    )
    assert "First" not in result.grounding.exact_customer_text
    assert "Second" not in result.grounding.exact_customer_text


def test_search_menu_exact_customer_text_marks_complete_result_set():
    result = service([
        item("first", name="First", score=20, price=10),
        item("second", name="Second", score=10, price=11),
    ]).search_menu()

    assert result.data["has_more"] is False
    assert result.grounding.exact_customer_text == (
        "Here are the current menu options I found:\n"
        "1. First - CUR 10\n"
        "2. Second - CUR 11\n"
        "Those are all the matching items I found.\n"
        "Which item would you like?"
    )
    assert "More matching items are available" not in (
        result.grounding.exact_customer_text
    )


def test_search_menu_exact_customer_text_omits_missing_price():
    result = service([
        item("without-price", name="Price Pending"),
    ]).search_menu()

    assert result.grounding.exact_customer_text == (
        "Here are the current menu options I found:\n"
        "1. Price Pending\n"
        "Those are all the matching items I found.\n"
        "Which item would you like?"
    )
    assert "price shown" not in result.grounding.exact_customer_text


def test_search_menu_exact_customer_text_does_not_expose_base_price_keys():
    result = service([
        item(
            "configured",
            name="Configured Item",
            base_prices={"private-price-key-a": 12, "private-price-key-b": 9},
        ),
    ]).search_menu()

    assert "Configured Item - from CUR 9" in result.grounding.exact_customer_text
    assert "private-price-key-a" not in result.grounding.exact_customer_text
    assert "private-price-key-b" not in result.grounding.exact_customer_text


def test_search_menu_exact_customer_text_prefers_authoritative_starting_price():
    result = service([
        item(
            "configured",
            name="Configured Item",
            starting_price=9,
            price=99,
        ),
    ]).search_menu(max_price=10)

    assert "Configured Item - from CUR 9" in result.grounding.exact_customer_text
    assert "CUR 99" not in result.grounding.exact_customer_text


def test_search_menu_no_match_has_authoritative_customer_text():
    result = service([
        item("pizza", name="Pizza"),
    ]).search_menu(query="unlisted platter")

    assert result.data == {"items": [], "has_more": False}
    assert result.grounding.exact_customer_text == (
        "I couldn't find a matching available menu item."
    )


def test_descriptive_pepperoni_search_artifact_is_data_driven():
    result = service([
        item(
            "matching-record",
            name="Configured Savory Pie",
            description="A menu record with pepperoni and herbs.",
            price=25,
        ),
        item(
            "other-record",
            name="Configured Garden Pie",
            description="A menu record with vegetables.",
            price=20,
        ),
    ]).search_menu(query="something with pepperoni")

    assert [entry["product_id"] for entry in result.data["items"]] == [
        "matching-record"
    ]
    assert "Configured Savory Pie" in result.grounding.exact_customer_text
    assert "Configured Garden Pie" not in result.grounding.exact_customer_text


def test_search_menu_excludes_items_already_shown():
    menu = service([
        item(f"item-{index}", score=index, starting_price=10 + index)
        for index in range(7)
    ])

    first = menu.search_menu(limit=5)
    shown = [entry["product_id"] for entry in first.data["items"]]
    second = menu.search_menu(limit=5, exclude_product_ids=shown)

    assert [entry["product_id"] for entry in second.data["items"]] == [
        "item-1", "item-0"
    ]
    assert second.data["has_more"] is False


def test_search_menu_exact_text_tracks_cumulative_and_repeated_exclusions():
    menu = MenuService(
        MemoryMenuRepository([
            item(f"item-{index}", name=f"Item {index}", score=index, price=10 + index)
            for index in range(5)
        ], []),
        customer_result_limit=2,
    )

    first = menu.search_menu()
    first_ids = [entry["product_id"] for entry in first.data["items"]]
    second = menu.search_menu(
        exclude_product_ids=[*first_ids, *first_ids],
    )
    second_ids = [entry["product_id"] for entry in second.data["items"]]
    third = menu.search_menu(
        exclude_product_ids=[*first_ids, *second_ids, *first_ids],
    )
    third_ids = [entry["product_id"] for entry in third.data["items"]]
    exhausted = menu.search_menu(
        exclude_product_ids=[*first_ids, *second_ids, *third_ids, *second_ids],
    )

    assert first_ids == ["item-4", "item-3"]
    assert second_ids == ["item-2", "item-1"]
    assert third_ids == ["item-0"]
    assert second.data["has_more"] is True
    assert third.data["has_more"] is False
    assert second.grounding.exact_customer_text == (
        "Here are the current menu options I found:\n"
        "1. Item 2 - CUR 12\n"
        "2. Item 1 - CUR 11\n"
        "More matching items are available. "
        "Would you like to see more, or choose one of these?"
    )
    assert third.grounding.exact_customer_text == (
        "Here are the current menu options I found:\n"
        "1. Item 0 - CUR 10\n"
        "These are the last matching items.\n"
        "Which item would you like?"
    )
    assert exhausted.data == {"items": [], "has_more": False}
    assert exhausted.grounding.exact_customer_text == (
        "There are no more matching menu items to show."
    )


def test_get_menu_item_exact_customer_text_uses_returned_item_and_groups():
    configured = item(
        "configured",
        name="Configured Pizza",
        starting_price=15,
    )
    configured["customization_group_ids"] = ["size"]
    menu = MenuService(MemoryMenuRepository([configured], [{
        "option_group_id": "size",
        "name": "Size",
        "type": "single",
        "required": True,
        "question": "Which size?",
        "options": [
            {"option_id": "small", "name": "Small", "available": True},
            {"option_id": "large", "name": "Large", "available": True},
        ],
        "min_select": 1,
        "max_select": 1,
    }]))

    result = menu.get_menu_item("configured")

    assert result.grounding.exact_customer_text == (
        "Configured Pizza - from CUR 15\n"
        "Configured menu description\n"
        "Options:\n"
        "Size: Small, Large"
    )


def test_search_menu_options_exact_customer_text_uses_returned_occurrences():
    configured = item("configured", name="Configured Pizza", price=20)
    configured["customization_group_ids"] = ["toppings"]
    menu = MenuService(MemoryMenuRepository([configured], [{
        "option_group_id": "toppings",
        "name": "Toppings",
        "type": "multiple",
        "required": False,
        "question": "Choose toppings",
        "options": [{
            "option_id": "extra-cheese",
            "name": "Extra Cheese",
            "price_delta": 3,
            "available": True,
        }],
        "min_select": 0,
        "max_select": 3,
    }]))

    result = menu.search_menu_options("Extra Cheese")

    assert result.grounding.exact_customer_text == (
        "I found this choice in the current menu:\n"
        "- Extra Cheese: Toppings for Configured Pizza"
    )


def test_search_menu_options_ignores_choice_without_available_host_item():
    menu = MenuService(MemoryMenuRepository([], [{
        "option_group_id": "orphan-group",
        "name": "Extras",
        "type": "multiple",
        "required": False,
        "question": "Choose extras",
        "options": [{
            "option_id": "orphan-option",
            "name": "Configured Choice",
            "available": True,
        }],
        "min_select": 0,
        "max_select": 1,
    }]))

    result = menu.search_menu_options("Configured Choice")

    assert result.data == {"occurrences": []}
    assert result.grounding.exact_customer_text == result.user_message
    assert "Configured Choice" not in result.grounding.exact_customer_text


def test_search_menu_exact_customer_text_does_not_expose_internal_item_id():
    configured = item("private-product-id", name="Temporary")
    configured["name"] = " "

    result = service([configured]).search_menu()

    assert result.grounding.exact_customer_text == result.user_message
    assert result.grounding.offered_options == []
    assert "private-product-id" not in result.grounding.exact_customer_text


def test_get_menu_item_exact_customer_text_does_not_expose_internal_ids():
    configured = item("private-product-id", name="Temporary")
    configured["name"] = " "
    configured["customization_group_ids"] = ["private-group-id"]
    menu = MenuService(MemoryMenuRepository([configured], [{
        "option_group_id": "private-group-id",
        "name": " ",
        "type": "single",
        "required": True,
        "question": "Choose",
        "options": [
            {"option_id": "private-option-id", "name": " ", "available": True},
        ],
        "min_select": 1,
        "max_select": 1,
    }]))

    result = menu.get_menu_item("private-product-id")

    assert result.grounding.exact_customer_text == result.user_message
    assert "private-product-id" not in result.grounding.exact_customer_text
    assert "private-group-id" not in result.grounding.exact_customer_text
    assert "private-option-id" not in result.grounding.exact_customer_text


def test_search_menu_options_exact_customer_text_does_not_expose_internal_ids():
    configured = item("private-product-id", name="Temporary")
    configured["name"] = " "
    configured["customization_group_ids"] = ["private-group-id"]
    menu = MenuService(MemoryMenuRepository([configured], [{
        "option_group_id": "private-group-id",
        "name": " ",
        "type": "multiple",
        "required": False,
        "question": "Choose",
        "options": [{
            "option_id": "private-option-id",
            "name": " ",
            "available": True,
        }],
        "min_select": 0,
        "max_select": 1,
    }]))

    result = menu.search_menu_options("private-option-id")

    assert result.grounding.exact_customer_text == result.user_message
    assert "private-product-id" not in result.grounding.exact_customer_text
    assert "private-group-id" not in result.grounding.exact_customer_text
    assert "private-option-id" not in result.grounding.exact_customer_text


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


def test_search_combines_category_and_tag_for_primary_drink_products():
    result = service([
        item(
            "pizza-deal",
            name="Pizza Deal",
            description="Pizza deal with drinks.",
            category="deals",
            tags=["deal", "drink"],
        ),
        item(
            "pepsi",
            name="PEPSI",
            category="drinks-and-extras",
            tags=["drink"],
        ),
        item(
            "ranch-dip",
            name="Ranch Dip",
            category="drinks-and-extras",
            tags=["extra"],
        ),
    ]).search_menu(category="drinks-and-extras", tags=["drink"])

    assert [entry["product_id"] for entry in result.data["items"]] == ["pepsi"]


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


def test_search_matches_natural_plural_category_query():
    result = service([
        item("cola", name="Cola", category="drink"),
        item("pizza", name="Pizza", category="pizza"),
    ]).search_menu(query="drinks")

    assert [entry["product_id"] for entry in result.data["items"]] == ["cola"]


def test_unknown_specific_query_does_not_fall_back_to_broad_menu():
    result = service([
        item("pizza", name="Pizza", category="pizza"),
        item("cola", name="Cola", category="drink"),
    ]).search_menu(query="unlisted platter")

    assert result.data["items"] == []


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


def test_unavailable_audit_results_are_never_presented_as_customer_choices():
    archived = item("private-archived", name="Archived Item", available=True)
    archived["archived"] = True

    result = service([
        item("private-unavailable", name="Unavailable Item", available=False),
        archived,
    ]).search_menu(available_only=False)

    assert [entry["product_id"] for entry in result.data["items"]] == [
        "private-unavailable"
    ]
    assert result.user_message == (
        "I found matching menu items, but they are currently unavailable."
    )
    assert result.grounding.exact_customer_text == (
        "Here are the matching menu items I found:\n"
        "1. Unavailable Item (currently unavailable)\n"
        "These matching menu items are currently unavailable.\n"
        "Would you like me to search for an available alternative?"
    )
    assert result.grounding.offered_options == []
    assert "Archived Item" not in result.grounding.exact_customer_text


def test_unfiltered_has_more_copy_does_not_imply_remaining_items_are_orderable():
    menu = MenuService(
        MemoryMenuRepository([
            item("available", name="Available Item", available=True, score=10),
            item("unavailable", name="Unavailable Item", available=False, score=0),
        ], []),
        customer_result_limit=1,
    )

    result = menu.search_menu(available_only=False)

    assert result.data["has_more"] is True
    assert "There are more matching menu items to show." in (
        result.grounding.exact_customer_text
    )
    assert "More matching items are available" not in (
        result.grounding.exact_customer_text
    )


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
