"""HTTP client for the Royal Mail Click & Drop API.

Thin wrapper around ``requests`` that handles authentication, rate-limit
retries and error surfacing.  Every function takes the API key explicitly
rather than reading a singleton — the credential lifecycle is separate
from the EasyPost client manager.

API docs: https://api.parcel.royalmail.com/
"""

from __future__ import annotations

import base64
import time
from typing import Any, Optional

import requests

BASE_URL = "https://api.parcel.royalmail.com/api/v1"
_TIMEOUT = 30
_MAX_RETRIES = 3
_RETRY_DELAY = 1.0


class ClickDropError(RuntimeError):
    """Any Click & Drop API error surfaced to the UI."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    path: str,
    api_key: str,
    *,
    json: Any = None,
    params: dict | None = None,
    accept: str = "application/json",
    raw: bool = False,
) -> Any:
    """Send a request with retry-on-429."""
    url = f"{BASE_URL}{path}"
    headers = _headers(api_key)
    headers["Accept"] = accept

    for attempt in range(_MAX_RETRIES):
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=json,
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 429:
            delay = _RETRY_DELAY * (2 ** attempt)
            time.sleep(delay)
            continue
        if resp.status_code == 401:
            raise ClickDropError(
                "Click & Drop API key is invalid or expired.", resp.status_code
            )
        if resp.status_code == 403:
            raise ClickDropError(
                "This feature is not available for your Click & Drop account type.",
                resp.status_code,
            )
        if raw:
            resp.raise_for_status()
            return resp.content
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise ClickDropError(
                f"Click & Drop API error {resp.status_code}: {body}",
                resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    raise ClickDropError("Click & Drop API rate limit exceeded after retries.", 429)


# ── Version / connectivity check ──────────────────────────────────────

def get_version(api_key: str) -> dict:
    return _request("GET", "/version", api_key)


# ── Services ──────────────────────────────────────────────────────────

def get_services(
    api_key: str,
    *,
    country_code: str = "",
    postcode: str = "",
    package_format: str = "",
    weight_grams: int = 0,
    carrier_name: str = "",
) -> list[dict]:
    """Available shipping services, optionally filtered by destination."""
    params: dict[str, Any] = {}
    if country_code:
        params["destinationCountryCode"] = country_code
    if postcode:
        params["destinationPostcode"] = postcode
    if package_format:
        params["packagingFormat"] = package_format
    if weight_grams:
        params["packageWeight"] = weight_grams
    if carrier_name:
        params["carrierName"] = carrier_name
    return _request("GET", "/services", api_key, params=params or None)


# ── Carriers ──────────────────────────────────────────────────────────

def get_carriers(api_key: str) -> list[dict]:
    return _request("GET", "/carriers", api_key)


# ── Orders ────────────────────────────────────────────────────────────

def create_orders(api_key: str, *, items: list[dict]) -> dict:
    return _request("POST", "/orders", api_key, json={"items": items})


def get_order(api_key: str, order_id: int) -> dict:
    return _request("GET", f"/orders/{order_id}", api_key)


def delete_order(api_key: str, order_id: int) -> dict:
    return _request("DELETE", f"/orders/{order_id}", api_key)


# ── Labels (OBA only) ────────────────────────────────────────────────

def get_label_pdf(
    api_key: str,
    order_id: int,
    *,
    include_returns: bool = False,
    include_cn: bool = False,
) -> bytes:
    """Download a label as raw PDF bytes.  OBA accounts only."""
    params: dict[str, Any] = {"documentType": "postageLabel"}
    if include_returns:
        params["includeReturnsLabel"] = "true"
    if include_cn:
        params["includeCN"] = "true"
    return _request(
        "GET",
        f"/orders/{order_id}/label",
        api_key,
        params=params,
        accept="application/pdf",
        raw=True,
    )


def decode_label_from_response(label_b64: Optional[str]) -> Optional[bytes]:
    """Decode the base64 label field returned by create_orders (OBA)."""
    if not label_b64:
        return None
    try:
        return base64.b64decode(label_b64)
    except Exception:
        return None


# ── Manifests ─────────────────────────────────────────────────────────

def create_manifest(api_key: str, *, carrier_name: str = "") -> dict:
    body: dict[str, Any] = {}
    if carrier_name:
        body["carrierName"] = carrier_name
    return _request("POST", "/manifests", api_key, json=body or None)


def get_manifest(api_key: str, manifest_id: int) -> dict:
    return _request("GET", f"/manifests/{manifest_id}", api_key)
