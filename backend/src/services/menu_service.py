import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.scripts.import_menu import normalize_records
from src.models.tool_responses import (
    GroundingEvidence,
    GroundingOption,
    PresentationConstraints,
    ToolResponse,
)


class MenuService:
    def __init__(
        self,
        repository,
        branch_id: str = "",
        *,
        customer_result_limit: int = 5,
    ):
        self.repository = repository
        self.branch_id = branch_id
        self.customer_result_limit = customer_result_limit

    def search_menu(self, query=None, category=None, tags=None, max_price=None,
                    available_only=True, limit=None, exclude_product_ids=None) -> ToolResponse:
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
            if item.get("archived"):
                continue
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
        excluded = {str(value) for value in exclude_product_ids or [] if value}
        is_continuation = bool(excluded)
        if excluded:
            matches = [
                item for item in matches
                if str(item.get("product_id")) not in excluded
            ]
        total_matches = len(matches)
        if limit is not None:
            matches = matches[:min(limit, self.customer_result_limit)]
        else:
            matches = matches[:self.customer_result_limit]
        has_more = total_matches > len(matches)
        available_matches = [
            item for item in matches if item.get("available") is True
        ]
        offered_matches = [
            item for item in available_matches
            if self._customer_item_name(item) is not None
        ]
        user_message = (
            "I found current menu options."
            if available_matches
            else (
                "I found matching menu items, but they are currently unavailable."
                if matches
                else (
                    "There are no more matching menu items to show."
                    if is_continuation
                    else "I couldn't find a matching available menu item."
                )
            )
        )
        return ToolResponse.ok(
            data={"items": matches, "has_more": has_more},
            user_message=user_message,
            next_action="present_menu_results",
            grounding=GroundingEvidence(
                authoritative_domains=["menu"],
                required_next_effect=(
                    "item_selected" if offered_matches else None
                ),
                offered_options=[
                    GroundingOption(
                        id=str(item["product_id"]),
                        label=str(item.get("name") or item["product_id"]),
                    )
                    for item in offered_matches
                    if item.get("product_id")
                ],
                presentation=PresentationConstraints(
                    max_items=self.customer_result_limit,
                ),
                exact_customer_text=self._search_menu_customer_text(
                    matches,
                    has_more=has_more,
                    availability_filtered=available_only,
                    is_continuation=is_continuation,
                    fallback_text=user_message,
                ),
            ),
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
        user_message = "Here are the current item details."
        return ToolResponse.ok(
            data={"item": result},
            user_message=user_message,
            next_action="present_item",
            grounding=GroundingEvidence(
                authoritative_domains=["menu"],
                exact_customer_text=self._menu_item_customer_text(
                    result,
                    fallback_text=user_message,
                ),
            ),
        )

    def search_menu_options(self, query: str) -> ToolResponse:
        """Find an available choice that may not be a standalone menu item."""
        normalized_query = self._normalize_search_text(query)
        occurrences = []
        menu_items = self.repository.search(available_only=True)
        for group in self.repository.list_entities("option_group"):
            matching_options = [
                option for option in group.get("options", [])
                if option.get("available", True)
                and normalized_query in {
                    self._normalize_search_text(str(option.get("name") or "")),
                    self._normalize_search_text(str(option.get("option_id") or "")),
                }
            ]
            if not matching_options:
                continue
            hosts = [
                self._public_item(item)
                for item in menu_items
                if group.get("option_group_id")
                in (item.get("customization_group_ids") or [])
            ]
            if not hosts:
                continue
            public_group = self._public_group(group)
            for option in matching_options:
                occurrences.append({
                    "option": {
                        key: option.get(key)
                        for key in (
                            "option_id", "name", "product_id", "price_delta", "available"
                        )
                        if key in option
                    },
                    "group": public_group,
                    "menu_items": hosts,
                })
        user_message = (
            "I found that choice in the current menu."
            if occurrences
            else "I couldn't find that item or choice in the current menu."
        )
        return ToolResponse.ok(
            data={"occurrences": occurrences},
            user_message=user_message,
            next_action="present_menu_information",
            grounding=GroundingEvidence(
                authoritative_domains=["menu"],
                exact_customer_text=self._menu_options_customer_text(
                    occurrences,
                    fallback_text=user_message,
                ),
            ),
        )

    def list_menu_categories(self, limit=None) -> ToolResponse:
        categories = [
            self._public_category(category)
            for category in self.repository.list_entities("category")
            if not category.get("archived") and category.get("available", True)
        ]
        if not categories:
            categories = self._categories_from_available_items()
        categories.sort(
            key=lambda category: (
                category.get("sort_order", 999),
                str(category.get("name") or category.get("category_id") or ""),
            )
        )
        effective_limit = min(
            max(1, limit or self.customer_result_limit),
            self.customer_result_limit,
        )
        limited = categories[:effective_limit]
        has_more = len(categories) > len(limited)
        user_message = (
            "I found current menu categories."
            if limited
            else "I couldn't find current menu categories."
        )
        return ToolResponse.ok(
            data={"categories": limited, "has_more": has_more},
            user_message=user_message,
            next_action="present_menu_categories",
            grounding=GroundingEvidence(
                authoritative_domains=["menu"],
                offered_options=[
                    GroundingOption(
                        id=str(category["category_id"]),
                        label=str(category.get("name") or category["category_id"]),
                    )
                    for category in limited
                    if category.get("category_id")
                ],
                presentation=PresentationConstraints(
                    max_items=self.customer_result_limit,
                ),
                exact_customer_text=self._menu_categories_customer_text(
                    limited,
                    has_more=has_more,
                    fallback_text=user_message,
                ),
            ),
        )

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
                   "tags", "image_url", "metadata", "customization_rules")
        return {key: item.get(key) for key in allowed if key in item}

    @staticmethod
    def _public_category(category):
        allowed = ("category_id", "name", "description", "sort_order")
        return {key: category.get(key) for key in allowed if key in category}

    def _categories_from_available_items(self) -> list[dict]:
        categories = {}
        for item in self.repository.search(available_only=True):
            category_id = str(item.get("category") or "").strip()
            if not category_id:
                continue
            category_name = (
                str(item.get("source_category") or "").strip()
                or category_id.replace("-", " ").replace("_", " ").title()
            )
            categories.setdefault(
                category_id,
                {
                    "category_id": category_id,
                    "name": category_name,
                    "sort_order": 999,
                },
            )
        return list(categories.values())

    @classmethod
    def _menu_categories_customer_text(
        cls,
        categories: list[dict],
        *,
        has_more: bool,
        fallback_text: str,
    ) -> str:
        lines = []
        for category in categories:
            name = cls._customer_name(category.get("name"))
            if name:
                lines.append(f"{len(lines) + 1}. {name}")
        if not lines:
            return fallback_text
        response = "You can start with these menu categories:\n" + "\n".join(lines)
        if has_more:
            return response + "\nThere are more categories available too. Which one sounds good?"
        return response + "\nWhich one sounds good?"

    @classmethod
    def _search_menu_customer_text(
        cls,
        items: list[dict],
        *,
        has_more: bool,
        availability_filtered: bool,
        is_continuation: bool,
        fallback_text: str,
    ) -> str:
        if not items:
            return fallback_text
        availability = [item.get("available") for item in items]
        if any(value not in (True, False) for value in availability):
            return fallback_text
        item_lines = [cls._customer_item_line(item) for item in items]
        if any(line is None for line in item_lines):
            return fallback_text
        has_available = any(availability)
        has_unavailable = not all(availability)
        lines = [
            "Here are the matching menu items I found:"
            if has_unavailable
            else "Here are the current menu options I found:"
        ]
        lines.extend(
            f"{index}. {line}"
            for index, line in enumerate(item_lines, start=1)
        )
        if has_more:
            if has_unavailable or not availability_filtered:
                lines.append("There are more matching menu items to show.")
                lines.append(
                    "Would you like to see more, or choose an available item shown here?"
                    if has_available
                    else "Would you like to see more?"
                )
            else:
                lines.append(
                    "More matching items are available. "
                    "Would you like to see more, or choose one of these?"
                )
        elif not has_available:
            lines.append("These matching menu items are currently unavailable.")
            lines.append("Would you like me to search for an available alternative?")
        elif is_continuation:
            lines.append("These are the last matching items.")
            lines.append(
                "Which available item would you like?"
                if has_unavailable
                else "Which item would you like?"
            )
        else:
            lines.append("Those are all the matching items I found.")
            lines.append(
                "Which available item would you like?"
                if has_unavailable
                else "Which item would you like?"
            )
        return "\n".join(lines)

    @classmethod
    def _menu_item_customer_text(cls, item: dict, *, fallback_text: str) -> str:
        item_line = cls._customer_item_line(item)
        if item_line is None:
            return fallback_text
        lines = [item_line]
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            lines.append(description.strip())

        option_lines = []
        for group in item.get("customization_groups") or []:
            if not isinstance(group, dict):
                continue
            option_names = []
            for option in group.get("options") or []:
                if not isinstance(option, dict) or not option.get("available", True):
                    continue
                option_name = cls._customer_name(option.get("name"))
                if option_name:
                    option_names.append(option_name)
            if not option_names:
                continue
            group_name = cls._customer_name(group.get("name"))
            option_lines.append(
                f"{group_name}: {', '.join(option_names)}"
                if group_name
                else ", ".join(option_names)
            )
        if option_lines:
            lines.append("Options:")
            lines.extend(option_lines)
        return "\n".join(lines)

    @classmethod
    def _menu_options_customer_text(
        cls,
        occurrences: list[dict],
        *,
        fallback_text: str,
    ) -> str:
        if not occurrences:
            return fallback_text
        option_lines = []
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                continue
            option = occurrence.get("option") or {}
            group = occurrence.get("group") or {}
            if not isinstance(option, dict) or not isinstance(group, dict):
                continue
            option_name = cls._customer_name(option.get("name"))
            if not option_name:
                continue
            group_name = cls._customer_name(group.get("name"))
            host_names = []
            for item in occurrence.get("menu_items") or []:
                if not isinstance(item, dict):
                    continue
                host_name = cls._customer_item_name(item)
                if host_name:
                    host_names.append(host_name)
            if group_name and host_names:
                context = f"{group_name} for {', '.join(host_names)}"
            elif group_name:
                context = group_name
            elif host_names:
                context = f"available for {', '.join(host_names)}"
            else:
                context = None
            option_lines.append(
                f"- {option_name}: {context}" if context else f"- {option_name}"
            )
        if not option_lines:
            return fallback_text
        return "\n".join([
            "I found this choice in the current menu:",
            *option_lines,
        ])

    @staticmethod
    def _customer_name(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @classmethod
    def _customer_item_name(cls, item: dict) -> str | None:
        return cls._customer_name(item.get("name"))

    @classmethod
    def _customer_item_line(cls, item: dict) -> str | None:
        name = cls._customer_item_name(item)
        if name is None:
            return None
        price = cls._customer_price_label(item)
        line = f"{name} - {price}" if price else name
        if item.get("available") is False:
            return f"{line} (currently unavailable)"
        return line

    @staticmethod
    def _customer_price_label(item: dict) -> str | None:
        currency = str(item.get("currency") or "").strip()
        if item.get("starting_price") is not None:
            amount = " ".join(
                part
                for part in (currency, str(item["starting_price"]))
                if part
            )
            return f"from {amount}"
        if item.get("price") is not None:
            return " ".join(
                part for part in (currency, str(item["price"])) if part
            )
        base_prices = item.get("base_prices")
        if isinstance(base_prices, dict):
            prices = []
            for value in base_prices.values():
                if value is None:
                    continue
                try:
                    numeric_value = Decimal(str(value))
                except (InvalidOperation, ValueError):
                    continue
                if numeric_value.is_finite():
                    prices.append((numeric_value, value))
            if prices:
                minimum = min(prices, key=lambda entry: entry[0])[1]
                amount = " ".join(
                    part for part in (currency, str(minimum)) if part
                )
                return f"from {amount}"
        return None

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[\w-]+", MenuService._normalize_search_text(value))

    @classmethod
    def _menu_relevant_tokens(cls, value: str, corpus_terms: set[str]) -> list[str]:
        tokens = [token for token in cls._tokens(value) if len(token) > 1 or token.isdigit()]
        return [
            token for token in tokens
            if token in corpus_terms or cls._has_close_token(token, corpus_terms)
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
            return 0
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
