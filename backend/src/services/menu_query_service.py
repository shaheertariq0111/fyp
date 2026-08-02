from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MenuSearchPlan:
    mode: str
    query: str | None = None
    category: str | None = None
    tags: tuple[str, ...] = ()
    facet: str | None = None

    @property
    def state_value(self) -> str:
        if self.mode == "facet_search" and self.facet:
            return f"facet:{self.facet}"
        return self.query or ""


class MenuQueryResolver:
    """Resolve customer menu wording into backend-validated search inputs."""

    _FACETS = {
        "dessert": {"tags": ("dessert",)},
        "drink": {
            "category": "drinks-and-extras",
            "tags": ("drink",),
        },
        "side": {"tags": ("side",)},
        "addon": {"tags": ("add-on",)},
        "extra": {"tags": ("extra",)},
    }
    _ALIASES = {
        "dessert": "dessert",
        "desserts": "dessert",
        "sweet": "dessert",
        "sweets": "dessert",
        "something sweet": "dessert",
        "drink": "drink",
        "drinks": "drink",
        "beverage": "drink",
        "beverages": "drink",
        "side": "side",
        "sides": "side",
        "addon": "addon",
        "addons": "addon",
        "add on": "addon",
        "add ons": "addon",
        "extras": "extra",
    }
    _BROWSE_PATTERN = re.compile(
        r"^(?:menu|show\s+(?:me\s+)?(?:(?:the|your)\s+)?"
        r"(?:(?:whole|full)\s+)?menu)$"
    )
    _PRODUCT_QUERY_PATTERN = re.compile(
        r"^(?:do\s+you\s+have|have\s+you\s+got|any|"
        r"show\s+(?:me\s+)?|can\s+i\s+(?:get|order))\s+"
        r"(?P<query>.+)$"
    )
    _BLOCKED_PRODUCT_QUERIES = {
        "menu",
        "the menu",
        "your menu",
        "full menu",
        "the full menu",
        "whole menu",
        "the whole menu",
        "more",
        "more items",
        "more options",
    }

    def resolve(self, message: str) -> MenuSearchPlan | None:
        normalized = self._normalize(message)
        if not normalized:
            return None
        if self._BROWSE_PATTERN.fullmatch(normalized):
            return MenuSearchPlan(mode="browse")

        facet = self._facet_from_message(normalized)
        if facet:
            return self._facet_plan(facet)

        product_match = self._PRODUCT_QUERY_PATTERN.fullmatch(normalized)
        if product_match is None:
            return None
        query = re.sub(
            r"^(?:(?:a|an|any|some|the)\s+)+",
            "",
            product_match.group("query"),
        ).strip()
        query = re.sub(
            r"\s+(?:on|from)\s+(?:(?:the|your)\s+)?menu$",
            "",
            query,
        ).strip()
        if not query or query in self._BLOCKED_PRODUCT_QUERIES:
            return None
        return MenuSearchPlan(mode="product_search", query=query)

    def from_state_value(self, value: object) -> MenuSearchPlan | None:
        stored = str(value or "").strip()
        if not stored:
            return MenuSearchPlan(mode="browse")
        if stored.startswith("facet:"):
            return self._facet_plan(stored.removeprefix("facet:"))
        return MenuSearchPlan(mode="product_search", query=stored)

    def _facet_plan(self, facet: str) -> MenuSearchPlan | None:
        definition = self._FACETS.get(facet)
        if definition is None:
            return None
        return MenuSearchPlan(
            mode="facet_search",
            category=definition.get("category"),
            tags=definition.get("tags", ()),
            facet=facet,
        )

    def _facet_from_message(self, normalized: str) -> str | None:
        candidate = re.sub(
            r"^(?:do\s+you\s+have|have\s+you\s+got|show\s+me|show|any|"
            r"recommend\s+me|recommend|suggest\s+me|suggest|i\s+want|"
            r"i\s+would\s+like|can\s+i\s+get|could\s+i\s+get|need|get\s+me)\s+",
            "",
            normalized,
        ).strip()
        candidate = re.sub(
            r"\s+(?:options?|items?|from\s+the\s+menu|on\s+the\s+menu)$",
            "",
            candidate,
        ).strip()
        candidate = re.sub(
            r"^(?:a|an|any|some|something)\s+",
            "",
            candidate,
        ).strip()
        return self._ALIASES.get(candidate)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(
            r"\s+",
            " ",
            re.sub(r"[^a-z0-9\s]", " ", value.casefold()),
        ).strip()
