import re
from decimal import Decimal

from src.models.tool_responses import ToolResponse


class MenuService:
    def __init__(self, repository):
        self.repository = repository

    def search_menu(self, query=None, category=None, tags=None, max_price=None,
                    available_only=True, limit=None) -> ToolResponse:
        items = self.repository.search(available_only=available_only)
        normalized_query = query.casefold() if query else None
        required_tags = {tag.casefold() for tag in tags or []}
        query_terms = set(self._tokens(normalized_query)) if normalized_query else set()
        matches = []
        for item in items:
            metadata = item.get("metadata") or {}
            searchable_values = [
                item.get("name", ""), item.get("description", ""),
                item.get("category", ""), item.get("source_category", ""),
                *item.get("tags", []), *metadata.get("best_for", []),
            ]
            searchable = " ".join(str(value) for value in searchable_values).casefold()
            if normalized_query:
                searchable_terms = set(self._tokens(searchable))
                if normalized_query not in searchable and not query_terms.intersection(
                    searchable_terms
                ):
                    continue
            if category and item.get("category", "").casefold() != category.casefold():
                continue
            item_match_terms = {
                str(value).casefold()
                for value in [*item.get("tags", []), *metadata.get("best_for", [])]
            }
            if required_tags and not required_tags.issubset(item_match_terms):
                continue
            effective_price = self._effective_price(item)
            if max_price is not None and effective_price is not None:
                if effective_price > Decimal(str(max_price)):
                    continue
            matches.append(self._public_item(item))
        matches.sort(key=self._recommendation_sort_key)
        if limit is not None:
            matches = matches[:limit]
        return ToolResponse.ok(
            data={"items": matches},
            user_message=("I found current menu options." if matches
                          else "I couldn't find a matching available menu item."),
            next_action="present_menu_results",
        )

    def get_menu_item(self, item_id: str) -> ToolResponse:
        item = self.repository.get_item(item_id)
        if not item:
            return ToolResponse.error(error_code="ITEM_NOT_FOUND",
                                      user_message="I couldn't find that menu item.")
        if not item.get("available", False):
            return ToolResponse.error(error_code="ITEM_UNAVAILABLE",
                                      user_message="That item is currently unavailable.")
        groups = []
        for group_id in item.get("customization_group_ids", []):
            group = self.repository.get_option_group(group_id)
            if group:
                groups.append(self._public_group(group))
        result = self._public_item(item)
        result["customization_groups"] = groups
        return ToolResponse.ok(data={"item": result}, user_message="Here are the current item details.",
                               next_action="present_item")

    @staticmethod
    def _public_item(item):
        allowed = ("product_id", "name", "description", "category", "currency",
                   "available", "price", "starting_price", "base_prices", "source_category",
                   "requires_customization", "customization_group_ids", "upsell_group_ids",
                   "tags", "image_url", "metadata")
        return {key: item.get(key) for key in allowed if key in item}

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[\w-]+", value.casefold())

    @staticmethod
    def _effective_price(item) -> Decimal | None:
        for field in ("starting_price", "price"):
            value = item.get(field)
            if value is not None:
                return Decimal(str(value))
        values = [Decimal(str(value)) for value in (item.get("base_prices") or {}).values()]
        return min(values) if values else None

    @classmethod
    def _recommendation_sort_key(cls, item):
        metadata = item.get("metadata") or {}
        score = Decimal(str(metadata.get("recommendation_score", 0)))
        price = cls._effective_price(item)
        return (
            -score,
            -int(metadata.get("is_popular", False)),
            -int(item.get("available", False)),
            price if price is not None else Decimal("Infinity"),
        )

    @staticmethod
    def _public_group(group):
        allowed = ("option_group_id", "name", "type", "required", "question", "options",
                   "min_select", "max_select")
        return {key: group.get(key) for key in allowed if key in group}
