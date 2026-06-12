#!/usr/bin/env python3
"""
Trade Profit Simulator

Simulates trades based on agent signals from database.
- Entry: 1 minute after news publication
- Exit: 40 minutes after entry
- Profit calculated based on signal direction and price movement
"""
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta, time
from typing import Optional
import psycopg2
import pandas as pd
import matplotlib.pyplot as plt
import pytz

EASTERN = pytz.timezone("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}


def connect_db():
    """Connect to database"""
    return psycopg2.connect(**DB_CONFIG)


def get_table_name(ticker: str) -> str:
    """Get price table name for ticker"""
    clean = ticker.lower().replace('-', '_').replace('.', '_')
    return f"historical_data_{clean}"


def to_eastern(ts: datetime) -> datetime:
    """Parse news time. DB `news.published` is naive US Eastern wall clock."""
    if ts.tzinfo is None:
        return EASTERN.localize(ts)
    return ts.astimezone(EASTERN)


def parse_news_published(ts: datetime) -> datetime:
    """Use news.published (naive ET). agent_results.news_published may be mis-tagged UTC."""
    if ts.tzinfo is None:
        return EASTERN.localize(ts)
    # Naive ET stored in timestamptz column comes back as UTC with same wall clock
    if ts.utcoffset() == timedelta(0):
        return EASTERN.localize(ts.replace(tzinfo=None))
    return ts.astimezone(EASTERN)


def market_close_ts(ts: datetime) -> datetime:
    ts = to_eastern(ts)
    return ts.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)


def get_eod_price(ticker: str, day) -> Optional[float]:
    """Last close on trading day up to 16:00 ET."""
    conn = connect_db()
    cursor = conn.cursor()
    table = get_table_name(ticker)
    day = day if hasattr(day, "year") else datetime.strptime(str(day), "%Y-%m-%d").date()
    sod = EASTERN.localize(datetime.combine(day, time(9, 30)))
    eod = EASTERN.localize(datetime.combine(day, MARKET_CLOSE))
    query = f"""
        SELECT close FROM {table}
        WHERE timestamp >= %s AND timestamp <= %s
        ORDER BY timestamp DESC LIMIT 1
    """
    try:
        cursor.execute(query, (sod, eod))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        cursor.close()
        conn.close()
        return None


def fetch_signals(
    start_date: str,
    end_date: str,
    agent: Optional[str],
    min_confidence: float,
    run_after: Optional[str] = None,
    agent_version: Optional[str] = "2.0.0",
):
    """Fetch signals from agent_results table"""
    conn = connect_db()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            ar.id, ar.news_id, ar.news_title, n.published, ar.agent_name, ar.signals_json
        FROM agent_results ar
        JOIN news n ON n.id = ar.news_id
        WHERE n.published >= %s 
          AND n.published < %s
          AND ar.signals_generated > 0
    """
    params = [start_date, end_date]
    
    if agent:
        query += " AND agent_name = %s"
        params.append(agent)

    if agent_version:
        query += " AND agent_version = %s"
        params.append(agent_version)

    if run_after:
        query += " AND run_timestamp >= %s"
        params.append(run_after)
    
    query += " ORDER BY n.published"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # Parse signals and filter by confidence
    signals = []
    for row in rows:
        result_id, news_id, news_title, published, agent_name, signals_json = row
        if not signals_json:
            continue
        
        parsed = json.loads(signals_json) if isinstance(signals_json, str) else signals_json
        for sig in parsed:
            conf = sig.get('confidence', 0)
            if conf >= min_confidence:
                signals.append({
                    'result_id': result_id,
                    'news_id': news_id,
                    'news_title': news_title or f"news #{news_id}",
                    'news_published': parse_news_published(published),
                    'agent_name': agent_name,
                    'ticker': sig.get('ticker'),
                    'signal': sig.get('signal', '').lower(),
                    'confidence': conf
                })
    
    return signals


def filter_tradable_signals(signals: list) -> tuple[list, list]:
    """Split signals: tradable only if news published at/after 9:30 ET."""
    tradable, pre_market = [], []
    for sig in signals:
        if to_eastern(sig["news_published"]).time() >= MARKET_OPEN:
            tradable.append(sig)
        else:
            pre_market.append(sig)
    return tradable, pre_market


def get_price_at_time(ticker: str, timestamp: datetime) -> Optional[float]:
    """Get close price at or near timestamp (within 5 min tolerance)"""
    conn = connect_db()
    cursor = conn.cursor()
    
    table = get_table_name(ticker)
    
    # Find closest price within 5 minute window
    query = f"""
        SELECT close, timestamp
        FROM {table}
        WHERE timestamp >= %s AND timestamp <= %s
        ORDER BY ABS(EXTRACT(EPOCH FROM (timestamp - %s)))
        LIMIT 1
    """
    
    ts_start = timestamp - timedelta(minutes=5)
    ts_end = timestamp + timedelta(minutes=5)
    
    try:
        cursor.execute(query, (ts_start, ts_end, timestamp))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return float(result[0]) if result else None
    except Exception:
        cursor.close()
        conn.close()
        return None


def simulate_trades(signals: list, use_yahoo: bool = False, hold_minutes: int = 40) -> pd.DataFrame:
    """Simulate trades and calculate profits.

    If use_yahoo=True, fall back to Yahoo Finance intraday prices when the local
    OHLC table is missing/has no bar at the entry/exit time.
    """
    yahoo_price = None
    if use_yahoo:
        try:
            from trade_simulator_yahoo import get_yahoo_price_at_time as yahoo_price
        except Exception as e:
            print(f"⚠️  Yahoo fallback unavailable ({e}); using DB prices only")
            yahoo_price = None

    def _price(ticker, ts):
        p = get_price_at_time(ticker, ts)
        if p is None and yahoo_price is not None:
            p = yahoo_price(ticker, ts)
        return p

    def _resolve_exit(ticker, entry_time):
        target = entry_time + timedelta(minutes=hold_minutes)
        eod = market_close_ts(entry_time)
        exit_time = min(target, eod)
        exit_price = _price(ticker, exit_time)
        if exit_price is None:
            exit_price = get_eod_price(ticker, to_eastern(entry_time).date())
            if exit_price is not None:
                exit_time = eod
        return exit_time, exit_price

    trades = []
    
    for sig in signals:
        ticker = sig['ticker']
        if not ticker:
            continue
        
        # Entry: 1 min after news
        entry_time = sig['news_published'] + timedelta(minutes=1)
        entry_price = _price(ticker, entry_time)
        exit_time, exit_price = _resolve_exit(ticker, entry_time)
        
        if entry_price is None or exit_price is None:
            continue
        
        # Calculate profit based on direction
        if sig['signal'] == 'buy':
            profit_pct = (exit_price - entry_price) / entry_price * 100
        else:  # sell
            profit_pct = (entry_price - exit_price) / entry_price * 100
        
        trades.append({
            'news_id': sig['news_id'],
            'news_title': sig['news_title'],
            'ticker': ticker,
            'signal': sig['signal'],
            'confidence': sig['confidence'],
            'agent': sig['agent_name'],
            'entry_time': entry_time,
            'exit_time': exit_time,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'profit_pct': profit_pct,
            'win': profit_pct > 0
        })
    
    return pd.DataFrame(trades)


def _news_label(row, max_len: int = 55) -> str:
    title = (row['news_title'] or '')[:max_len]
    if len(row['news_title'] or '') > max_len:
        title += '…'
    return f"#{row['news_id']} {title} [{row['ticker']} {row['signal']}]"


def plot_by_news(df: pd.DataFrame, output_name: str = "simulation_by_news.png"):
    """Bar chart: profit per trade labeled by source news."""
    if df.empty:
        print("No trades to visualize by news")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    plot_df = df.sort_values('profit_pct').copy()
    plot_df['label'] = plot_df.apply(_news_label, axis=1)

    height = max(6, 0.45 * len(plot_df) + 1.5)
    fig, ax = plt.subplots(figsize=(14, height))
    colors = ['#28A745' if p > 0 else '#DC3545' for p in plot_df['profit_pct']]
    ax.barh(plot_df['label'], plot_df['profit_pct'], color=colors, edgecolor='white')
    ax.axvline(x=0, color='gray', linestyle='-', linewidth=1)
    ax.set_xlabel('Profit % (entry +1m → exit +40m)')
    ax.set_title('Trades by Source News')
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()

    output_path = os.path.join(script_dir, output_name)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Chart saved to: {output_path}")
    plt.show()


def plot_results(df: pd.DataFrame):
    """Generate matplotlib visualizations"""
    if df.empty:
        print("No trades to visualize")
        return
    
    # Get script directory for output
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Trading Simulation Results', fontsize=14, fontweight='bold')
    
    # 1. Cumulative P/L over time
    ax1 = axes[0, 0]
    df_sorted = df.sort_values('entry_time')
    df_sorted['cumulative_pct'] = df_sorted['profit_pct'].cumsum()
    ax1.plot(df_sorted['entry_time'], df_sorted['cumulative_pct'], 
             color='#2E86AB', linewidth=2)
    ax1.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax1.fill_between(df_sorted['entry_time'], df_sorted['cumulative_pct'], 0,
                     where=df_sorted['cumulative_pct'] >= 0, alpha=0.3, color='green')
    ax1.fill_between(df_sorted['entry_time'], df_sorted['cumulative_pct'], 0,
                     where=df_sorted['cumulative_pct'] < 0, alpha=0.3, color='red')
    ax1.set_title('Cumulative Profit/Loss (%)')
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Cumulative %')
    ax1.tick_params(axis='x', rotation=45)
    
    # 2. Win rate pie chart
    ax2 = axes[0, 1]
    wins = df['win'].sum()
    losses = len(df) - wins
    colors = ['#28A745', '#DC3545']
    ax2.pie([wins, losses], labels=[f'Wins ({wins})', f'Losses ({losses})'],
            colors=colors, autopct='%1.1f%%', startangle=90)
    ax2.set_title(f'Win Rate ({wins}/{len(df)} trades)')
    
    # 3. Profit distribution histogram
    ax3 = axes[1, 0]
    ax3.hist(df['profit_pct'], bins=20, color='#6C757D', edgecolor='white', alpha=0.7)
    ax3.axvline(x=0, color='red', linestyle='--', linewidth=2)
    ax3.axvline(x=df['profit_pct'].mean(), color='blue', linestyle='-', linewidth=2,
                label=f'Mean: {df["profit_pct"].mean():.2f}%')
    ax3.set_title('Profit Distribution')
    ax3.set_xlabel('Profit %')
    ax3.set_ylabel('Count')
    ax3.legend()
    
    # 4. Per-ticker performance
    ax4 = axes[1, 1]
    ticker_perf = df.groupby('ticker')['profit_pct'].sum().sort_values()
    colors = ['#28A745' if x > 0 else '#DC3545' for x in ticker_perf.values]
    ticker_perf.plot(kind='barh', ax=ax4, color=colors)
    ax4.axvline(x=0, color='gray', linestyle='-', linewidth=1)
    ax4.set_title('Total Profit by Ticker (%)')
    ax4.set_xlabel('Total Profit %')
    
    plt.tight_layout()
    
    output_path = os.path.join(script_dir, 'simulation_results.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"\nChart saved to: {output_path}")
    plt.show()


def print_summary(df: pd.DataFrame):
    """Print trade summary statistics"""
    if df.empty:
        print("\nNo trades executed (no price data found)")
        return
    
    print("\n" + "=" * 60)
    print("TRADE SIMULATION SUMMARY")
    print("=" * 60)
    
    print(f"\nTotal trades: {len(df)}")
    print(f"Wins: {df['win'].sum()} ({df['win'].mean()*100:.1f}%)")
    print(f"Losses: {len(df) - df['win'].sum()}")
    
    print(f"\nTotal profit: {df['profit_pct'].sum():.2f}%")
    print(f"Average profit: {df['profit_pct'].mean():.2f}%")
    print(f"Max profit: {df['profit_pct'].max():.2f}%")
    print(f"Max loss: {df['profit_pct'].min():.2f}%")

    # --- Baselines (sanity check: does the agent beat naive strategies?) -------
    # always-buy: go long every signal regardless of agent's buy/sell direction.
    raw_move = (df['exit_price'] - df['entry_price']) / df['entry_price'] * 100
    always_buy_total = raw_move.sum()
    always_buy_win = (raw_move > 0).mean() * 100
    print("\n--- Baseline comparison (no commission/spread) ---")
    print(f"Agent:      win {df['win'].mean()*100:.1f}%, total {df['profit_pct'].sum():+.2f}%")
    print(f"Always-buy: win {always_buy_win:.1f}%, total {always_buy_total:+.2f}%")
    print(f"Agent edge vs always-buy: {df['profit_pct'].sum() - always_buy_total:+.2f}%")
    
    print(f"\nBy agent:")
    for agent, group in df.groupby('agent'):
        print(f"  {agent}: {len(group)} trades, {group['profit_pct'].sum():.2f}% total")
    
    print(f"\nTop 5 tickers by profit:")
    top = df.groupby('ticker')['profit_pct'].sum().sort_values(ascending=False).head()
    for ticker, profit in top.items():
        print(f"  {ticker}: {profit:.2f}%")
    
    print("=" * 60)


def write_signals_table(
    signals: list,
    trades_df: Optional[pd.DataFrame] = None,
    output_name: str = "signals_table.csv",
    min_confidence: float = 0.0,
) -> pd.DataFrame:
    """Print and save signals with datetime columns."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    trade_map = {}
    if trades_df is not None and not trades_df.empty:
        for _, r in trades_df.iterrows():
            trade_map[(r["news_id"], r["ticker"])] = r

    rows = []
    for sig in signals:
        pub = to_eastern(sig["news_published"])
        row = {
            "news_id": sig["news_id"],
            "time_published_et": pub.strftime("%Y-%m-%d %H:%M:%S"),
            "news_title": (sig.get("news_title") or "")[:80],
            "ticker": sig.get("ticker"),
            "signal": sig.get("signal"),
            "confidence": sig.get("confidence"),
            "agent": sig.get("agent_name"),
        }
        key = (sig["news_id"], sig.get("ticker"))
        pre_market = pub.time() < MARKET_OPEN
        low_conf = sig.get("confidence", 0) < min_confidence
        if pre_market:
            row.update({"entry_time": "", "exit_time": "", "entry_price": "", "exit_price": "", "profit_pct": "", "status": "pre_market"})
        elif low_conf:
            row.update({"entry_time": "", "exit_time": "", "entry_price": "", "exit_price": "", "profit_pct": "", "status": "below_confidence"})
        elif key in trade_map:
            t = trade_map[key]
            row["entry_time"] = to_eastern(t["entry_time"]).strftime("%Y-%m-%d %H:%M:%S")
            row["exit_time"] = to_eastern(t["exit_time"]).strftime("%Y-%m-%d %H:%M:%S")
            row["entry_price"] = round(float(t["entry_price"]), 4)
            row["exit_price"] = round(float(t["exit_price"]), 4)
            row["profit_pct"] = round(float(t["profit_pct"]), 2)
            row["status"] = "executed"
        else:
            row.update({"entry_time": "", "exit_time": "", "entry_price": "", "exit_price": "", "profit_pct": "", "status": "skipped"})
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("time_published_et")
    output_path = os.path.join(script_dir, output_name)
    df.to_csv(output_path, index=False)
    print("\n" + "=" * 60)
    print("SIGNALS TABLE")
    print("=" * 60)
    print(df.to_string(index=False))
    print(f"\nSaved to: {output_path}")
    return df


def get_available_agents():
    """Get list of available agents from database"""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT agent_name FROM agent_results ORDER BY agent_name")
    agents = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return agents


def get_date_range():
    """Get min/max dates available in database"""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(news_published), MAX(news_published) FROM agent_results")
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result[0], result[1]


def run_simulation(
    start_date: str,
    end_date: str,
    agent: Optional[str],
    min_confidence: float,
    run_after: Optional[str] = None,
    agent_version: Optional[str] = "2.0.0",
    by_news_output: str = "simulation_by_news.png",
    use_yahoo: bool = False,
    hold_minutes: int = 40,
):
    """Run trade simulation with given parameters"""
    print(f"\nFetching signals from {start_date} to {end_date}")
    print(f"Agent: {agent or 'all'}")
    print(f"Agent version: {agent_version or 'all'}")
    print(f"Min confidence: {min_confidence}")
    print(f"Hold time: {hold_minutes} min")
    if run_after:
        print(f"Run after: {run_after} (batch session filter)")
    if use_yahoo:
        print("Price source: DB, then Yahoo fallback")

    signals = fetch_signals(
        start_date, end_date, agent, min_confidence,
        run_after=run_after, agent_version=agent_version,
    )
    tradable, pre_market = filter_tradable_signals(signals)
    print(f"Found {len(signals)} signals ({len(pre_market)} before 9:30 ET excluded)")

    all_signals = fetch_signals(
        start_date, end_date, agent, min_confidence=0.0,
        run_after=run_after, agent_version=agent_version,
    )
    
    if not tradable:
        print("No tradable signals after 9:30 ET filter")
        if all_signals or signals:
            write_signals_table(all_signals or signals, None, min_confidence=min_confidence)
        return
    
    print("\nSimulating trades...")
    df = simulate_trades(tradable, use_yahoo=use_yahoo, hold_minutes=hold_minutes)
    
    print_summary(df)
    write_signals_table(all_signals or signals, df, min_confidence=min_confidence)
    plot_results(df)
    plot_by_news(df, output_name=by_news_output)


def interactive_menu():
    """Interactive CLI menu"""
    while True:
        print("\n" + "=" * 60)
        print("TRADE PROFIT SIMULATOR")
        print("=" * 60)
        print("\n1. Run simulation")
        print("2. View available agents")
        print("3. View available date range")
        print("4. Exit")
        
        choice = input("\nSelect option (1-4): ").strip()
        
        if choice == '1':
            # Get date range
            min_date, max_date = get_date_range()
            if min_date and max_date:
                print(f"\nAvailable data: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
            
            start = input("Start date (YYYY-MM-DD): ").strip()
            end = input("End date (YYYY-MM-DD): ").strip()
            
            # Agent selection
            agents = get_available_agents()
            print(f"\nAvailable agents: {', '.join(agents)}")
            agent_input = input("Agent name (leave empty for all): ").strip()
            agent = agent_input if agent_input else None
            
            # Confidence
            conf_input = input("Min confidence (default 0.8): ").strip()
            min_conf = float(conf_input) if conf_input else 0.8
            
            run_simulation(start, end, agent, min_conf)
            
        elif choice == '2':
            agents = get_available_agents()
            print(f"\nAvailable agents:")
            for a in agents:
                print(f"  - {a}")
                
        elif choice == '3':
            min_date, max_date = get_date_range()
            if min_date and max_date:
                print(f"\nData available from {min_date.strftime('%Y-%m-%d %H:%M')} to {max_date.strftime('%Y-%m-%d %H:%M')}")
            else:
                print("\nNo data available")
                
        elif choice == '4':
            print("Goodbye!")
            break
        else:
            print("Invalid option")


def main():
    parser = argparse.ArgumentParser(description='Simulate trades from agent signals')
    parser.add_argument('--start-date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--agent', help='Agent name filter (simple_agent/complex_agent)')
    parser.add_argument('--min-confidence', type=float, default=0.8,
                        help='Minimum confidence threshold (default: 0.8)')
    parser.add_argument('--run-after', metavar='DATETIME',
                        help='Only agent_results with run_timestamp >= this (e.g. 2026-05-31 19:00:00)')
    parser.add_argument('--agent-version', default='2.0.0',
                        help='Filter by agent_version (default: 2.0.0)')
    parser.add_argument('--use-yahoo', action='store_true',
                        help='Fall back to Yahoo Finance prices when local OHLC is missing')
    parser.add_argument('--hold-minutes', type=int, default=40,
                        help='Minutes to hold after entry (default: 40)')
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='Run in interactive mode')
    
    args = parser.parse_args()
    
    # Interactive mode if no dates provided or -i flag
    if args.interactive or (not args.start_date and not args.end_date):
        interactive_menu()
    else:
        if not args.start_date or not args.end_date:
            parser.error("Both --start-date and --end-date are required for non-interactive mode")
        run_simulation(
            args.start_date,
            args.end_date,
            args.agent,
            args.min_confidence,
            run_after=args.run_after,
            agent_version=args.agent_version,
            by_news_output="simulation_by_news_session.png" if args.run_after else "simulation_by_news.png",
            use_yahoo=args.use_yahoo,
            hold_minutes=args.hold_minutes,
        )


if __name__ == "__main__":
    main()

