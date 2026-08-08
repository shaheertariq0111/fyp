from __future__ import annotations

from dataclasses import dataclass

from src.agent.whatsapp_turn_intent import WhatsAppTurnInterpretation


INFORMATIONAL_ACTIONS = {
    "menu_browse",
    "menu_search",
    "menu_item_detail",
    "menu_compare",
    "menu_recommendation",
}
TRANSACTIONAL_ACTIONS = {
    "select_menu_item",
    "answer_customization_step",
    "checkout",
    "cancel_cart",
    "transactional_change",
}
SELECTION_ACTIONS = {"select_menu_item", "answer_customization_step"}


@dataclass(frozen=True)
class WhatsAppTurnPolicyDecision:
    accepted: bool
    reason: str


class WhatsAppTurnPolicyService:
    """Validate classifier output before it can influence backend routing."""

    def __init__(self, confidence_threshold: float = 0.85) -> None:
        self.confidence_threshold = confidence_threshold

    def validate(
        self,
        interpretation: WhatsAppTurnInterpretation,
        *,
        allowed_actions: list[str],
        available_options: list[dict[str, str]] | None = None,
    ) -> WhatsAppTurnPolicyDecision:
        action = interpretation.action
        if interpretation.confidence < self.confidence_threshold:
            return WhatsAppTurnPolicyDecision(False, "low_confidence")
        if action not in allowed_actions:
            return WhatsAppTurnPolicyDecision(False, "action_not_allowed")
        if action in INFORMATIONAL_ACTIONS and (
            not interpretation.informational_only or interpretation.wants_to_order
        ):
            return WhatsAppTurnPolicyDecision(False, "invalid_informational_flags")
        if action in TRANSACTIONAL_ACTIONS and (
            interpretation.informational_only or not interpretation.wants_to_order
        ):
            return WhatsAppTurnPolicyDecision(False, "invalid_transactional_flags")
        if action not in INFORMATIONAL_ACTIONS | TRANSACTIONAL_ACTIONS and (
            interpretation.informational_only or interpretation.wants_to_order
        ):
            return WhatsAppTurnPolicyDecision(False, "invalid_neutral_flags")

        option_ids = {
            str(option.get("id"))
            for option in available_options or []
            if option.get("id") is not None
        }
        if interpretation.selected_option is not None and (
            interpretation.selected_option not in option_ids
        ):
            return WhatsAppTurnPolicyDecision(False, "invalid_selected_option")
        if action in SELECTION_ACTIONS and not interpretation.selected_option:
            return WhatsAppTurnPolicyDecision(False, "selected_option_required")
        if action not in SELECTION_ACTIONS and interpretation.selected_option is not None:
            return WhatsAppTurnPolicyDecision(False, "selected_option_not_allowed")
        if action == "menu_item_detail" and not interpretation.target_items:
            return WhatsAppTurnPolicyDecision(False, "target_item_required")
        if action == "menu_compare" and len(interpretation.target_items) < 2:
            return WhatsAppTurnPolicyDecision(False, "comparison_targets_required")
        return WhatsAppTurnPolicyDecision(True, "accepted")


def whatsapp_no_write_authorization(
    interpretation: WhatsAppTurnInterpretation,
    *,
    allowed_actions: list[str],
    available_options: list[dict[str, str]] | None = None,
) -> tuple[bool, bool]:
    """Return (authorized, informational_read) after strict policy validation."""
    decision = WhatsAppTurnPolicyService().validate(
        interpretation,
        allowed_actions=allowed_actions,
        available_options=available_options,
    )
    if not decision.accepted:
        return False, False
    action = interpretation.action
    authorized = action in INFORMATIONAL_ACTIONS | {"general_chat"}
    informational_read = action in {
        "menu_item_detail",
        "menu_compare",
        "menu_recommendation",
    }
    return authorized, informational_read
