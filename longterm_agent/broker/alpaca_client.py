"""Thin Alpaca PAPER client. Read-only by default; every write is idempotent.

Why a hand-rolled client rather than alpaca-py: this needs exactly six calls, and the
guards below are the point of the module. A general SDK would happily take a live host.

SAFETY. The key pair in use is paper-only — the live endpoint answers 401 to it (measured
2026-08-09), so a real order is not reachable even by mistake. The guards here are the
second line, not the only one:

  * the host must be the paper host, checked on every request, not just at construction;
  * the key id must carry the PK prefix Alpaca gives paper accounts (AK = live).

Both raise before any network call. A wrong host with a right key would otherwise surface
as a 401 that reads like a credentials problem.
"""
import os
from typing import Any, Dict, List, Optional

import requests

PAPER_HOST = "https://paper-api.alpaca.markets"
TIMEOUT_S = 20


class BrokerConfigError(RuntimeError):
    """Refusing to talk to the broker at all — misconfiguration, not a failed request."""


class BrokerError(RuntimeError):
    """The broker answered, and the answer was an error."""


def credentials() -> tuple:
    """(key_id, secret) from the environment.

    Accepts the spellings that actually occur: Alpaca's own docs say ALPACA_API_KEY /
    ALPACA_SECRET_KEY, the dashboard labels the halves "Key ID" and "Secret Key", and this
    repo's .env uses ALPACA_API_KEY_ID for the id with ALPACA_API_KEY holding the secret.
    Guessing wrong here surfaces as a 401, which reads like a bad key rather than a bad
    variable name — so resolve it explicitly instead.
    """
    key = (os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_KEY_ID")
           or os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY"))
    secret = (os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_SECRET")
              or os.getenv("ALPACA_API_SECRET"))
    if secret is None and os.getenv("ALPACA_API_KEY_ID"):
        # Key id lives in its own variable, so a bare ALPACA_API_KEY is the secret half.
        secret = os.getenv("ALPACA_API_KEY")
    return key, secret


class AlpacaPaper:
    """Paper trading account. Construction validates; it does not connect."""

    def __init__(self, host: str = PAPER_HOST, key: Optional[str] = None,
                 secret: Optional[str] = None):
        env_key, env_secret = credentials()
        self.key = key or env_key
        self.secret = secret or env_secret
        self.host = host.rstrip("/")
        self._check_config()

    # --- guards ---------------------------------------------------------------------
    def _check_config(self) -> None:
        if not (self.key and self.secret):
            raise BrokerConfigError(
                "ALPACA_API_KEY_ID / secret are not set — generate them in the Paper "
                "Trading dashboard at app.alpaca.markets.")
        if self.host != PAPER_HOST:
            raise BrokerConfigError(
                f"host {self.host!r} is not the paper host. This package never talks to a "
                f"live endpoint; expected {PAPER_HOST!r}.")
        if not self.key.startswith("PK"):
            # AK is Alpaca's live prefix. A live key here means someone swapped the pair.
            raise BrokerConfigError(
                f"key id does not carry the PK prefix of a paper account "
                f"(got {self.key[:2]!r}…). Refusing to proceed.")

    @property
    def _headers(self) -> Dict[str, str]:
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    def _request(self, method: str, path: str, **kw) -> Any:
        self._check_config()          # re-checked per call: host is mutable state
        url = f"{self.host}/v2/{path.lstrip('/')}"
        # A dedicated variable, deliberately NOT the standard HTTPS_PROXY: that one is read
        # by every requests/urllib call in the process, so setting it would silently route
        # OpenRouter, Telegram and the price feeds through the same tunnel. Only the broker
        # leg needs it — measured 2026-08-12, the server reaches OpenRouter and Telegram
        # fine while Alpaca is dropped at TCP level from its hosting ASN.
        proxy = os.getenv("ALPACA_HTTPS_PROXY", "").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        r = requests.request(method, url, headers=self._headers, timeout=TIMEOUT_S,
                             proxies=proxies, **kw)
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    # --- reads ----------------------------------------------------------------------
    def account(self) -> Dict[str, Any]:
        return self._request("GET", "account")

    def overnight_buying_power(self) -> float:
        """Reg-T buying power — the binding limit for a book held for weeks.

        NOT `buying_power`: that field carries the 4x INTRADAY figure on a PDT-eligible
        account ($400k on $100k equity, measured 2026-08-09). Sizing a multi-week book
        against it overshoots by 2x and the margin call arrives once the book reaches
        full overlap — three weeks in, the worst moment to be surprised.
        """
        a = self.account()
        rt = a.get("regt_buying_power")
        return float(rt) if rt is not None else 2.0 * float(a["equity"])

    def positions(self) -> List[Dict[str, Any]]:
        return self._request("GET", "positions")

    def position(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            return self._request("GET", f"positions/{symbol}")
        except BrokerError as e:
            if "404" in str(e):
                return None
            raise

    def order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._request("GET", "orders:by_client_order_id",
                                 params={"client_order_id": client_order_id})
        except BrokerError as e:
            if "404" in str(e):
                return None
            raise

    # --- the one write ---------------------------------------------------------------
    def submit(self, symbol: str, qty: float, side: str, order_type: str,
               client_order_id: str, time_in_force: Optional[str] = None,
               dry_run: bool = False) -> Dict[str, Any]:
        """Place one order. Safe to call twice with the same `client_order_id`.

        Idempotency is the whole design here. The scheduler retries, and this project has
        already shipped a writer that silently wrote nothing every night — a double-submit
        would be the same class of failure with the opposite sign. Alpaca rejects a repeated
        client_order_id, and that rejection is treated as success: the existing order is
        fetched and returned, so a retry is a no-op rather than a second position.

        `order_type` is the strategy's own convention, mapped to Alpaca's fields:
          market -> market/day   (mid_1m: submitted on the minute boundary)
          moo    -> market/opg   (open_t1: the next session's open)
          moc    -> market/cls   (the exit, which the grader prices at the close)
        """
        kind = {"market": ("market", "day"), "moo": ("market", "opg"),
                "moc": ("market", "cls")}.get(order_type)
        if kind is None:
            raise ValueError(f"unknown order_type {order_type!r}")
        if qty <= 0:
            raise ValueError(f"qty must be > 0, got {qty!r}")
        body = {"symbol": symbol, "qty": str(qty), "side": side,
                "type": kind[0], "time_in_force": time_in_force or kind[1],
                "client_order_id": client_order_id}
        if dry_run:
            return {"dry_run": True, **body}
        try:
            return self._request("POST", "orders", json=body)
        except BrokerError as e:
            if "422" not in str(e):
                raise
            existing = self.order_by_client_id(client_order_id)
            if existing is None:
                raise
            return existing

    def cancel(self, order_id: str) -> None:
        self._request("DELETE", f"orders/{order_id}")
