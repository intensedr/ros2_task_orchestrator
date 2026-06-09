"""Shared helpers for stable structured task errors."""

from __future__ import annotations

import json
from typing import Any


def error_payload(error_code: str, error_message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": error_code,
        "message": error_message,
        "details": details or {},
    }


def maybe_add_error(
    payload: dict[str, Any],
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if error_code or error_message:
        payload["error"] = error_payload(error_code, error_message, details)
    return payload


def error_result_json(error_code: str, error_message: str, details: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {
            "error": error_payload(error_code, error_message, details),
            "error_code": error_code,
            "error_message": error_message,
        },
        sort_keys=True,
    )


def result_json_with_error(
    result_json: str,
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> str:
    if not (error_code or error_message):
        return result_json
    if result_json == "{}":
        return error_result_json(error_code, error_message, details)

    try:
        payload = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json

    if not isinstance(payload, dict):
        return result_json

    if "error" not in payload:
        maybe_add_error(payload, error_code, error_message, details)
    payload.setdefault("error_code", error_code)
    payload.setdefault("error_message", error_message)
    return json.dumps(payload, sort_keys=True)
