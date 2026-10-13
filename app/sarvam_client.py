"""Places an outbound call through Sarvam Voice Agents.

API reference: https://docs.sarvam.ai/voice-agents/api/instant-outbound/create
"""
from __future__ import annotations

from typing import Any

import httpx

from .config import Settings

LANGUAGES = {"Bengali", "Gujarati", "Kannada", "Malayalam", "Tamil", "Telugu", "Punjabi",
             "Sanskrit", "Odia", "Marathi", "Hindi", "English", "Assamese"}


class SarvamError(Exception):
    pass


def build_outbound_request(agent_variables: dict[str, str], to_number: str, customer_id: str, s: Settings) -> dict[str, Any]:
    app_config: dict[str, Any] = {
        "app_id": s.sarvam_app_id,
        "app_version": s.sarvam_app_version,
        "connection_config": {
            "connection_id": s.sarvam_connection_id,
            "agent_phone_number": s.sarvam_agent_phone_number,
        },
        "agent_variables": agent_variables,
    }
    lang = agent_variables.get("preferred_language")
    if lang in LANGUAGES:
        app_config["app_overrides"] = {"initial_language_name": lang}
    token = f"?token={s.tool_secret}" if s.tool_secret else ""
    return {
        "app_config": app_config,
        "user_config": {"user_phone_number": to_number},
        "webhook_config": {
            "url": f"{s.public_base_url}/webhooks/sarvam/outbound{token}",
            "metadata": {"customer_id": customer_id},
        },
    }


def place_call(agent_variables: dict[str, str], customer_id: str, s: Settings) -> str:
    """Returns Sarvam's attempt_id. Dials DEMO_PHONE_NUMBER, not the number on the record."""
    if not s.demo_phone_number:
        raise SarvamError("DEMO_PHONE_NUMBER is not set. Refusing to dial: calls may only go to a number you control.")
    if not s.sarvam_ready:
        raise SarvamError("Sarvam settings are incomplete. Fill the SARVAM_* values in .env.")
    body = build_outbound_request(agent_variables, s.demo_phone_number, customer_id, s)
    url = f"{s.sarvam_base_url}/orgs/{s.sarvam_org_id}/workspaces/{s.sarvam_workspace_id}/outbounds"
    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, json=body, headers={"X-API-Key": s.sarvam_api_key})
    if r.status_code >= 400:
        raise SarvamError(f"Sarvam error {r.status_code}: {r.text}")
    return r.json()["attempt_id"]
