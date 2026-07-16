import re
from datetime import datetime, timezone
from decimal import Decimal

from src.scripts.import_menu import normalize_records
from src.models.tool_responses import ToolResponse


class MenuService:
    def __init__(self, repository, branch_id: str = ""):
        self.repository = repository
        self.branch_id = branch_id

    def search_menu(self, query=None, category=None, tags=None, max_price=None,
                    available_only=True, limit=None) -> ToolResponse:
        items = self.repository.search(available_only=available_only)
        normalized_query = query.casefold() if query else None
        required_tags = {tag.casefold() for tag in tags or []}
        searchable_index = [(item, *self._searchable_text_and_tokens(item)) for item in items]
        corpus_terms = {
            token for _, _, tokens in searchable_index
            for token in tokens
        }
        query_terms = self._menu_relevant_tokens(normalized_query, corpus_terms) if normalized_query else []
        matches = []
        for item, searchable, searchable_tokens in searchable_index:
            metadata = item.get("metadata") or {}
            match_score = 0
            if normalized_query:
                searchable_terms = set(searchable_tokens)
                match_score = self._query_match_score(normalized_query, query_terms, searchable,
                                                      searchable_tokens, searchable_terms)
                if match_score == 0:
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
            matches.append((match_score, self._public_item(item)))
        matches.sort(key=lambda entry: (-entry[0], self._recommendation_sort_key(entry[1])))
        matches = [item for _, item in matches]
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
        if item.get("archived") or not item.get("available", False):
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

    def admin_list_entities(self, entity_type: str) -> dict:
        self._validate_entity_type(entity_type)
        return {"items": sorted(
            self.repository.list_entities(entity_type),
            key=lambda item: str(item.get("name") or item.get("product_id") or item.get("category_id")
                                 or item.get("option_group_id") or item.get("upsell_group_id") or ""),
        )}

    def admin_get_entity(self, entity_type: str, entity_id: str) -> dict:
        self._validate_entity_type(entity_type)
        item = self.repository.get_entity(entity_type, entity_id)
        if not item:
            raise ValueError("MENU_ENTITY_NOT_FOUND")
        return {"item": item}

    def admin_save_menu_item(self, payload: dict, *, existing_id: str | None = None) -> dict:
        product_id = payload["product_id"]
        if existing_id and existing_id != product_id:
            raise ValueError("PRODUCT_ID_CANNOT_CHANGE")
        if not any(payload.get(field) is not None and payload.get(field) != {}
                   for field in ("price", "starting_price", "base_prices")):
            raise ValueError("MENU_ITEM_PRICE_REQUIRED")
        existing = self.repository.get_item(product_id)
        record = {
            "entity_type": "menu_item",
            **payload,
            "archived": bool((existing or {}).get("archived", False)),
        }
        if existing and existing.get("created_at"):
            record["created_at"] = existing["created_at"]
        normalized = self._normalize_one(record)
        self.repository.save_entity(normalized)
        return {"item": normalized}

    def admin_set_item_availability(self, item_id: str, available: bool) -> dict:
        item = self.repository.get_item(item_id)
        if not item:
            raise ValueError("MENU_ITEM_NOT_FOUND")
        item["available"] = available
        item["updated_at"] = self._now()
        self.repository.save_entity(item)
        return {"item": item}

    def admin_archive_item(self, item_id: str) -> dict:
        item = self.repository.get_item(item_id)
        if not item:
            raise ValueError("MENU_ITEM_NOT_FOUND")
        item["available"] = False
        item["archived"] = True
        item["updated_at"] = self._now()
        self.repository.save_entity(item)
        return {"item": item}

    def admin_save_category(self, payload: dict, *, existing_id: str | None = None) -> dict:
        category_id = payload["category_id"]
        if existing_id and existing_id != category_id:
            raise ValueError("CATEGORY_ID_CANNOT_CHANGE")
        existing = self.repository.get_entity("category", category_id)
        record = {"entity_type": "category", **payload}
        if existing and existing.get("created_at"):
            record["created_at"] = existing["created_at"]
        normalized = self._normalize_one(record)
        self.repository.save_entity(normalized)
        return {"item": normalized}

    def admin_save_option_group(self, payload: dict, *, existing_id: str | None = None) -> dict:
        group_id = payload["option_group_id"]
        if existing_id and existing_id != group_id:
            raise ValueError("OPTION_GROUP_ID_CANNOT_CHANGE")
        existing = self.repository.get_entity("option_group", group_id)
        record = {"entity_type": "option_group", **payload}
        if existing and existing.get("created_at"):
            record["created_at"] = existing["created_at"]
        normalized = self._normalize_one(record)
        self.repository.save_entity(normalized)
        return {"item": normalized}

    def admin_save_upsell_group(self, payload: dict, *, existing_id: str | None = None) -> dict:
        group_id = payload["upsell_group_id"]
        if existing_id and existing_id != group_id:
            raise ValueError("UPSELL_GROUP_ID_CANNOT_CHANGE")
        existing = self.repository.get_entity("upsell_group", group_id)
        record = {"entity_type": "upsell_group", **payload}
        if existing and existing.get("created_at"):
            record["created_at"] = existing["created_at"]
        normalized = self._normalize_one(record)
        self.repository.save_entity(normalized)
        return {"item": normalized}

    def _normalize_one(self, record: dict) -> dict:
        return next(iter(normalize_records(
            [record],
            self.repository.menu_pk.removeprefix("MENU#"),
            self.branch_id,
        )))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_entity_type(entity_type: str) -> None:
        if entity_type not in {"menu_item", "category", "option_group", "upsell_group"}:
            raise ValueError("INVALID_MENU_ENTITY_TYPE")

    @staticmethod
    def _public_item(item):
        allowed = ("product_id", "name", "description", "category", "currency",
                   "available", "price", "starting_price", "base_prices", "source_category",
                   "requires_customization", "customization_group_ids", "upsell_group_ids",
                   "tags", "image_url", "metadata")
        return {key: item.get(key) for key in allowed if key in item}

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[\w-]+", MenuService._normalize_search_text(value))

    @classmethod
    def _menu_relevant_tokens(cls, value: str, corpus_terms: set[str]) -> list[str]:
        tokens = [token for token in cls._tokens(value) if len(token) > 1 or token.isdigit()]
        return [
            token for token in tokens
            if token in corpus_terms or cls._has_same_length_close_token(token, corpus_terms)
        ]

    @classmethod
    def _searchable_text_and_tokens(cls, item) -> tuple[str, list[str]]:
        metadata = item.get("metadata") or {}
        searchable_values = [
            item.get("name", ""), item.get("description", ""),
            item.get("category", ""), item.get("source_category", ""),
            *item.get("tags", []), *item.get("search_terms", []),
            *metadata.get("best_for", []),
        ]
        searchable = cls._normalize_search_text(" ".join(str(value) for value in searchable_values))
        return searchable, cls._tokens(searchable)

    @staticmethod
    def _normalize_search_text(value: str) -> str:
        normalized = value.casefold()
        normalized = re.sub(r"\b(\d+)\s*(pcs?|pieces?)\b", r"\1 \2", normalized)
        return normalized

    @classmethod
    def _query_match_score(cls, normalized_query: str, query_terms: list[str], searchable: str,
                           searchable_tokens: list[str],
                           searchable_terms: set[str]) -> int:
        if not query_terms:
            return 1
        matched_terms = set()
        fuzzy_matches = 0
        required_terms = set(query_terms)
        for term in required_terms:
            if term in searchable_terms:
                matched_terms.add(term)
            elif cls._has_close_token(term, searchable_terms):
                matched_terms.add(term)
                fuzzy_matches += 1
        if matched_terms != required_terms:
            return 0
        normalized_query = cls._normalize_search_text(normalized_query)
        phrase_bonus = 20 if normalized_query in searchable else 0
        order_bonus = cls._ordered_match_bonus(query_terms, searchable_tokens)
        return phrase_bonus + order_bonus + (len(matched_terms) * 4) - fuzzy_matches

    @classmethod
    def _ordered_match_bonus(cls, query_terms: list[str], searchable_tokens: list[str]) -> int:
        if len(query_terms) < 2:
            return 0
        start_index = -1
        positions = []
        for term in query_terms:
            match_index = next(
                (
                    index for index, token in enumerate(searchable_tokens)
                    if index > start_index and (token == term or cls._is_close_token(term, token))
                ),
                None,
            )
            if match_index is None:
                return 0
            positions.append(match_index)
            start_index = match_index
        span = positions[-1] - positions[0]
        return max(2, 14 - span)

    @staticmethod
    def _has_close_token(term: str, candidates: set[str]) -> bool:
        if len(term) < 4:
            return False
        return any(MenuService._is_close_token(term, candidate) for candidate in candidates)

    @staticmethod
    def _has_same_length_close_token(term: str, candidates: set[str]) -> bool:
        if len(term) < 4:
            return False
        return any(
            len(term) == len(candidate) and MenuService._is_close_token(term, candidate)
            for candidate in candidates
        )

    @staticmethod
    def _is_close_token(term: str, candidate: str) -> bool:
        if abs(len(term) - len(candidate)) > 1:
            return False
        if term == candidate:
            return True
        if len(term) == len(candidate):
            differences = [index for index, pair in enumerate(zip(term, candidate))
                           if pair[0] != pair[1]]
            if len(differences) == 1:
                return True
            if len(differences) == 2:
                first, second = differences
                return (second == first + 1
                        and term[first] == candidate[second]
                        and term[second] == candidate[first])
        shorter, longer = (term, candidate) if len(term) < len(candidate) else (candidate, term)
        left = right = edits = 0
        while left < len(shorter) and right < len(longer):
            if shorter[left] == longer[right]:
                left += 1
                right += 1
            else:
                edits += 1
                if edits > 1:
                    return False
                right += 1
        return True

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
