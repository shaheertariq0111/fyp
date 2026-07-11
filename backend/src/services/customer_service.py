from __future__ import annotations

import hashlib
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from src.models.tool_responses import ToolResponse


class CustomerService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def normalize_phone(phone: str) -> str | None:
        cleaned = re.sub(r"[^\d+]", "", phone.strip())
        if cleaned.startswith("00"):
            cleaned = f"+{cleaned[2:]}"
        if cleaned.startswith("+"):
            digits = cleaned[1:]
        else:
            digits = cleaned
            cleaned = f"+{digits}"
        if not digits.isdigit() or len(digits) < 8 or len(digits) > 15:
            return None
        return cleaned

    @staticmethod
    def phone_hash(phone_e164: str) -> str:
        return hashlib.sha256(phone_e164.encode()).hexdigest()

    def ensure_customer(self, customer_id: str | None = None, channel: str = "web") -> dict:
        if customer_id:
            existing = self.repository.get(customer_id)
            if existing:
                return existing
        now = self._now()
        effective_id = customer_id or f"cust-{uuid.uuid4()}"
        customer = {
            "PK": f"CUSTOMER#{effective_id}",
            "SK": "PROFILE",
            "customer_id": effective_id,
            "display_name": None,
            "phone_e164": None,
            "phone_hash": None,
            "phone_verified": False,
            "addresses": [],
            "channel_profiles": {channel: {"created_at": now}},
            "created_at": now,
            "updated_at": now,
        }
        self.repository.create(customer)
        return customer

    def get_profile(self, customer_id: str) -> ToolResponse:
        customer = self.repository.get(customer_id)
        if not customer:
            return ToolResponse.error(
                error_code="CUSTOMER_NOT_FOUND",
                user_message="I couldn't find that customer profile.",
            )
        return ToolResponse.ok(
            data={"customer": self._public(customer)},
            user_message="Here is the current customer profile.",
            next_action="present_customer_profile",
            agent={
                "entity": "customer",
                "customer": self._public(customer),
                "instruction": "Use this trusted customer profile. Do not invent missing fields.",
            },
        )

    def update_profile(
        self,
        customer_id: str,
        *,
        display_name: str | None = None,
        phone_number: str | None = None,
        channel: str = "web",
        phone_verified: bool = False,
    ) -> ToolResponse:
        customer = self.ensure_customer(customer_id, channel)
        if display_name is not None:
            cleaned_name = " ".join(display_name.strip().split())
            if not cleaned_name:
                return ToolResponse.error(
                    error_code="CUSTOMER_NAME_REQUIRED",
                    user_message="Please provide a customer name.",
                )
            customer["display_name"] = cleaned_name
        if phone_number is not None:
            phone_e164 = self.normalize_phone(phone_number)
            if not phone_e164:
                return ToolResponse.error(
                    error_code="INVALID_PHONE_NUMBER",
                    user_message="Please provide a valid phone number with country code.",
                )
            existing = self.repository.get_by_phone_hash(self.phone_hash(phone_e164))
            if existing and existing.get("customer_id") != customer_id:
                customer = existing
            customer["phone_e164"] = phone_e164
            customer["phone_hash"] = self.phone_hash(phone_e164)
            customer["GSI1PK"] = f"PHONE#{customer['phone_hash']}"
            customer["GSI1SK"] = "CUSTOMER"
            customer["phone_verified"] = bool(phone_verified)
        profiles = dict(customer.get("channel_profiles") or {})
        profiles.setdefault(channel, {"created_at": customer.get("created_at")})
        profiles[channel]["updated_at"] = self._now()
        customer["channel_profiles"] = profiles
        customer["updated_at"] = self._now()
        self.repository.save(customer)
        return ToolResponse.ok(
            data={"customer": self._public(customer)},
            user_message="Customer details were saved.",
            next_action="present_customer_profile",
            agent={
                "entity": "customer",
                "customer": self._public(customer),
                "instruction": "Use these trusted customer details for this customer.",
            },
        )

    def save_address(
        self,
        customer_id: str,
        *,
        address_text: str,
        label: str | None = None,
        make_default: bool = True,
        channel: str = "web",
    ) -> ToolResponse:
        customer = self.ensure_customer(customer_id, channel)
        cleaned_address = " ".join((address_text or "").strip().split())
        if not cleaned_address:
            return ToolResponse.error(
                error_code="ADDRESS_REQUIRED",
                user_message="Please provide a delivery address.",
            )

        now = self._now()
        addresses = [dict(address) for address in customer.get("addresses") or []]
        if make_default:
            for address in addresses:
                address["is_default"] = False

        address = {
            "address_id": f"ADDR-{uuid.uuid4()}",
            "label": " ".join(label.strip().split()) if label and label.strip() else "Delivery address",
            "address_text": cleaned_address,
            "created_at": now,
            "last_used_at": now,
            "is_default": bool(make_default),
            "verified": False,
        }
        addresses.append(address)
        customer["addresses"] = addresses
        customer["updated_at"] = now
        profiles = dict(customer.get("channel_profiles") or {})
        profiles.setdefault(channel, {"created_at": customer.get("created_at")})
        profiles[channel]["updated_at"] = now
        customer["channel_profiles"] = profiles
        self.repository.save(customer)
        return ToolResponse.ok(
            data={"customer": self._public(customer), "address": address},
            user_message="Delivery address was saved.",
            next_action="present_customer_profile",
            agent={
                "entity": "customer",
                "customer": self._public(customer),
                "address": address,
                "instruction": (
                    "Use this trusted saved delivery address. If the customer is "
                    "checking out for delivery, also save the exact address text "
                    "onto the current order with update_order_flow(save_address)."
                ),
            },
        )

    def admin_search(self, query: str | None = None, limit: int = 50) -> dict:
        normalized = " ".join((query or "").strip().split()).casefold()
        customers = [self._public(customer) for customer in self.repository.list_all()]
        if normalized:
            phone = self.normalize_phone(normalized)
            if phone:
                match = self.repository.get_by_phone_hash(self.phone_hash(phone))
                customers = [self._public(match)] if match else []
            else:
                customers = [
                    customer for customer in customers
                    if normalized in str(customer.get("display_name") or "").casefold()
                    or normalized in str(customer.get("phone_e164") or "").casefold()
                    or any(
                        normalized in str(address.get("address_text") or "").casefold()
                        for address in customer.get("addresses", [])
                    )
                ]
        customers.sort(key=lambda customer: str(customer.get("display_name") or customer.get("customer_id")))
        return {"customers": customers[:limit], "next_cursor": None}

    def admin_get(self, customer_id: str, order_service=None) -> dict:
        customer = self.repository.get(customer_id)
        if not customer:
            raise ValueError("CUSTOMER_NOT_FOUND")
        result = {"customer": self._public(customer)}
        if order_service is not None:
            orders = [
                order for order in order_service.admin_list_orders(limit=1000)["orders"]
                if order.get("customer_id") == customer_id or order.get("user_id") == customer_id
            ]
            result["orders"] = orders
        return result

    @staticmethod
    def _public(customer: dict) -> dict:
        return {
            "customer_id": customer.get("customer_id"),
            "display_name": customer.get("display_name"),
            "phone_e164": customer.get("phone_e164"),
            "phone_verified": customer.get("phone_verified"),
            "addresses": deepcopy(customer.get("addresses") or []),
        }
