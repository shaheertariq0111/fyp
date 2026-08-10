from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.models.conversation_contracts import OptionContract
from src.services import agent_session_service as agent_session_service_module
from src.services.agent_request_processor import AgentRequestProcessor
from src.services.agent_session_service import AgentSessionService


NOW = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)


def contract(
    contract_id="contract-1",
    *,
    version=1,
    effect="item_selected",
    consumer="start_cart_item_customization",
    option_id="item-1",
    source="search_menu",
):
    return OptionContract(
        contract_id=contract_id,
        contract_version=version,
        required_effect=effect,
        consumer_capability=consumer,
        source_capability=source,
        source_request_id="request-1",
        options=[{"id": option_id, "label": "Choice"}],
        created_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=30)).isoformat(),
    )


class ContractRepository:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.transitions = []
        self.legacy_transitions = []
        self.cleared = False

    def get_whatsapp_order_state(self, *_args):
        return dict(self.state)

    def transition_option_contract(self, _customer, _session, **kwargs):
        self.transitions.append(kwargs)
        successor = kwargs["successor"]
        if successor is None:
            self.state.pop("active_option_contract", None)
        else:
            self.state["active_option_contract"] = successor

    def transition_legacy_option_contract(self, _customer, _session, **kwargs):
        self.legacy_transitions.append(kwargs)
        successor = kwargs["successor"]
        for key in (
            "offered_menu_items", "shown_menu_item_ids",
            "whatsapp_required_effect", "whatsapp_order_state_updated_at",
        ):
            self.state.pop(key, None)
        if successor is None:
            self.state.pop("active_option_contract", None)
        else:
            self.state["active_option_contract"] = successor

    def clear_option_contract(self, *_args):
        self.cleared = True
        self.state = {}


def session_service(repository):
    return AgentSessionService(
        repository,
        customer_service=SimpleNamespace(),
        settings=SimpleNamespace(agent_session_ttl_hours=24),
        clock=lambda: NOW,
    )


def test_contract_validation_checks_identity_version_consumer_and_membership():
    active = contract()
    service = session_service(ContractRepository({"active_option_contract": active.model_dump()}))

    assert service.validate_option_contract_consumption(
        "customer-1", "session-1",
        consumer_capability=active.consumer_capability,
        contract_id=active.contract_id,
        contract_version=active.contract_version,
        selected_option_id="item-1",
    ) == active
    for override in (
        {"contract_id": "wrong"},
        {"contract_version": 2},
        {"consumer_capability": "wrong_tool"},
        {"selected_option_id": "stale-item"},
    ):
        values = {
            "consumer_capability": active.consumer_capability,
            "contract_id": active.contract_id,
            "contract_version": active.contract_version,
            "selected_option_id": "item-1",
            **override,
        }
        result = service.validate_option_contract_consumption(
            "customer-1", "session-1", **values
        )
        assert result.success is False
        assert result.error_code == "INVALID_OPTION_CONTRACT"


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"contract_id": None}, "contract_id_mismatch"),
        ({"contract_version": None}, "contract_version_mismatch"),
        ({"contract_id": "wrong"}, "contract_id_mismatch"),
        ({"contract_version": 2}, "contract_version_mismatch"),
        ({"consumer_capability": "wrong_tool"}, "consumer_mismatch"),
        ({"selected_option_id": None}, "selected_option_missing"),
        ({
            "selected_option_id": "Choice", "bound_option_id": "Choice"
        }, "selected_option_not_offered"),
        ({
            "selected_option_id": "wrong", "bound_option_id": "wrong"
        }, "selected_option_not_offered"),
        ({"bound_option_id": "other-item"}, "item_selected_option_mismatch"),
        ({"scope": {}}, "scope_mismatch"),
    ],
)
def test_contract_validation_fails_closed_with_privacy_safe_reason(
    monkeypatch, overrides, expected_reason,
):
    active = contract("private-contract", option_id="private-item").model_copy(
        update={"scope": {"cart_id": "private-cart"}}
    )
    service = session_service(ContractRepository({
        "active_option_contract": active.model_dump()
    }))
    events = []
    monkeypatch.setattr(
        agent_session_service_module.logger,
        "info",
        lambda _message, *, extra: events.append(extra),
    )
    values = {
        "consumer_capability": active.consumer_capability,
        "contract_id": active.contract_id,
        "contract_version": active.contract_version,
        "selected_option_id": "private-item",
        "bound_option_id": "private-item",
        "scope": {"cart_id": "private-cart"},
        **overrides,
    }

    response = service.validate_option_contract_consumption(
        "private-customer", "private-session", **values
    )

    assert response.success is False
    assert response.error_code == "INVALID_OPTION_CONTRACT"
    assert events == [{
        "event": "option_contract_validation_failed",
        "option_contract_failure_reason": expected_reason,
    }]
    logged = repr(events)
    for private_value in (
        "private-contract", "private-item", "private-cart",
        "private-customer", "private-session", "Choice",
    ):
        assert private_value not in logged


def test_missing_active_contract_emits_safe_contract_missing_reason(monkeypatch):
    service = session_service(ContractRepository())
    events = []
    monkeypatch.setattr(
        agent_session_service_module.logger,
        "info",
        lambda _message, *, extra: events.append(extra),
    )

    response = service.validate_option_contract_consumption(
        "customer", "session",
        consumer_capability="start_cart_item_customization",
        contract_id="not-logged",
        contract_version=1,
        selected_option_id="not-logged",
    )

    assert response.error_code == "INVALID_OPTION_CONTRACT"
    assert events[0]["option_contract_failure_reason"] == "contract_missing"
    assert "not-logged" not in repr(events)


def test_exact_bound_item_contract_selection_succeeds_without_diagnostics(
    monkeypatch,
):
    active = contract(option_id="item-5")
    service = session_service(ContractRepository({
        "active_option_contract": active.model_dump()
    }))
    events = []
    monkeypatch.setattr(
        agent_session_service_module.logger,
        "info",
        lambda _message, *, extra: events.append(extra),
    )

    result = service.validate_option_contract_consumption(
        "customer", "session",
        consumer_capability="start_cart_item_customization",
        contract_id=active.contract_id,
        contract_version=active.contract_version,
        selected_option_id="item-5",
        bound_option_id="item-5",
    )

    assert result == active
    assert events == []


def test_typed_contract_is_preferred_and_legacy_state_adapts_when_absent():
    typed = contract("typed")
    repository = ContractRepository({
        "active_option_contract": typed.model_dump(),
        "offered_menu_items": [{"product_id": "legacy", "name": "Legacy"}],
        "whatsapp_order_state_updated_at": NOW.isoformat(),
    })
    assert session_service(repository).get_active_option_contract("c", "s") == typed

    repository.state.pop("active_option_contract")
    legacy = session_service(repository).get_active_option_contract("c", "s")
    assert legacy.source_capability == "legacy-v1"
    assert [option.id for option in legacy.options] == ["legacy"]


def test_expired_malformed_and_future_contracts_fail_closed_and_clean_up():
    for raw in (
        {"invalid": True},
        contract().model_copy(update={"expires_at": NOW.isoformat()}).model_dump(),
        contract().model_copy(update={
            "created_at": (NOW + timedelta(minutes=1)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=31)).isoformat(),
        }).model_dump(),
    ):
        repository = ContractRepository({"active_option_contract": raw})
        assert session_service(repository).get_active_option_contract("c", "s") is None
        assert repository.cleared or repository.transitions


class ProcessorSessions:
    def __init__(self, active=None):
        self.active = active
        self.transitions = []

    def get_active_option_contract(self, *_args):
        return self.active

    def transition_option_contract(self, *_args, **kwargs):
        self.transitions.append(kwargs)
        self.active = kwargs["successor"]


def processor_for(sessions):
    forbidden = lambda: (_ for _ in ()).throw(AssertionError("not used"))
    return AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_sessions=sessions),
        agent_client_provider=forbidden,
        identity_resolver=forbidden,
        response_builder=forbidden,
    )


def call_with(*, proposal=None, consumption=None, effects=None):
    grounding = {"transactional_effects": effects or []}
    if proposal is not None:
        grounding["option_contract_proposal"] = proposal.model_dump()
    if consumption is not None:
        grounding["option_contract_consumption"] = consumption
    return {
        "tool_name": "capability", "success": True, "is_write": True,
        "result": {"success": True, "grounding": grounding},
    }


def protocol_result(**values):
    return {"option_contract_protocol_version": 1, **values}


def legacy_offer_call(*, include_typed_proposal=False):
    grounding = {
        "transactional_effects": [],
        "required_next_effect": "item_selected",
        "offered_options": [{"id": "item-1", "label": "Choice"}],
    }
    if include_typed_proposal:
        grounding["option_contract_proposal"] = contract().model_dump()
    return {
        "tool_name": "search_menu", "success": True, "is_write": False,
        "result": {"success": True, "grounding": grounding},
    }


def context():
    return SimpleNamespace(
        channel="whatsapp", customer_id="customer-1", user_id="customer-1",
        agent_session_id="session-1",
    )


def test_scripted_informational_reference_detour_retains_pizza_contract():
    pizza = contract("pizza-contract", option_id="pizza-1")
    sessions = ProcessorSessions(pizza)
    processor = processor_for(sessions)

    processor._persist_whatsapp_grounding_state(
        context(),
        SimpleNamespace(raw_result=protocol_result(tool_calls=[call_with()])),
    )
    assert sessions.transitions == []
    assert sessions.active == pizza


def test_scripted_dessert_selection_offer_replaces_pizza_contract():
    pizza = contract("pizza-contract", option_id="pizza-1")
    dessert = contract("dessert-contract", option_id="dessert-1")
    sessions = ProcessorSessions(pizza)
    processor = processor_for(sessions)

    processor._persist_whatsapp_grounding_state(
        context(), SimpleNamespace(raw_result=protocol_result(
            required_effect=dessert.required_effect,
            tool_calls=[call_with(proposal=dessert)],
        ))
    )
    assert sessions.transitions[-1]["expected"] == pizza
    assert sessions.transitions[-1]["successor"] == dessert
    assert sessions.active == dessert


def test_fulfillment_offer_replaces_menu_contract_and_both_choices_validate():
    menu = contract("menu-contract", option_id="menu-item")
    fulfillment_base = contract(
        "fulfillment-contract",
        effect="fulfillment_saved",
        consumer="update_order_flow",
        option_id="set_delivery",
        source="get_order_status",
    )
    fulfillment = OptionContract(**{
        **fulfillment_base.model_dump(),
        "scope": {"order_id": "ORD-1"},
        "options": [
            {"id": "set_delivery", "label": "Delivery"},
            {"id": "set_takeaway", "label": "Takeaway"},
        ],
    })
    sessions = ProcessorSessions(menu)
    processor = processor_for(sessions)

    processor._persist_whatsapp_grounding_state(
        context(),
        SimpleNamespace(raw_result=protocol_result(
            required_effect="fulfillment_saved",
            tool_calls=[call_with(proposal=fulfillment)],
        )),
    )

    assert sessions.transitions[-1]["expected"] == menu
    assert sessions.transitions[-1]["successor"] == fulfillment
    repository = ContractRepository({
        "active_option_contract": fulfillment.model_dump()
    })
    service = session_service(repository)
    for action in ("set_delivery", "set_takeaway"):
        assert service.validate_option_contract_consumption(
            "customer-1",
            "session-1",
            consumer_capability="update_order_flow",
            contract_id=fulfillment.contract_id,
            contract_version=fulfillment.contract_version,
            selected_option_id=action,
            scope={"order_id": "ORD-1"},
        ) == fulfillment


def test_consumption_requires_same_call_effect_and_atomically_installs_successor():
    old = contract("old")
    successor = contract(
        "next", effect="customization_saved",
        consumer="save_customization_choice", option_id="size-large",
        source="start_cart_item_customization",
    )
    proof = {
        "contract_id": old.contract_id,
        "contract_version": old.contract_version,
        "effect": old.required_effect,
        "selected_option_id": "item-1",
    }
    sessions = ProcessorSessions(old)
    processor = processor_for(sessions)
    processor._persist_whatsapp_grounding_state(
        context(), SimpleNamespace(raw_result=protocol_result(required_effect=successor.required_effect, tool_calls=[
            call_with(consumption=proof, effects=["item_selected"], proposal=successor),
        ]))
    )
    assert len(sessions.transitions) == 1
    assert sessions.transitions[0]["expected"] == old
    assert sessions.transitions[0]["successor"] == successor

    sessions = ProcessorSessions(old)
    processor = processor_for(sessions)
    processor._persist_whatsapp_grounding_state(
        context(), SimpleNamespace(raw_result=protocol_result(tool_calls=[
            call_with(consumption=proof, effects=[]),
            call_with(effects=["item_selected"]),
        ]))
    )
    assert sessions.transitions == []
    assert sessions.active == old


class MixedVersionSessions:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.typed_calls = []

    def get_active_option_contract(self, *_args):
        raise AssertionError("old-runtime responses must not use typed persistence")

    def transition_option_contract(self, *_args, **kwargs):
        self.typed_calls.append(kwargs)

    def save_whatsapp_order_state(self, *_args, **kwargs):
        self.state = {
            "offered_menu_items": kwargs["offered_menu_items"],
            "whatsapp_required_effect": kwargs["required_effect"],
        }

    def clear_whatsapp_order_state(self, *_args):
        self.state = {}


def test_absent_protocol_marker_uses_legacy_offer_persistence_with_typed_service():
    sessions = MixedVersionSessions()
    processor = processor_for(sessions)
    processor._persist_whatsapp_grounding_state(
        context(),
        SimpleNamespace(raw_result={
            "required_effect": "item_selected",
            "tool_calls": [legacy_offer_call(include_typed_proposal=True)],
        }),
    )

    assert sessions.state["whatsapp_required_effect"] == "item_selected"
    assert sessions.state["offered_menu_items"] == [
        {"product_id": "item-1", "name": "Choice"}
    ]
    assert sessions.typed_calls == []


def test_absent_protocol_marker_legacy_effect_clears_existing_state():
    sessions = MixedVersionSessions({
        "offered_menu_items": [{"product_id": "item-1", "name": "Choice"}],
        "whatsapp_required_effect": "item_selected",
    })
    processor_for(sessions)._persist_whatsapp_grounding_state(
        context(),
        SimpleNamespace(raw_result={
            "tool_calls": [call_with(effects=["item_selected"])],
        }),
        prior_required_effect="item_selected",
        prior_available_option_count=1,
    )

    assert sessions.state == {}
    assert sessions.typed_calls == []


def test_absent_protocol_marker_informational_turn_retains_legacy_state():
    original = {
        "offered_menu_items": [{"product_id": "item-1", "name": "Choice"}],
        "whatsapp_required_effect": "item_selected",
    }
    sessions = MixedVersionSessions(original)
    processor_for(sessions)._persist_whatsapp_grounding_state(
        context(),
        SimpleNamespace(raw_result={"tool_calls": [call_with()]}),
        prior_required_effect="item_selected",
        prior_available_option_count=1,
    )

    assert sessions.state == original
    assert sessions.typed_calls == []


def test_legacy_adapted_contract_uses_dedicated_lazy_transition():
    repository = ContractRepository({
        "offered_menu_items": [{"product_id": "item-1", "name": "Choice"}],
        "whatsapp_required_effect": "item_selected",
        "whatsapp_order_state_updated_at": NOW.isoformat(),
    })
    service = session_service(repository)
    legacy = service.get_active_option_contract("c", "s")

    service.transition_option_contract(
        "c", "s", expected=legacy, successor=None,
    )

    assert len(repository.legacy_transitions) == 1
    assert repository.transitions == []
    assert repository.state == {}


def test_legacy_adapted_contract_atomically_installs_typed_successor_projection():
    repository = ContractRepository({
        "offered_menu_items": [{"product_id": "item-1", "name": "Choice"}],
        "whatsapp_required_effect": "item_selected",
        "whatsapp_order_state_updated_at": NOW.isoformat(),
    })
    service = session_service(repository)
    legacy = service.get_active_option_contract("c", "s")
    successor = contract("successor", option_id="item-2")
    projection = {
        "offered_menu_items": [{"product_id": "item-2", "name": "Second"}],
        "shown_menu_item_ids": ["item-2"],
    }

    service.transition_option_contract(
        "c", "s", expected=legacy, successor=successor,
        legacy_projection=projection,
    )

    call = repository.legacy_transitions[0]
    assert call["successor"] == successor.model_dump()
    assert call["legacy_projection"] == projection
    assert repository.state["active_option_contract"] == successor.model_dump()
