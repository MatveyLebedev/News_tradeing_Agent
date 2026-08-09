"""Guards and idempotency. No network: every test either fails before a request or mocks it."""
import pytest

from longterm_agent.broker import alpaca_client as ac


PK = "PKTESTTESTTESTTEST00"
SEC = "s" * 40


def client(**kw):
    return ac.AlpacaPaper(key=PK, secret=SEC, **kw)


# --- guards --------------------------------------------------------------------------
def test_live_host_is_refused():
    with pytest.raises(ac.BrokerConfigError, match="not the paper host"):
        ac.AlpacaPaper(host="https://api.alpaca.markets", key=PK, secret=SEC)


def test_live_key_is_refused():
    with pytest.raises(ac.BrokerConfigError, match="PK prefix"):
        ac.AlpacaPaper(key="AKLIVEKEYLIVEKEYLIVE", secret=SEC)


def test_missing_credentials_are_refused(monkeypatch):
    for v in ("ALPACA_API_KEY_ID", "ALPACA_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY",
              "ALPACA_SECRET_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ac.BrokerConfigError, match="are not set"):
        ac.AlpacaPaper()


def test_host_is_rechecked_per_request(monkeypatch):
    """Construction-time validation alone would let a later assignment through."""
    c = client()
    c.host = "https://api.alpaca.markets"
    monkeypatch.setattr(ac.requests, "request",
                        lambda *a, **k: pytest.fail("a request escaped the guard"))
    with pytest.raises(ac.BrokerConfigError):
        c.account()


# --- credential resolution ----------------------------------------------------------
def test_repo_env_spelling_resolves(monkeypatch):
    """This repo's .env: key id in ALPACA_API_KEY_ID, secret in ALPACA_API_KEY."""
    for v in ("ALPACA_SECRET_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_KEY_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY_ID", PK)
    monkeypatch.setenv("ALPACA_API_KEY", SEC)
    assert ac.credentials() == (PK, SEC)


def test_alpaca_docs_spelling_resolves(monkeypatch):
    for v in ("ALPACA_API_KEY_ID", "ALPACA_KEY_ID", "ALPACA_SECRET", "ALPACA_API_SECRET"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", PK)
    monkeypatch.setenv("ALPACA_SECRET_KEY", SEC)
    assert ac.credentials() == (PK, SEC)


# --- order type mapping --------------------------------------------------------------
class _Resp:
    def __init__(self, code=200, payload=None, text="{}"):
        self.status_code, self._p, self.text = code, payload or {}, text

    def json(self):
        return self._p


@pytest.mark.parametrize("kind,typ,tif", [("market", "market", "day"),
                                          ("moo", "market", "opg"),
                                          ("moc", "market", "cls")])
def test_order_type_maps_to_alpaca_fields(monkeypatch, kind, typ, tif):
    seen = {}

    def fake(method, url, **kw):
        seen.update(kw.get("json") or {})
        return _Resp(payload={"id": "1"})

    monkeypatch.setattr(ac.requests, "request", fake)
    client().submit("AAPL", 3, "buy", kind, client_order_id="cid")
    assert (seen["type"], seen["time_in_force"]) == (typ, tif)


def test_unknown_order_type_is_loud():
    with pytest.raises(ValueError, match="unknown order_type"):
        client().submit("AAPL", 1, "buy", "limit", client_order_id="cid")


def test_zero_qty_is_refused():
    with pytest.raises(ValueError, match="qty must be"):
        client().submit("AAPL", 0, "buy", "market", client_order_id="cid")


def test_dry_run_never_calls_the_network(monkeypatch):
    monkeypatch.setattr(ac.requests, "request",
                        lambda *a, **k: pytest.fail("dry run hit the network"))
    out = client().submit("AAPL", 3, "buy", "market", client_order_id="cid", dry_run=True)
    assert out["dry_run"] and out["symbol"] == "AAPL"


# --- idempotency ---------------------------------------------------------------------
def test_duplicate_client_order_id_returns_the_existing_order(monkeypatch):
    """A retried scheduler must not open a second position."""
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url))
        if method == "POST":
            return _Resp(422, text="client_order_id must be unique")
        return _Resp(payload={"id": "existing", "client_order_id": "cid"})

    monkeypatch.setattr(ac.requests, "request", fake)
    out = client().submit("AAPL", 3, "buy", "market", client_order_id="cid")
    assert out["id"] == "existing"
    assert [m for m, _ in calls] == ["POST", "GET"]


def test_a_real_error_still_raises(monkeypatch):
    monkeypatch.setattr(ac.requests, "request",
                        lambda *a, **k: _Resp(500, text="boom"))
    with pytest.raises(ac.BrokerError):
        client().submit("AAPL", 3, "buy", "market", client_order_id="cid")


# --- buying power --------------------------------------------------------------------
def test_overnight_buying_power_prefers_regt(monkeypatch):
    monkeypatch.setattr(ac.requests, "request", lambda *a, **k: _Resp(
        payload={"equity": "100000", "buying_power": "400000",
                 "regt_buying_power": "200000"}))
    assert client().overnight_buying_power() == 200000.0


def test_overnight_buying_power_falls_back_to_2x_equity(monkeypatch):
    """Never fall back to `buying_power`: that is the 4x intraday number."""
    monkeypatch.setattr(ac.requests, "request", lambda *a, **k: _Resp(
        payload={"equity": "100000", "buying_power": "400000"}))
    assert client().overnight_buying_power() == 200000.0
