"""Phase 9.1/9.2/9.4/9.5 tests — pure parsers and formatters (no DB, no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bloomberg_news_fetcher"))
from filings_fetcher import html_to_text, parse_form4  # noqa: E402

from longterm_agent.tools.filings_tool import format_insider  # noqa: E402

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer><officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-10</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>250.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-11</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>248</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_transactions():
    txs = parse_form4(FORM4)
    assert len(txs) == 2
    sell, buy = txs
    assert sell["insider_name"] == "DOE JANE"
    assert sell["insider_title"] == "Chief Financial Officer"
    assert sell["code"] == "S" and sell["acquired"] is False
    assert sell["shares"] == 1000 and sell["price"] == 250.5
    assert sell["value"] == 250500.0
    assert buy["code"] == "P" and buy["acquired"] is True


def test_parse_form4_garbage_is_empty():
    assert parse_form4("not xml at all") == []
    assert parse_form4("<ownershipDocument/>") == []


def test_html_to_text_strips_scripts_and_caps():
    html = "<html><head><script>evil()</script><style>.x{}</style></head>" \
           "<body><h1>Guidance&nbsp;Update</h1><p>Revenue   to be <b>$12.2B</b></p></body></html>"
    text = html_to_text(html)
    assert "evil" not in text and ".x{}" not in text
    assert "Guidance" in text and "$12.2B" in text
    assert len(html_to_text("<p>" + "long " * 100000 + "</p>")) <= 150_000


def test_format_insider_aggregates_and_cluster_flag():
    # rows: (filed, name, title, code, acquired, shares, value)
    rows = [
        ("2026-07-10", "DOE JANE", "CFO", "S", False, 1000.0, 250500.0),
        ("2026-07-09", "SMITH BOB", "CEO", "S", False, 2000.0, 500000.0),
        ("2026-07-08", "LEE ANN", "Director", "S", False, 300.0, 75000.0),
        ("2026-07-07", "WU KAI", "Officer", "P", True, 500.0, 124000.0),
        ("2026-07-06", "DOE JANE", "CFO", "A", True, 5000.0, None),  # award — excluded from nets
    ]
    out = format_insider(rows, 90)
    assert out["available"] is True
    assert out["open_market_sells"] == {"n": 3, "shares": 3300.0, "value_usd": 825500.0}
    assert out["open_market_buys"] == {"n": 1, "shares": 500.0, "value_usd": 124000.0}
    assert out["cluster_selling"] is True                     # 3 distinct sellers
    out2 = format_insider(rows[:2] + rows[3:], 90)
    assert out2["cluster_selling"] is False                   # only 2 distinct sellers
    assert format_insider([], 90)["available"] is False


def test_summarize_chain_atm_and_skew():
    import pandas as pd
    from options_fetcher import summarize_chain
    calls = pd.DataFrame({"strike": [90, 100, 110], "impliedVolatility": [0.32, 0.30, 0.29],
                          "openInterest": [100, 200, 300]})
    puts = pd.DataFrame({"strike": [90, 100, 110], "impliedVolatility": [0.36, 0.31, 0.30],
                         "openInterest": [400, 100, 100]})
    out = summarize_chain(calls, puts, spot=100.0, expiry="2026-08-21")
    assert out["atm_call_iv"] == 0.30 and out["atm_put_iv"] == 0.31
    assert out["call_iv_p110"] == 0.29 and out["put_iv_p90"] == 0.36
    assert out["put_skew"] == 0.05                            # 90%-strike put IV over ATM
    assert out["put_call_oi"] == 1.0                          # 600/600
