"""Hide My Email (iCloud+ alias) client - a JSON REST layer authed purely by
the web session's cookies. See RESEARCH.md "Hide My Email"."""

from __future__ import annotations

import dataclasses

import requests

from ..errors import AppleError


class HmeError(AppleError):
    pass


# Origin/Referer of the icloud.com page that normally makes this XHR call.
_HEADERS = {"Accept": "application/json", "Origin": "https://www.icloud.com",
           "Referer": "https://www.icloud.com/"}


def _error_detail(data: dict) -> str:
    """Apple's `error` field shape varies (dict, bare int, or absent); fall back to the raw body."""
    err = data.get("error")
    if isinstance(err, dict):
        return err.get("errorMessage") or err.get("message") or str(err)
    if err is not None:
        return str(err)
    return data.get("errorMessage") or data.get("message") or str(data)


@dataclasses.dataclass(frozen=True)
class HmeAlias:
    anonymous_id: str
    address: str
    label: str
    note: str
    forward_to: str
    is_active: bool
    domain: str
    created_at: float  # unix epoch seconds

    def public_dict(self) -> dict:
        return {"address": self.address, "label": self.label, "domain": self.domain}


class HmeClient:
    def __init__(self, base_url: str, http: requests.Session):
        if not base_url.startswith("https://"):
            raise HmeError("Hide My Email service URL must use HTTPS")
        self._v1 = base_url.rstrip("/") + "/v1"
        self._v2 = base_url.rstrip("/") + "/v2"
        self.http = http

    def _post(self, operation: str, payload: dict | None = None) -> dict:
        resp = self.http.post(
            f"{self._v1}/hme/{operation}", headers=_HEADERS,
            json=payload, timeout=20,
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise HmeError(
                f"non-JSON response from hme/{operation} (HTTP {resp.status_code})"
            ) from e
        if not resp.ok or not data.get("success"):
            raise HmeError(
                f"hme/{operation} failed (HTTP {resp.status_code}): {_error_detail(data)}"
            )
        return data.get("result") or {}

    def list(self) -> list[HmeAlias]:
        resp = self.http.get(f"{self._v2}/hme/list", headers=_HEADERS, timeout=20)
        try:
            data = resp.json()
        except ValueError as e:
            raise HmeError(f"non-JSON response from hme/list (HTTP {resp.status_code})") from e
        if not resp.ok or not data.get("success"):
            raise HmeError(f"hme/list failed (HTTP {resp.status_code}): {_error_detail(data)}")
        result = data.get("result") or {}
        return [_parse(e) for e in result.get("hmeEmails", [])]

    def generate(self) -> str:
        """Obtain a candidate address. It is not active until reserve() succeeds."""
        address = self._post("generate").get("hme")
        if not isinstance(address, str) or "@" not in address:
            raise HmeError("hme/generate did not return an address")
        return address

    def reserve(self, address: str, label: str, note: str = "") -> HmeAlias:
        """Activate a generated address with the user's label and note."""
        if not label.strip():
            raise HmeError("a label is required to reserve a Hide My Email address")
        result = self._post(
            "reserve", {"hme": address, "label": label.strip(), "note": note}
        )
        item = result.get("hme")
        if not isinstance(item, dict):
            raise HmeError("hme/reserve did not return an alias")
        return _parse(item)

    def update_metadata(self, anonymous_id: str, label: str, note: str = "") -> None:
        """Update an existing alias. Use the anonymousId returned by list()."""
        if not anonymous_id or not label.strip():
            raise HmeError("alias ID and nonempty label are required")
        self._post(
            "updateMetaData",
            {"anonymousId": anonymous_id, "label": label.strip(), "note": note},
        )


def _parse(e: dict) -> HmeAlias:
    return HmeAlias(
        anonymous_id=e.get("anonymousId", ""),
        address=e.get("hme", ""),
        label=e.get("label", ""),
        note=e.get("note", ""),
        forward_to=e.get("forwardToEmail", ""),
        is_active=bool(e.get("isActive")),
        domain=e.get("domain", ""),
        created_at=(e.get("createTimestamp") or 0) / 1000,
    )
