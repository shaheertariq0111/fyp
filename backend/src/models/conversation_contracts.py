from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.tool_responses import TransactionalEffect


PresentationRole = Literal["selection_offer", "informational_reference"]
OPTION_CONTRACT_TTL_SECONDS = 30 * 60


class OptionContractOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(min_length=1, max_length=128)
    contract_version: int = Field(ge=1, le=2_147_483_647)
    contract_kind: Literal["selection_offer"] = "selection_offer"
    required_effect: TransactionalEffect
    consumer_capability: str = Field(min_length=1, max_length=128)
    source_capability: str = Field(min_length=1, max_length=128)
    source_request_id: str = Field(min_length=1, max_length=128)
    scope: dict[str, str] = Field(default_factory=dict)
    options: list[OptionContractOption] = Field(min_length=1, max_length=50)
    created_at: str = Field(max_length=64)
    expires_at: str = Field(max_length=64)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: dict[str, Any]) -> dict[str, str]:
        if len(value) > 8:
            raise ValueError("option contract scope is too large")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ValueError("invalid option contract scope key")
            if not isinstance(item, str) or not item or len(item) > 256:
                raise ValueError("invalid option contract scope value")
            result[key] = item
        return result

    @model_validator(mode="after")
    def validate_timestamps_and_options(self) -> "OptionContract":
        created = _timestamp(self.created_at)
        expires = _timestamp(self.expires_at)
        if expires <= created:
            raise ValueError("option contract expiry must follow creation")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("option contract option IDs must be unique")
        return self


class OptionContractConsumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(min_length=1, max_length=128)
    contract_version: int = Field(ge=1)
    effect: TransactionalEffect
    selected_option_id: str = Field(min_length=1, max_length=256)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("option contract timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
