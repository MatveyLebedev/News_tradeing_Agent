"""Ticker → common news name(s).

The Neo4j Company nodes have a null `name`, and news outlets refer to companies by
their common name, not their ticker. This curated map gives the ILIKE recall path a
name to match. Values are the substrings most likely to appear in Bloomberg/CNBC copy
(deliberately short — "Meta" not "Meta Platforms, Inc."). Tickers not listed fall back
to a ticker-symbol ILIKE only.
"""

COMPANY_NAMES = {
    "AAPL": ["Apple"],
    "MSFT": ["Microsoft"],
    "GOOGL": ["Alphabet", "Google"],
    "GOOG": ["Alphabet", "Google"],
    "AMZN": ["Amazon"],
    "NVDA": ["Nvidia", "NVIDIA"],
    "META": ["Meta", "Facebook", "Instagram"],
    "TSLA": ["Tesla"],
    "AVGO": ["Broadcom"],
    "ORCL": ["Oracle"],
    "AMD": ["AMD", "Advanced Micro"],
    "INTC": ["Intel"],
    "IBM": ["IBM"],
    "CRM": ["Salesforce"],
    "ADBE": ["Adobe"],
    "CSCO": ["Cisco"],
    "QCOM": ["Qualcomm"],
    "TXN": ["Texas Instruments"],
    "AMAT": ["Applied Materials"],
    "MU": ["Micron"],
    "NOW": ["ServiceNow"],
    "INTU": ["Intuit"],
    "PANW": ["Palo Alto"],
    "NFLX": ["Netflix"],
    "DIS": ["Disney"],
    "CMCSA": ["Comcast"],
    "T": ["AT&T"],
    "VZ": ["Verizon"],
    "TMUS": ["T-Mobile"],
    "PYPL": ["PayPal"],
    "UBER": ["Uber"],
    "ABNB": ["Airbnb"],
    "JPM": ["JPMorgan", "JP Morgan"],
    "BAC": ["Bank of America"],
    "WFC": ["Wells Fargo"],
    "GS": ["Goldman Sachs"],
    "MS": ["Morgan Stanley"],
    "C": ["Citigroup", "Citibank"],
    "V": ["Visa"],
    "MA": ["Mastercard"],
    "BRK-B": ["Berkshire"],
    "WMT": ["Walmart"],
    "HD": ["Home Depot"],
    "COST": ["Costco"],
    "NKE": ["Nike"],
    "MCD": ["McDonald"],
    "SBUX": ["Starbucks"],
    "KO": ["Coca-Cola", "Coca Cola"],
    "PEP": ["PepsiCo", "Pepsi"],
    "PG": ["Procter & Gamble", "Procter and Gamble"],
    "XOM": ["Exxon"],
    "CVX": ["Chevron"],
    "COP": ["ConocoPhillips"],
    "BA": ["Boeing"],
    "CAT": ["Caterpillar"],
    "GE": ["General Electric", "GE Aerospace"],
    "LMT": ["Lockheed"],
    "RTX": ["RTX", "Raytheon"],
    "UNH": ["UnitedHealth"],
    "JNJ": ["Johnson & Johnson"],
    "LLY": ["Eli Lilly", "Lilly"],
    "PFE": ["Pfizer"],
    "MRK": ["Merck"],
    "ABBV": ["AbbVie"],
}


def names_for(ticker: str) -> list:
    """Return the list of news-name substrings for a ticker (may be empty)."""
    return COMPANY_NAMES.get(ticker.upper(), [])
