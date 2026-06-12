"""
Tool for validating ticker symbols
Pure data validation without LLM
"""
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import yfinance as yf
from langchain_core.tools import tool
import json

from ..schemas import TickerInfo

# Таймаут запроса к Yahoo Finance (секунды), чтобы тул не зависал ~30с
VALIDATE_TICKER_TIMEOUT = 10


def validate_ticker(ticker: str) -> Dict[str, Any]:
    """
    Validate a single ticker symbol from US market using Yahoo Finance
    
    Args:
        ticker: Stock ticker symbol to validate
        
    Returns:
        Dict with validation result and basic info (reason/error в явном виде при неуспехе)
    """
    ticker = ticker.upper().strip()
    
    if not ticker:
        return {"valid": False, "ticker": ticker, "reason": "Empty ticker"}
    
    ok, error_msg = validate_ticker_exists_with_timeout(ticker)
    if ok:
        return {"valid": True, "ticker": ticker, "reason": "Valid US market ticker"}
    return {"valid": False, "ticker": ticker, "reason": error_msg or "Ticker not found or not from US market"}


def validate_tickers_batch(tickers_data: List[Dict[str, Any]]) -> List[TickerInfo]:
    """
    Validate a batch of tickers from US market with relevance scores
    
    Args:
        tickers_data: List of dicts with ticker, relevance, and reason
        
    Returns:
        List of validated TickerInfo objects (US market only)
    """
    validated_tickers = []
    
    for item in tickers_data:
        ticker = item.get('ticker', '').upper().strip()
        if not ticker:
            continue
        
        # Validate with Yahoo Finance (US market only)
        if validate_ticker_exists(ticker):
            ticker_info = TickerInfo(
                ticker=ticker,
                relevance=float(item.get('relevance', 0.5)),
                reason=item.get('reason', 'mentioned in article')
            )
            validated_tickers.append(ticker_info)
        else:
            print(f"Ticker {ticker} not found or not from US market, skipping")
    
    # Sort by relevance
    validated_tickers.sort(key=lambda x: x.relevance, reverse=True)
    
    return validated_tickers


def _validate_ticker_exists_impl(ticker: str) -> tuple[bool, str]:
    """Внутренняя проверка через Yahoo Finance. Возвращает (ok, error_message)."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info:
            return (False, "No data from Yahoo Finance")
        if "symbol" not in info:
            return (False, "No symbol in Yahoo response")
        symbol = (info.get("symbol") or ticker).upper()
        if symbol != ticker and not (info.get("shortName") or info.get("longName")):
            return (False, f"Symbol mismatch: got {symbol!r}")

        exchange = (info.get("exchange") or "").upper()
        country = (info.get("country") or "").upper()
        quote_type = (info.get("quoteType") or "").upper()

        us_exchanges = [
            "NYSE", "NASDAQ", "NYQ", "NMS", "NGM", "NCM",
            "AMEX", "ARCA", "BATS", "PCX", "ETF",
        ]
        is_us_exchange = any(ex in exchange for ex in us_exchanges)
        is_us_country = country in ("UNITED STATES", "USA", "US")

        if quote_type == "ETF" and symbol == ticker:
            return (True, "")
        if is_us_exchange or is_us_country:
            return (True, "")
        return (False, f"Not US market: exchange={exchange!r}, country={country!r}")
    except Exception as e:
        return (False, f"Yahoo Finance error: {e!s}")


def validate_ticker_exists_with_timeout(ticker: str) -> tuple[bool, str]:
    """
    Проверка тикера с таймаутом. Возвращает (valid, error_message).
    При valid=True error_message пустая; при valid=False — явный текст ошибки.
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_validate_ticker_exists_impl, ticker)
        try:
            return fut.result(timeout=VALIDATE_TICKER_TIMEOUT)
        except FuturesTimeoutError:
            return (False, "Validation timeout (Yahoo Finance slow or unavailable)")


def validate_ticker_exists(ticker: str) -> bool:
    """Validate that a ticker exists in US market. Used by batch validation."""
    ok, _ = validate_ticker_exists_with_timeout(ticker)
    return ok


# ============================================================================
# LLM-Callable Tool (with @tool decorator)
# ============================================================================

def create_validate_ticker_tool():
    """Create validate_ticker tool"""
    
    @tool
    def validate_ticker_tool(ticker: str) -> str:
        """
        Validate that a ticker symbol exists and is from US market (NYSE, NASDAQ, AMEX).
        Use this to verify ticker symbols before analysis.
    
        Args:
            ticker: Stock ticker symbol to validate (e.g., "AAPL", "MSFT")
        
        Returns:
            JSON string with validation result (valid: true/false, reason)
        """
        try:
            result = validate_ticker(ticker)
            return json.dumps(result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    return validate_ticker_tool
