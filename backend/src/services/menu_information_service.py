from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation
from src.models.tool_responses import ToolResponse


@dataclass(frozen=True)
class MenuInformationRead:
    tool_name: str
    response: ToolResponse


@dataclass(frozen=True)
class MenuInformationResult:
    text: str
    reads: list[MenuInformationRead] = field(default_factory=list)


@dataclass(frozen=True)
class _ResolvedItems:
    items: list[dict[str, Any]]
    reads: list[MenuInformationRead]
    clarification: str | None = None


class MenuInformationService:
    """Answer menu questions using read-only authoritative menu services."""

    def __init__(self, menu) -> None:
        self.menu = menu

    def answer(
        self,
        interpretation: WhatsAppTurnInterpretation,
    ) -> MenuInformationResult:
        if not interpretation.informational_only:
            raise ValueError("Menu information requires an informational interpretation")

        if interpretation.action == "menu_compare":
            return self._compare(interpretation)
        if self._first_target(interpretation) and not interpretation.facet and (
            interpretation.action == "menu_item_detail"
            or interpretation.question_type in {
                "price",
                "contents",
                "quantity",
                "pieces",
                "ingredients",
                "options",
                "size",
                "combo_contents",
                "availability",
            }
        ):
            return self._item_answer(interpretation)
        if interpretation.action in {
            "menu_browse",
            "menu_search",
            "menu_recommendation",
        }:
            return self._list_matches(interpretation)
        return self._item_answer(interpretation)

    def _list_matches(
        self,
        interpretation: WhatsAppTurnInterpretation,
    ) -> MenuInformationResult:
        target = self._first_target(interpretation)
        response = self.menu.search_menu(
            query=target,
            tags=[interpretation.facet] if interpretation.facet else None,
            available_only=True,
            limit=10,
            exclude_product_ids=[],
        )
        reads = [MenuInformationRead("search_menu", response)]
        items = (response.data or {}).get("items", []) if response.success else []
        if not items:
            return self._missing_item(target, reads)
        return MenuInformationResult(self._menu_list(items), reads)

    def _item_answer(
        self,
        interpretation: WhatsAppTurnInterpretation,
    ) -> MenuInformationResult:
        target = self._first_target(interpretation)
        resolved = self._resolve_items(
            target,
            include_variants=interpretation.question_type in {
                "pieces",
                "quantity",
                "size",
            },
        )
        if resolved.clarification:
            return MenuInformationResult(resolved.clarification, resolved.reads)
        if not resolved.items:
            return self._missing_item(target, resolved.reads)

        details, detail_reads = self._load_details(resolved.items)
        reads = [*resolved.reads, *detail_reads]
        question_type = interpretation.question_type
        if question_type in {"pieces", "quantity"}:
            text = self._pieces_answer(details)
        elif question_type in {"options", "size"}:
            text = self._options_answer(
                details,
                question_type=question_type,
                facet=interpretation.facet,
            )
        elif question_type in {"contents", "combo_contents", "ingredients"}:
            text = self._contents_answer(details, question_type=question_type)
        elif question_type in {"dietary", "spice"}:
            text = self._labels_answer(details, question_type=question_type)
        elif question_type == "availability":
            text = self._availability_answer(details)
        else:
            text = self._detail_answer(details)
        return MenuInformationResult(text, reads)

    def _compare(
        self,
        interpretation: WhatsAppTurnInterpretation,
    ) -> MenuInformationResult:
        reads: list[MenuInformationRead] = []
        compared: list[dict[str, Any]] = []
        for target in interpretation.target_items:
            resolved = self._resolve_items(target, include_variants=False)
            reads.extend(resolved.reads)
            if resolved.clarification:
                return MenuInformationResult(resolved.clarification, reads)
            if not resolved.items:
                missing = self._missing_item(target, reads)
                return missing
            details, detail_reads = self._load_details(resolved.items[:1])
            reads.extend(detail_reads)
            compared.extend(details)
        lines = ["Here is a comparison from the current menu:"]
        for item in compared:
            lines.append(f"- {self._item_summary(item)}")
        return MenuInformationResult("\n".join(lines), reads)

    def _resolve_items(self, target: str, *, include_variants: bool) -> _ResolvedItems:
        response = self.menu.search_menu(
            query=target,
            available_only=True,
            limit=None,
            exclude_product_ids=[],
        )
        reads = [MenuInformationRead("search_menu", response)]
        matches = (response.data or {}).get("items", []) if response.success else []
        if not matches:
            return _ResolvedItems([], reads)

        normalized_target = self._normalize(target)
        exact = [
            item for item in matches
            if self._normalize(str(item.get("name") or "")) == normalized_target
        ]
        if exact:
            return _ResolvedItems(exact, reads)

        variants = [
            item for item in matches
            if self._variant_base(str(item.get("name") or "")) == normalized_target
        ]
        if variants and (include_variants or len(variants) == 1):
            return _ResolvedItems(variants, reads)

        candidates = variants or matches
        if len(candidates) == 1:
            return _ResolvedItems([], reads)
        names = self._unique_names(candidates[:5])
        return _ResolvedItems(
            [],
            reads,
            "Which item did you mean: " + ", ".join(names) + "?",
        )

    def _load_details(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[MenuInformationRead]]:
        get_menu_item = getattr(self.menu, "get_menu_item", None)
        if get_menu_item is None:
            return items, []
        details = []
        reads = []
        for item in items:
            product_id = item.get("product_id")
            if not product_id:
                details.append(item)
                continue
            response = get_menu_item(str(product_id))
            reads.append(MenuInformationRead("get_menu_item", response))
            details.append(
                ((response.data or {}).get("item") or item)
                if response.success
                else item
            )
        return details, reads

    def _missing_item(
        self,
        target: str,
        reads: list[MenuInformationRead],
    ) -> MenuInformationResult:
        search_options = getattr(self.menu, "search_menu_options", None)
        if search_options is not None and target:
            response = search_options(target)
            reads = [*reads, MenuInformationRead("search_menu_options", response)]
            occurrences = (
                (response.data or {}).get("occurrences", [])
                if response.success
                else []
            )
            if occurrences:
                option_name = str(
                    (occurrences[0].get("option") or {}).get("name") or target
                )
                contexts = []
                for occurrence in occurrences:
                    group_name = str(
                        (occurrence.get("group") or {}).get("name") or "menu choice"
                    )
                    hosts = [
                        str(item.get("name"))
                        for item in occurrence.get("menu_items", [])
                        if item.get("name")
                    ]
                    contexts.append(
                        f"{group_name} for {', '.join(hosts)}"
                        if hosts
                        else group_name
                    )
                return MenuInformationResult(
                    f"{option_name} is not listed as a standalone menu item, "
                    f"but it is available as a {'; '.join(contexts)} option.",
                    reads,
                )
        label = target or "that item"
        return MenuInformationResult(
            f"I couldn't find {label} in the current available menu.",
            reads,
        )

    @classmethod
    def _menu_list(cls, items: list[dict[str, Any]]) -> str:
        return "\n".join([
            "Here is what I found in the current menu:",
            *[
                f"{index}. {item.get('name', 'Menu item')} - {cls._price(item)}"
                for index, item in enumerate(items, start=1)
            ],
        ])

    @classmethod
    def _detail_answer(cls, items: list[dict[str, Any]]) -> str:
        return "\n\n".join(cls._item_summary(item) for item in items)

    @classmethod
    def _contents_answer(
        cls,
        items: list[dict[str, Any]],
        *,
        question_type: str,
    ) -> str:
        sections = []
        for item in items:
            description = str(item.get("description") or "").strip()
            lines = [f"{item.get('name', 'Menu item')} - {cls._price(item)}"]
            if description:
                lines.append(description)
            else:
                lines.append("The current menu does not provide a detailed description.")
            if question_type == "combo_contents":
                groups = item.get("customization_groups") or []
                quantities = (item.get("customization_rules") or {}).get(
                    "selection_quantities", {}
                )
                for group in groups:
                    group_id = group.get("option_group_id")
                    quantity = quantities.get(group_id)
                    choice_text = f"choose {quantity}" if quantity else "choose from"
                    options = cls._option_names(group)
                    if options:
                        lines.append(
                            f"{group.get('name', 'Choice')}: {choice_text} "
                            f"{', '.join(options)}."
                        )
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    @classmethod
    def _pieces_answer(cls, items: list[dict[str, Any]]) -> str:
        explicit = [
            item for item in items
            if re.search(r"\b\d+\s*(?:pcs?|pieces?)\b", str(item.get("name") or ""), re.I)
        ]
        if explicit:
            return "\n".join([
                "The current menu has these variants:",
                *[f"- {item.get('name')} - {cls._price(item)}" for item in explicit],
            ])
        description = " ".join(str(item.get("description") or "") for item in items)
        piece_match = re.search(r"\b\d+\s*(?:pcs?|pieces?)\b", description, re.I)
        if piece_match:
            return f"{items[0].get('name')} comes as {piece_match.group(0)}."
        return (
            f"The current menu does not specify a piece count for "
            f"{items[0].get('name', 'that item')}."
        )

    @classmethod
    def _options_answer(
        cls,
        items: list[dict[str, Any]],
        *,
        question_type: str,
        facet: str | None,
    ) -> str:
        item = items[0]
        groups = item.get("customization_groups") or []
        term = "size" if question_type == "size" else cls._normalize(facet or "")
        relevant = [
            group for group in groups
            if not term
            or term in cls._normalize(
                f"{group.get('name', '')} {group.get('question', '')}"
            )
        ]
        if not relevant:
            label = f" {term}" if term else ""
            return (
                f"The current menu does not list{label} options for "
                f"{item.get('name', 'that item')}."
            )
        lines = [f"Available options for {item.get('name', 'this item')}:"]
        for group in relevant:
            options = cls._option_names(group)
            if options:
                lines.append(f"- {group.get('name', 'Choice')}: {', '.join(options)}")
        return "\n".join(lines)

    @classmethod
    def _labels_answer(
        cls,
        items: list[dict[str, Any]],
        *,
        question_type: str,
    ) -> str:
        lines = []
        for item in items:
            metadata = item.get("metadata") or {}
            tags = cls._unique_strings([
                *item.get("tags", []),
                *metadata.get("best_for", []),
            ])
            if question_type == "spice" and metadata.get("spice_level"):
                lines.append(
                    f"{item.get('name')}: spice level "
                    f"{metadata.get('spice_level')}; menu labels: {', '.join(tags)}."
                )
            elif tags:
                lines.append(f"{item.get('name')}: {', '.join(tags)}.")
            else:
                lines.append(
                    f"The current menu does not provide {question_type} labels for "
                    f"{item.get('name', 'that item')}."
                )
        return "\n".join(lines)

    @classmethod
    def _availability_answer(cls, items: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"Yes, {item.get('name')} is currently available - {cls._price(item)}."
            for item in items
        )

    @classmethod
    def _item_summary(cls, item: dict[str, Any]) -> str:
        description = str(item.get("description") or "").strip()
        text = f"{item.get('name', 'Menu item')} - {cls._price(item)}"
        return f"{text}. {description}" if description else text

    @classmethod
    def _price(cls, item: dict[str, Any]) -> str:
        base_prices = item.get("base_prices")
        if isinstance(base_prices, dict) and base_prices:
            return ", ".join(
                f"{str(size).replace('_', ' ').title()} {cls._money(value, item)}"
                for size, value in base_prices.items()
            )
        amount = item.get("starting_price")
        if amount is None:
            amount = item.get("price")
        return cls._money(amount, item)

    @staticmethod
    def _money(amount: Any, item: dict[str, Any]) -> str:
        currency = str(item.get("currency") or "").upper()
        label = "PKR" if currency == "PKR" else currency
        if amount is None:
            return "price unavailable"
        try:
            number = f"{float(amount):,.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            number = str(amount)
        return f"{label} {number}".strip()

    @staticmethod
    def _option_names(group: dict[str, Any]) -> list[str]:
        return [
            str(option.get("name") or option.get("label") or "").strip()
            for option in group.get("options", [])
            if option.get("available", True)
            and (option.get("name") or option.get("label"))
        ]

    @staticmethod
    def _first_target(interpretation: WhatsAppTurnInterpretation) -> str:
        return next(
            (value.strip() for value in interpretation.target_items if value.strip()),
            "",
        )

    @classmethod
    def _variant_base(cls, value: str) -> str:
        return cls._normalize(
            re.sub(r"\s*-?\s*\d+\s*(?:pcs?|pieces?)\s*$", "", value, flags=re.I)
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.casefold())).strip()

    @staticmethod
    def _unique_names(items: list[dict[str, Any]]) -> list[str]:
        return MenuInformationService._unique_strings(
            [str(item.get("name")) for item in items if item.get("name")]
        )

    @staticmethod
    def _unique_strings(values: list[Any]) -> list[str]:
        seen = set()
        unique = []
        for value in values:
            text = str(value).strip()
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                unique.append(text)
        return unique
