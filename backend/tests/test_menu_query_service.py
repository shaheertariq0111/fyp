import pytest

from src.services.menu_query_service import MenuQueryResolver


@pytest.mark.parametrize(
    ("message", "facet", "category", "tags"),
    [
        ("do you have drinks?", "drink", "drinks-and-extras", ("drink",)),
        ("show beverages", "drink", "drinks-and-extras", ("drink",)),
        ("any dessert?", "dessert", None, ("dessert",)),
        ("do you have any desserts?", "dessert", None, ("dessert",)),
        ("recommend me something sweet", "dessert", None, ("dessert",)),
        ("show sides", "side", None, ("side",)),
        ("show add-ons", "addon", None, ("add-on",)),
        ("show extras", "extra", None, ("extra",)),
    ],
)
def test_resolver_maps_allowed_menu_facets(message, facet, category, tags):
    plan = MenuQueryResolver().resolve(message)

    assert plan is not None
    assert plan.mode == "facet_search"
    assert plan.facet == facet
    assert plan.category == category
    assert plan.tags == tags
    assert plan.state_value == f"facet:{facet}"


def test_resolver_preserves_specific_product_query():
    plan = MenuQueryResolver().resolve("do you have choco bread?")

    assert plan is not None
    assert plan.mode == "product_search"
    assert plan.query == "choco bread"
    assert plan.category is None
    assert plan.tags == ()


def test_resolver_does_not_treat_incidental_drink_word_as_facet_request():
    assert MenuQueryResolver().resolve("pizza with a drink") is None


def test_resolver_restores_validated_facet_for_pagination():
    resolver = MenuQueryResolver()

    plan = resolver.from_state_value("facet:dessert")

    assert plan is not None
    assert plan.facet == "dessert"
    assert plan.tags == ("dessert",)
    assert resolver.from_state_value("facet:unknown") is None
