import pytest

from fakes import MemoryMenuRepository
from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation
from src.services.menu_information_service import MenuInformationService
from src.services.menu_service import MenuService


def item(product_id, name, **overrides):
    return {
        "product_id": product_id,
        "name": name,
        "description": overrides.pop("description", f"Backend description for {name}."),
        "category": overrides.pop("category", "sides"),
        "currency": "PKR",
        "available": True,
        "starting_price": overrides.pop("starting_price", 450),
        "customization_group_ids": overrides.pop("customization_group_ids", []),
        "upsell_group_ids": [],
        "tags": overrides.pop("tags", []),
        **overrides,
    }


ITEMS = [
    item(
        "choco-bread",
        "Choco Bread",
        description="Freshly-baked bread covered with chocolate and icing sugar.",
        tags=["side", "dessert", "vegetarian"],
    ),
    item(
        "lava-cake-1-pc",
        "Lava Cake - 1 Pc",
        description="Milk chocolate cake with a molten center.",
    ),
    item(
        "lava-cake-2-pcs",
        "Lava Cake - 2 Pcs",
        description="Milk chocolate cake with a molten center.",
        starting_price=850,
    ),
    item(
        "only-veggie",
        "Only Veggie",
        category="pizza",
        tags=["pizza", "vegetarian"],
        base_prices={"small": 650, "medium": 1300, "large": 1800},
    ),
    item(
        "chicken-fajita",
        "Chicken Fajita",
        description="Pizza sauce, cheese, spicy chicken, onions and green peppers.",
        category="pizza",
        tags=["pizza", "spicy", "chicken"],
        base_prices={"small": 650, "medium": 1300, "large": 1800},
        customization_group_ids=["pizza-size", "pizza-crust"],
    ),
    item(
        "pepsi",
        "PEPSI",
        description="Refreshing beverage.",
        category="drinks",
        tags=["drink"],
        starting_price=150,
        customization_group_ids=["pepsi-size"],
    ),
    item(
        "epic-medium",
        "Epic Medium",
        description="1 Medium Pizza and 2 Small Drinks.",
        category="deals",
        tags=["combo"],
        starting_price=1500,
        customization_group_ids=["combo-pizza-flavor", "combo-drink-choice"],
        customization_rules={
            "selection_quantities": {
                "combo-pizza-flavor": 1,
                "combo-drink-choice": 2,
            },
            "fixed_size": "medium",
        },
    ),
]

GROUPS = [
    {
        "option_group_id": "pizza-size",
        "name": "Pizza Size",
        "question": "Choose a pizza size.",
        "options": [
            {"option_id": "small", "name": "Small", "available": True},
            {"option_id": "medium", "name": "Medium", "available": True},
            {"option_id": "large", "name": "Large", "available": True},
        ],
    },
    {
        "option_group_id": "pizza-crust",
        "name": "Pizza Crust",
        "question": "Choose a crust.",
        "options": [
            {"option_id": "regular", "name": "Regular Crust", "available": True},
            {"option_id": "thin", "name": "Crunchy Thin Crust", "available": True},
        ],
    },
    {
        "option_group_id": "pepsi-size",
        "name": "Drink Size",
        "question": "Choose a drink size.",
        "options": [
            {"option_id": "345ml", "name": "345 ml", "available": True},
            {"option_id": "500ml", "name": "500 ml", "available": True},
            {"option_id": "1-5l", "name": "1.5 L", "available": True},
        ],
    },
    {
        "option_group_id": "combo-pizza-flavor",
        "name": "Combo Pizza Flavor",
        "question": "Choose a pizza flavor.",
        "options": [
            {
                "option_id": "chicken-fajita",
                "name": "Chicken Fajita",
                "product_id": "chicken-fajita",
                "available": True,
            }
        ],
    },
    {
        "option_group_id": "combo-drink-choice",
        "name": "Combo Drink",
        "question": "Choose a drink.",
        "options": [
            {
                "option_id": "pepsi",
                "name": "Pepsi",
                "product_id": "pepsi",
                "available": True,
            },
            {"option_id": "diet-pepsi", "name": "Diet Pepsi", "available": True},
            {"option_id": "seven-up", "name": "7UP", "available": True},
        ],
    },
]


def service():
    repository = MemoryMenuRepository(ITEMS, GROUPS)
    return MenuInformationService(MenuService(repository))


def interpretation(
    *,
    action="menu_item_detail",
    question_type="contents",
    targets=None,
    facet=None,
):
    return WhatsAppTurnInterpretation(
        action=action,
        confidence=0.97,
        informational_only=True,
        wants_to_order=False,
        question_type=question_type,
        target_items=targets or [],
        facet=facet,
    )


def test_item_detail_uses_backend_description_and_price():
    result = service().answer(interpretation(targets=["choco bread"]))

    assert "Freshly-baked bread covered with chocolate" in result.text
    assert "PKR 450" in result.text
    assert all(read.tool_name in {"search_menu", "get_menu_item"} for read in result.reads)


@pytest.mark.parametrize(
    "message_target",
    ["lava cake"],
)
def test_piece_question_lists_backend_variants(message_target):
    result = service().answer(interpretation(
        question_type="pieces",
        targets=[message_target],
    ))

    assert "Lava Cake - 1 Pc" in result.text
    assert "Lava Cake - 2 Pcs" in result.text
    assert "PKR 450" in result.text
    assert "PKR 850" in result.text


@pytest.mark.parametrize(
    ("facet", "expected", "excluded"),
    [
        ("vegetarian", "Only Veggie", "Chicken Fajita"),
        ("spicy", "Chicken Fajita", "Only Veggie"),
    ],
)
def test_pizza_facet_questions_return_only_backend_tag_matches(facet, expected, excluded):
    result = service().answer(interpretation(
        action="menu_search",
        question_type="dietary" if facet == "vegetarian" else "spice",
        targets=["pizza"],
        facet=facet,
    ))

    assert expected in result.text
    assert excluded not in result.text


def test_combo_contents_come_from_description_and_customization_rules():
    result = service().answer(interpretation(
        question_type="combo_contents",
        targets=["epic medium"],
    ))

    assert "1 Medium Pizza and 2 Small Drinks" in result.text
    assert "Combo Pizza Flavor: choose 1" in result.text
    assert "Combo Drink: choose 2" in result.text


@pytest.mark.parametrize(
    ("target", "question_type", "facet", "expected"),
    [
        ("chicken fajita", "options", "crust", ["Regular Crust", "Crunchy Thin Crust"]),
        ("pepsi", "size", None, ["345 ml", "500 ml", "1.5 L"]),
    ],
)
def test_item_options_come_from_backend_groups(target, question_type, facet, expected):
    result = service().answer(interpretation(
        question_type=question_type,
        targets=[target],
        facet=facet,
    ))

    for label in expected:
        assert label in result.text


def test_item_price_uses_backend_size_prices():
    result = service().answer(interpretation(
        question_type="price",
        targets=["chicken fajita"],
    ))

    assert "Small PKR 650" in result.text
    assert "Medium PKR 1,300" in result.text
    assert "Large PKR 1,800" in result.text


def test_comparison_uses_each_items_backend_description_and_price():
    result = service().answer(interpretation(
        action="menu_compare",
        question_type="contents",
        targets=["choco bread", "lava cake - 1 pc"],
    ))

    assert "Choco Bread" in result.text
    assert "Freshly-baked bread" in result.text
    assert "Lava Cake - 1 Pc" in result.text
    assert "molten center" in result.text


@pytest.mark.parametrize(
    ("target", "action"),
    [("7up", "menu_item_detail"), ("diet pepsi", "menu_search")],
)
def test_combo_only_drinks_are_not_reported_as_standalone_items(target, action):
    result = service().answer(interpretation(
        action=action,
        question_type="availability",
        targets=[target],
    ))

    assert "not listed as a standalone menu item" in result.text
    assert "Combo Drink" in result.text
    assert "Epic Medium" in result.text


def test_ambiguous_item_search_asks_for_clarification():
    result = service().answer(interpretation(targets=["cake"]))

    assert result.text.startswith("Which item did you mean:")
    assert "Lava Cake - 1 Pc" in result.text
    assert "Lava Cake - 2 Pcs" in result.text


def test_non_informational_interpretation_is_rejected():
    unsafe = WhatsAppTurnInterpretation(
        action="checkout",
        confidence=0.99,
        informational_only=False,
        wants_to_order=True,
    )

    with pytest.raises(ValueError, match="informational"):
        service().answer(unsafe)
