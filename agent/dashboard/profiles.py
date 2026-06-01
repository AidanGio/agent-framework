"""User profile schema and LangGraph Store CRUD."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from .options import SUPPORTED_MODEL_IDS, model_supports_effort

logger = logging.getLogger(__name__)

PROFILES_NAMESPACE: list[str] = ["profiles"]


class ProfileUpdate(BaseModel):
    default_model: str
    reasoning_effort: str

    @field_validator("default_model")
    @classmethod
    def _model_supported(cls, v: str) -> str:
        if v not in SUPPORTED_MODEL_IDS:
            raise ValueError(f"unsupported model: {v}")
        return v

    def validate_pairing(self) -> None:
        if not model_supports_effort(self.default_model, self.reasoning_effort):
            raise ValueError(
                f"effort {self.reasoning_effort!r} not supported by {self.default_model!r}"
            )


def _client():
    return get_client()


async def _get_value(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(namespace, key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def get_profile(user_id: str) -> dict[str, Any] | None:
    return await _get_value(PROFILES_NAMESPACE, user_id)


async def upsert_profile(user_id: str, email: str, update: ProfileUpdate) -> dict[str, Any]:
    existing = await get_profile(user_id) or {}
    value: dict[str, Any] = {
        **existing,
        "id": user_id,
        "email": email or existing.get("email", ""),
        "default_model": update.default_model,
        "reasoning_effort": update.reasoning_effort,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(PROFILES_NAMESPACE, user_id, value)
    return value


async def list_profiles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(PROFILES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    return out
