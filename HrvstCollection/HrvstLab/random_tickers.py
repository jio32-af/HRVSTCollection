import random
import datetime
import pandas as pd
import yfinance as yf
import warnings
import sys
import os
import threading
import time as _time
from dataclasses import dataclass
from typing import List, Dict

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Suppress noisy yfinance warnings globally (no output redirection at import time,
# so `import random_tickers` stays side-effect free and testable).
warnings.filterwarnings('ignore', message='.*5m data not available.*')
warnings.filterwarnings('ignore', message='.*delisted.*')
stderr_original = sys.stderr

@dataclass
class Transaction:
    ticker: str
    transaction_type: str  # 'BUY' or 'SELL'
    timestamp: datetime.datetime
    price: float
    shares: int
    cost_basis: float = 0.0  # For SELL transactions, the cost basis of shares sold (FIFO method)
    buy_timestamp: datetime.datetime = None  # When the shares were bought (for tax calculation)
    
    def __str__(self):
        action = "BUY" if self.transaction_type == "BUY" else "SELL"
        return f"{action:4} {self.shares:3} {self.ticker} @ ${self.price:.2f} | {self.timestamp}"
    
    @property
    def total_value(self):
        return self.shares * self.price
    
    @property
    def realized_gain_loss(self):
        """Calculate realized gain/loss for SELL transactions using FIFO cost basis"""
        if self.transaction_type == "SELL":
            return self.total_value - self.cost_basis
        return 0.0

def load_tickers(file_path):
    with open(file_path, 'r') as file:
        tickers = [line.strip() for line in file if line.strip()]
    return tickers

def is_valid_trading_time(timestamp):
    # Check if the timestamp is a weekday (Monday to Friday)
    if timestamp.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    # Check if the hour is before 9:30 AM or after 5:00 PM
    if timestamp.hour < 9 or (timestamp.hour == 9 and timestamp.minute < 30) or timestamp.hour >= 16:
        return False
    # Check for US market holidays
    if is_market_holiday(timestamp):
        return False
    return True

def is_market_holiday(timestamp):
    """Check if the given timestamp falls on a US market holiday"""
    year = timestamp.year
    month = timestamp.month
    day = timestamp.day
    
    # New Year's Day (January 1)
    if month == 1 and day == 1:
        return True
    
    # Martin Luther King Jr. Day (third Monday in January)
    if month == 1 and timestamp.weekday() == 0 and 15 <= day <= 21:
        return True
    
    # Presidents' Day (third Monday in February)
    if month == 2 and timestamp.weekday() == 0 and 15 <= day <= 21:
        return True
    
    # Good Friday (Friday before Easter)
    # Approximate calculation (not exact for all years)
    if month == 3 or month == 4:
        if timestamp.weekday() == 4:  # Friday
            # This is a simplified check - for exact dates would need Easter calculation
            pass
    
    # Memorial Day (last Monday in May)
    if month == 5 and timestamp.weekday() == 0 and day >= 25:
        return True
    
    # Juneteenth (June 19)
    if month == 6 and day == 19:
        return True
    
    # Independence Day (July 4)
    if month == 7 and day == 4:
        return True
    
    # Labor Day (first Monday in September)
    if month == 9 and timestamp.weekday() == 0 and day <= 7:
        return True
    
    # Thanksgiving Day (fourth Thursday in November)
    if month == 11 and timestamp.weekday() == 3 and 22 <= day <= 28:
        return True
    
    # Christmas Day (December 25)
    if month == 12 and day == 25:
        return True
    
    return False

def is_major_corporate_event(ticker, timestamp):
    """Check if ticker had major corporate events around this time"""
    # Dictionary of known major acquisitions/mergers (ticker: date_range)
    major_events = {
        'TWTR': ('2022-10-27', '2022-11-01'),  # Elon Musk acquisition
        'FTX': ('2022-11-08', '2022-11-15'),   # FTX collapse
        'ATVI': ('2023-10-13', '2023-10-20'),  # Microsoft acquisition
        'FB': ('2021-10-28', '2021-11-05'),    # Meta rebranding
        'BABA': ('2020-11-03', '2020-11-10'),  # Ant Group IPO halted
        'NK': ('2021-08-20', '2021-08-27'),    # Nook/Kobo changes
        'TSLA': ('2022-04-08', '2022-04-15'),  # Twitter stake announcement
        'SNAP': ('2022-05-24', '2022-05-31'),  # Major privacy changes
    }
    
    if ticker in major_events:
        event_start, event_end = major_events[ticker]
        event_start_date = datetime.datetime.strptime(event_start, '%Y-%m-%d')
        event_end_date = datetime.datetime.strptime(event_end, '%Y-%m-%d')
        
        # Check if timestamp is within the event window
        if event_start_date <= timestamp <= event_end_date:
            return True
    
    return False

def validate_accounting(transactions: List[Transaction], initial_cash: float = 50000.0) -> bool:
    """
    Validate accounting consistency across all transactions.
    
    Cost Basis Method: FIFO (First-In, First-Out)
    - When selling shares, we use the cost basis of the oldest purchased shares first
    - This is documented and consistent throughout the system
    
    Cash Accounting Rules:
    - Every BUY decreases cash by exactly: shares × price
    - Every SELL increases cash by exactly: shares × price
    - Starting cash is fixed at $50,000 for reproducibility
    
    Returns: True if accounting is valid, False otherwise
    """
    cash_balance = initial_cash
    holdings = {}  # {ticker: {'shares': int, 'cost_basis': [{'shares': int, 'price': float}, ...]}}
    
    for i, transaction in enumerate(transactions, 1):
        ticker = transaction.ticker
        
        if transaction.transaction_type == 'BUY':
            expected_cash_change = -transaction.total_value
            if abs(cash_balance + expected_cash_change - (cash_balance - transaction.total_value)) > 0.01:
                print(f"❌ Accounting error at transaction {i}: BUY cash change mismatch")
                return False
            
            cash_balance -= transaction.total_value
            
            if ticker not in holdings:
                holdings[ticker] = {'shares': 0, 'cost_basis': []}
            
            holdings[ticker]['shares'] += transaction.shares
            holdings[ticker]['cost_basis'].append({'shares': transaction.shares, 'price': transaction.price})
            
        elif transaction.transaction_type == 'SELL':
            expected_cash_change = transaction.total_value
            if abs(cash_balance + expected_cash_change - (cash_balance + transaction.total_value)) > 0.01:
                print(f"❌ Accounting error at transaction {i}: SELL cash change mismatch")
                return False
            
            # Verify we own enough shares
            if ticker not in holdings or holdings[ticker]['shares'] < transaction.shares:
                print(f"❌ Accounting error at transaction {i}: Selling more shares than owned")
                return False
            
            # Verify cost basis calculation using FIFO
            calculated_cost_basis = 0.0
            shares_to_cost = transaction.shares
            temp_cost_basis = holdings[ticker]['cost_basis'].copy()
            
            while shares_to_cost > 0 and temp_cost_basis:
                oldest_cost = temp_cost_basis[0]
                if oldest_cost['shares'] <= shares_to_cost:
                    calculated_cost_basis += oldest_cost['shares'] * oldest_cost['price']
                    shares_to_cost -= oldest_cost['shares']
                    temp_cost_basis.pop(0)
                else:
                    calculated_cost_basis += shares_to_cost * oldest_cost['price']
                    oldest_cost['shares'] -= shares_to_cost
                    shares_to_cost = 0
            
            if abs(calculated_cost_basis - transaction.cost_basis) > 0.01:
                print(f"❌ Accounting error at transaction {i}: Cost basis mismatch (calculated: ${calculated_cost_basis:.2f}, recorded: ${transaction.cost_basis:.2f})")
                return False
            
            # Verify realized gain/loss calculation
            expected_realized_gain = transaction.total_value - transaction.cost_basis
            if abs(expected_realized_gain - transaction.realized_gain_loss) > 0.01:
                print(f"❌ Accounting error at transaction {i}: Realized gain/loss mismatch")
                return False
            
            cash_balance += transaction.total_value
            holdings[ticker]['shares'] -= transaction.shares
            
            # Actually remove from cost basis queue (real FIFO)
            shares_to_remove = transaction.shares
            while shares_to_remove > 0 and holdings[ticker]['cost_basis']:
                oldest_cost = holdings[ticker]['cost_basis'][0]
                if oldest_cost['shares'] <= shares_to_remove:
                    shares_to_remove -= oldest_cost['shares']
                    holdings[ticker]['cost_basis'].pop(0)
                else:
                    oldest_cost['shares'] -= shares_to_remove
                    shares_to_remove = 0
            
            if holdings[ticker]['shares'] == 0:
                del holdings[ticker]
    
    print(f"✅ Accounting validation passed for {len(transactions)} transactions")
    return True

def _cache_path():
    import os as _os
    import tempfile as _tf
    base = _os.environ.get("HRVST_CACHE", _os.path.join(_tf.gettempdir(), "HRVST"))
    _os.makedirs(base, exist_ok=True)
    return _os.path.join(base, "prices.json")


def _disk_cache_read():
    import json as _json
    import os as _os
    path = _cache_path()
    if not _os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = _json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CACHE_LOCK = threading.Lock()


def _disk_cache_write(patch: Dict):
    import json as _json
    with _CACHE_LOCK:
        try:
            data = _disk_cache_read()
            data.update(patch)
            if len(data) > 3000:  # prune oldest-ish entries by insertion order
                data = dict(list(data.items())[-3000:])
            with open(_cache_path(), "w") as fh:
                _json.dump(data, fh)
        except Exception:
            pass


_FETCH_WORKERS = 8
_LIVE_TTL = 60.0  # seconds; repeat views/refreshes within a minute hit memory, not Yahoo
_LIVE_CACHE: Dict[str, tuple] = {}  # ticker -> (epoch_seconds, price)


def _map_concurrent(fn, items, max_workers=_FETCH_WORKERS):
    """Run `fn` over `items` on a thread pool (blocking Yahoo I/O overlaps).

    Order-preserving; a failing item yields None instead of aborting the
    batch. Single items skip the pool so tests with strict stubs stay sync.
    """

    def _safe(item):
        try:
            return fn(item)
        except Exception:
            return None

    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [_safe(items[0])]
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
        return list(ex.map(_safe, items))


def _pick_buy_prices(closes, entries) -> Dict:
    """Pure map: {(TICKER, date_iso): price} from a daily-closes DataFrame.

    `closes` columns are tickers (or a single Series), index is dates.
    Each entry takes the last close on or before its buy date (weekends and
    holidays walk back automatically). Missing tickers stay absent.
    """
    out: Dict = {}
    for ticker_raw, date_raw, *_ in entries:
        ticker = (ticker_raw or "").strip().upper()
        if not ticker:
            continue
        try:
            if isinstance(date_raw, datetime.datetime):
                day = date_raw.date()
            elif isinstance(date_raw, datetime.date):
                day = date_raw
            else:
                day = datetime.datetime.strptime(str(date_raw).strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError, AttributeError):
            continue
        try:
            col = closes[ticker].dropna() if hasattr(closes, "columns") and ticker in closes.columns else closes.dropna()
            past = col[col.index.date <= day] if hasattr(col.index, "date") else col
            if len(past):
                out[(ticker, day.isoformat())] = float(past.iloc[-1])
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
    return out


def batch_buy_prices(entries: List, days_buffer: int = 7) -> Dict:
    """One batched download pricing every entry's buy date (plus disk cache).

    Historical closes (>30 days old) are cached on disk forever since they
    never change; recent dates always refetch. Returns {(TICKER, date): price}.
    """
    dated = []
    for item in entries:
        try:
            raw = item[1]
            if isinstance(raw, datetime.datetime):
                day = raw.date()
            elif isinstance(raw, datetime.date):
                day = raw
            else:
                day = datetime.datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
            dated.append((item, day))
        except (ValueError, TypeError, AttributeError, IndexError):
            continue
    if not dated:
        return {}
    tickers = sorted({(item[0] or "").strip().upper() for item, _ in dated if item[0]})
    start = min(day for _, day in dated) - datetime.timedelta(days=days_buffer)
    today = datetime.datetime.now().date()
    month_ago = today - datetime.timedelta(days=30)

    cache = _disk_cache_read()
    out, fresh = {}, {}
    for item, day in dated:
        ticker = item[0].strip().upper()
        key = f"{ticker}|{day.isoformat()}"
        if day <= month_ago and key in cache:
            try:
                out[(ticker, day.isoformat())] = float(cache[key])
                continue
            except (TypeError, ValueError):
                pass
        fresh.setdefault(ticker, []).append((item, day))
    if not fresh:
        return out
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = yf.download(" ".join(fresh), start=start.isoformat(),
                               end=today.isoformat(), progress=False,
                               auto_adjust=False, threads=True)
        closes = data["Close"] if data is not None and not data.empty else None
    except Exception:
        closes = None
    if closes is not None:
        need = [item for sub in fresh.values() for item, _ in sub]
        picked = _pick_buy_prices(closes, need)
        out.update(picked)
        patch = {f"{t}|{d}": p for (t, d), p in picked.items()
                 if datetime.date.fromisoformat(d) <= month_ago}
        if patch:
            _disk_cache_write(patch)
    return out


def get_price_at(ticker: str, timestamp: datetime.datetime):
    """Fetch the historical price of `ticker` near `timestamp`.

    Tries 5-minute intraday data first, then falls back to daily data.
    Returns the price as a float, or None when no data is available.

    Hits are cached on disk forever: history never changes, so refreshes,
    triage re-runs, and redeploys never re-fetch the same date twice.
    """
    key = None
    try:
        day = timestamp.date() if isinstance(timestamp, datetime.datetime) else timestamp
        key = f"PX|{(ticker or '').strip().upper()}|{day.isoformat()}"
        hit = _disk_cache_read().get(key)
        if hit is not None:
            return float(hit)
    except Exception:
        pass
    price = _get_price_at_uncached(ticker, timestamp)
    if price is not None and key is not None:
        try:
            _disk_cache_write({key: float(price)})
        except Exception:
            pass
    return price


def _get_price_at_uncached(ticker: str, timestamp: datetime.datetime):
    try:
        stock = yf.Ticker(ticker)
        start_date = timestamp.strftime('%Y-%m-%d')
        end_date = (timestamp + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='.*5m data not available.*')
            try:
                hist = stock.history(start=start_date, end=end_date, interval="5m")
            except Exception:
                hist = None
            if hist is not None and not hist.empty:
                hist = hist.copy()
                hist['time_diff'] = abs(hist.index.tz_localize(None) - timestamp)
                return float(hist.loc[hist['time_diff'].idxmin()]['Close'])

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                daily_hist = stock.history(start=start_date, end=end_date, interval="1d")
            except Exception:
                return None
            if daily_hist is not None and not daily_hist.empty:
                return float(daily_hist.iloc[-1]['Close'])
    except Exception:
        return None
    return None


def generate_transactions(num_transactions: int = 10, budget: float = 50000.0) -> List[Transaction]:
    """
    Generate a list of chronologically ordered, valid BUY/SELL transactions with proper accounting.
    
    Accounting Method:
    - Cost Basis: FIFO (First-In, First-Out) for share cost tracking
    - Cash: Fixed starting amount of $50,000 for reproducibility
    - Every transaction updates cash balance by exactly shares × price
    - SELL transactions only allowed when sufficient shares are owned
    """
    # Load all available tickers
    nasdaq_tickers = load_tickers('NASDAQ')
    sp500_tickers = load_tickers('S&P500')
    rus1000_tickers = load_tickers('Rus1000')
    all_tickers = nasdaq_tickers + sp500_tickers + rus1000_tickers
    
    transactions = []
    cash_balance = budget
    # Track holdings with cost basis and buy timestamps: {ticker: {'shares': int, 'cost_basis': [{'shares': int, 'price': float, 'timestamp': datetime.datetime}, ...]}}
    holdings = {}  # Dictionary to track cost basis and buy timestamps for each ticker (FIFO queue)
    attempts = 0
    max_attempts = 2000  # Prevent infinite loop
    
    # Create progress bar
    if TQDM_AVAILABLE:
        pbar = tqdm(total=num_transactions, desc="Generating transactions")
    else:
        pbar = None
    
    # Start from earliest date and move forward
    current_date = datetime.datetime(2020, 1, 1)
    
    while len(transactions) < num_transactions and attempts < max_attempts:
        attempts += 1
        
        # Update progress display
        if not pbar and attempts % 10 == 0:
            print(f"Progress: {len(transactions)}/{num_transactions} transactions", end='\r')
        
        # Generate timestamp that moves forward in time
        days_to_add = random.randint(1, 180)  # Add 1-180 days
        current_date = current_date + datetime.timedelta(days=days_to_add)
        
        if current_date > datetime.datetime.now():
            current_date = datetime.datetime(2020, 1, 1)  # Reset if we go too far
        
        # Generate time during trading hours
        random_hour = random.randint(9, 15)
        random_minute = random.randint(0, 59)
        if random_hour == 9:
            random_minute = max(30, random_minute)  # After 9:30 AM
        
        random_timestamp = current_date.replace(hour=random_hour, minute=random_minute, second=random.randint(0, 59))
        
        # Skip if not valid trading time
        if not is_valid_trading_time(random_timestamp):
            continue
        
        # Determine transaction type based on holdings and cash
        owned_tickers = [t for t in holdings if holdings[t]['shares'] > 0]
        
        # 70% chance to SELL if we have holdings and sufficient cash buffer
        if owned_tickers and len(transactions) >= 3 and random.random() < 0.7:
            transaction_type = 'SELL'
            ticker = random.choice(owned_tickers)
        else:
            transaction_type = 'BUY'
            ticker = random.choice(all_tickers)
        
        # Skip if ticker had major corporate events around this time
        if is_major_corporate_event(ticker, random_timestamp):
            continue
        
        try:
            # Historical price for this ticker on this date (intraday, else daily)
            price = get_price_at(ticker, random_timestamp)
            if price is None:
                continue  # Skip tickers with no data on this date
            
            # Generate shares based on transaction type
            if transaction_type == 'BUY':
                # Generate random shares (1-100 for larger transactions to use more budget)
                shares = random.randint(1, 100)
                cost = shares * price
                
                # Check if we can afford this purchase
                if cost > cash_balance:
                    # Try to reduce shares to fit budget
                    max_shares = int(cash_balance / price)
                    if max_shares >= 1:
                        shares = max_shares
                        cost = shares * price
                    else:
                        continue  # Skip if even 1 share is too expensive
                        
                cost_basis_per_share = price
                
            else:  # SELL
                # Calculate sell amount based on holdings
                if ticker in holdings and holdings[ticker]['shares'] > 0:
                    # 5% chance to sell 50-70% of holdings, otherwise 20-30%
                    if random.random() < 0.05:
                        sell_percentage = random.uniform(0.5, 0.7)
                    else:
                        sell_percentage = random.uniform(0.2, 0.3)
                    shares = max(1, round(holdings[ticker]['shares'] * sell_percentage))
                    
                    # Ensure we don't sell more than we own
                    if shares > holdings[ticker]['shares']:
                        shares = holdings[ticker]['shares']
                    
                    # Calculate cost basis using FIFO
                    cost_basis = 0.0
                    shares_to_cost = shares
                    temp_cost_basis = holdings[ticker]['cost_basis'].copy()
                    
                    while shares_to_cost > 0 and temp_cost_basis:
                        oldest_cost = temp_cost_basis[0]
                        if oldest_cost['shares'] <= shares_to_cost:
                            cost_basis += oldest_cost['shares'] * oldest_cost['price']
                            shares_to_cost -= oldest_cost['shares']
                            temp_cost_basis.pop(0)
                        else:
                            cost_basis += shares_to_cost * oldest_cost['price']
                            oldest_cost['shares'] -= shares_to_cost
                            shares_to_cost = 0
                    
                    cost_basis_per_share = cost_basis / shares if shares > 0 else 0
                else:
                    continue  # Skip if don't own any shares
            
            # Create transaction with buy timestamp for tax calculation
            if transaction_type == 'SELL':
                # Get the buy timestamp for tax calculation (from the oldest shares being sold)
                buy_timestamp = None
                shares_to_track = shares
                temp_cost_basis = holdings[ticker]['cost_basis'].copy()
                while shares_to_track > 0 and temp_cost_basis:
                    oldest_cost = temp_cost_basis[0]
                    if oldest_cost['shares'] <= shares_to_track:
                        if buy_timestamp is None:
                            buy_timestamp = oldest_cost['timestamp']
                        shares_to_track -= oldest_cost['shares']
                        temp_cost_basis.pop(0)
                    else:
                        if buy_timestamp is None:
                            buy_timestamp = oldest_cost['timestamp']
                        shares_to_track = 0
            else:
                buy_timestamp = None
            
            transaction = Transaction(
                ticker=ticker,
                transaction_type=transaction_type,
                timestamp=random_timestamp,
                price=price,
                shares=shares,
                cost_basis=cost_basis if transaction_type == 'SELL' else 0.0,
                buy_timestamp=buy_timestamp
            )
            
            # Update holdings and cash
            if transaction_type == 'BUY':
                if ticker not in holdings:
                    holdings[ticker] = {'shares': 0, 'cost_basis': []}
                
                holdings[ticker]['shares'] += shares
                holdings[ticker]['cost_basis'].append({'shares': shares, 'price': price, 'timestamp': random_timestamp})
                cash_balance -= cost
                
            else:  # SELL
                holdings[ticker]['shares'] -= shares
                cash_balance += shares * price
                
                # Actually remove from cost basis queue (real FIFO)
                shares_to_remove = shares
                while shares_to_remove > 0 and holdings[ticker]['cost_basis']:
                    oldest_cost = holdings[ticker]['cost_basis'][0]
                    if oldest_cost['shares'] <= shares_to_remove:
                        shares_to_remove -= oldest_cost['shares']
                        holdings[ticker]['cost_basis'].pop(0)
                    else:
                        oldest_cost['shares'] -= shares_to_remove
                        shares_to_remove = 0
                
                # Clean up if no shares left
                if holdings[ticker]['shares'] == 0:
                    del holdings[ticker]
            
            transactions.append(transaction)
            
            # Update progress
            if pbar:
                pbar.update(1)
            else:
                print(f"Progress: {len(transactions)}/{num_transactions} transactions", end='\r')
            
        except Exception:
            # Skip this ticker and try another
            continue
    
    # Close progress bar
    if pbar:
        pbar.close()
    else:
        print()  # New line after progress
    
    # Transactions are already in chronological order, no need to sort
    # Ensure transactions is a list of Transaction objects
    if not isinstance(transactions, list) or not transactions:
        transactions = []
    
    # Generate transactions if none are provided
    if not transactions:
        transactions = generate_transactions(num_transactions=10)
    
    # Debugging: Print type of transactions
    print(f"Type of transactions: {type(transactions)}")
    if transactions:
        print(f"Number of transactions: {len(transactions)}")
        if len(transactions) > 0:
            print(f"First transaction type: {type(transactions[0])}")
    return transactions

def calculate_portfolio(transactions: List[Transaction]) -> tuple[Dict[str, Dict[str, int]], float, float]:
    """
    Calculate current portfolio holdings, cash balance, and gains from transactions.
    
    Cost Basis Tracking: 
    - Uses transaction cost basis directly for consistent accounting
    - Realized gains come from transaction records (FIFO method already applied)
    - Unrealized gains calculated from current holdings vs their remaining cost basis
    """
    portfolio = {}
    cash_balance = 50000.0  # Fixed starting amount for reproducibility
    total_realized_gains = 0.0
    
    for transaction in transactions:
        ticker = transaction.ticker
        
        if ticker not in portfolio:
            portfolio[ticker] = {'bought': 0, 'sold': 0, 'owned': 0, 'cost_basis': 0.0}
        
        if transaction.transaction_type == 'BUY':
            portfolio[ticker]['bought'] += transaction.shares
            portfolio[ticker]['owned'] += transaction.shares
            portfolio[ticker]['cost_basis'] += transaction.total_value
            cash_balance -= transaction.total_value
        else:  # SELL
            portfolio[ticker]['sold'] += transaction.shares
            portfolio[ticker]['owned'] -= transaction.shares
            cash_balance += transaction.total_value
            total_realized_gains += transaction.realized_gain_loss
            # Use the transaction's own cost basis for consistency
            portfolio[ticker]['cost_basis'] -= transaction.cost_basis
    
    return portfolio, cash_balance, total_realized_gains

def print_portfolio_summary(portfolio: Dict[str, Dict[str, int]]):
    """Prints the current portfolio holdings."""
    
    print("\n" + "=" * 50)
    print("PORTFOLIO SUMMARY")
    print("=" * 50)
    
    for ticker, data in portfolio.items():
        print(f"\n{ticker}")
        print(f"Bought: {data['bought']}")
        print(f"Sold:   {data['sold']}")
        print(f"Cost Basis: ${data['cost_basis']:.2f}")
        print("──────────")
        print(f"Owned:  {data['owned']}")
    
    print("\n" + "=" * 50)

def print_tax_summary(tax_summary: Dict[str, float]):
    """Prints the tax summary for realized gains/losses."""
    
    print("\n" + "=" * 50)
    print("TAX SUMMARY (Realized Gains/Losses)")
    print("=" * 50)
    print(f"Short-term Gains: ${tax_summary['short_term_gains']:.2f}")
    print(f"Short-term Losses: ${tax_summary['short_term_losses']:.2f}")
    print(f"Long-term Gains:   ${tax_summary['long_term_gains']:.2f}")
    print(f"Long-term Losses:   ${tax_summary['long_term_losses']:.2f}")
    print("=" * 50)

def print_portfolio_summary(portfolio: Dict[str, Dict[str, int]]):
    """Print a summary of current portfolio holdings"""
    print("\n" + "=" * 50)
    print("PORTFOLIO SUMMARY")
    print("=" * 50)
    
    for ticker, data in portfolio.items():
        print(f"\n{ticker}")
        print(f"Bought: {data['bought']}")
        print(f"Sold:   {data['sold']}")
        print(f"Cost Basis: ${data['cost_basis']:.2f}")
        print("──────────")
        print(f"Owned:  {data['owned']}")
    
    print("\n" + "=" * 50)

def identify_tax_loss_opportunities(portfolio: Dict[str, Dict[str, int]], transactions: List[Transaction], min_loss_percentage: float = 5.0) -> List[Dict[str, any]]:
    """
    Identify tax-loss harvesting opportunities in the current portfolio.
    
    A tax-loss opportunity exists when:
    - Position has unrealized loss (current value < cost basis)
    - The loss can offset gains to reduce tax liability
    - Long-term losses are more valuable for offsetting long-term gains
    """
    opportunities = []
    
    # Debug: Print portfolio to verify owned shares
    print("DEBUG: Portfolio Overview")
    for ticker, data in portfolio.items():
        print(f"Ticker: {ticker}, Owned: {data['owned']}, Cost Basis: ${data['cost_basis']:.2f}")
    
    for ticker, data in portfolio.items():
        if data['owned'] > 0:  # Only consider positions we still own
            print(f"DEBUG: Checking {ticker} with {data['owned']} shares owned")
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = hist.iloc[-1]['Close']
                    current_value = current_price * data['owned']
                    unrealized_gain_loss = current_value - data['cost_basis']
                    if unrealized_gain_loss < 0:
                        loss_amount = abs(unrealized_gain_loss)
                        loss_percentage = (loss_amount / data['cost_basis']) * 100
                        if loss_percentage >= min_loss_percentage:
                            # Calculate holding period to determine tax classification
                            buy_transactions = [t for t in transactions if t.ticker == ticker and t.transaction_type == 'BUY']
                            if buy_transactions:
                                earliest_buy = min(buy_transactions, key=lambda x: x.timestamp)
                                holding_days = (datetime.datetime.now() - earliest_buy.timestamp).days
                                is_long_term = holding_days > 365
                            else:
                                holding_days = 0
                                is_long_term = False
                            
                            opportunities.append({
                                'ticker': ticker,
                                'shares': data['owned'],
                                'current_price': current_price,
                                'cost_basis': data['cost_basis'],
                                'current_value': current_value,
                                'unrealized_loss': loss_amount,
                                'loss_percentage': loss_percentage,
                                'holding_days': holding_days,
                                'is_long_term': is_long_term,
                                'tax_impact': 'high' if is_long_term else 'medium'
                            })
            except Exception:
                continue
                print(f"DEBUG: Fetching data for {ticker}")
                hist = stock.history(period="1d")
                if not hist.empty:
                    print(f"DEBUG: Successfully fetched data for {ticker}")
                    current_price = hist.iloc[-1]['Close']
                    current_value = current_price * data['owned']
                    unrealized_gain_loss = current_value - data['cost_basis']
                    print(f"DEBUG: {ticker} - Current Price: ${current_price:.2f}, Current Value: ${current_value:.2f}, Cost Basis: ${data['cost_basis']:.2f}, Unrealized Gain/Loss: ${unrealized_gain_loss:.2f}")
                    
                    # Check if there's an unrealized loss above the minimum percentage threshold
                    if unrealized_gain_loss < 0 and (abs(unrealized_gain_loss / data['cost_basis']) * 100) >= min_loss_percentage:
                        loss_amount = abs(unrealized_gain_loss)
                        if (loss_amount / data['cost_basis']) * 100 >= min_loss_percentage:
                            # Calculate holding period to determine tax classification
                            # Find the average buy date for this position
                            buy_transactions = [t for t in transactions if t.ticker == ticker and t.transaction_type == 'BUY']
                            if buy_transactions:
                                earliest_buy = min(buy_transactions, key=lambda x: x.timestamp)
                                holding_days = (datetime.datetime.now() - earliest_buy.timestamp).days
                                is_long_term = holding_days > 365
                            else:
                                holding_days = 0
                                is_long_term = False

                            opportunities.append({
                                'ticker': ticker,
                                'shares': data['owned'],
                                'current_price': current_price,
                                'cost_basis': data['cost_basis'],
                                'current_value': current_value,
                                'unrealized_loss': loss_amount,
                                'loss_percentage': loss_percentage,
                                'holding_days': holding_days,
                                'is_long_term': is_long_term,
                                'tax_impact': 'high' if is_long_term else 'medium'
                            })
                        
                        # Calculate holding period to determine tax classification
                        # Find the average buy date for this position
                        buy_transactions = [t for t in transactions if t.ticker == ticker and t.transaction_type == 'BUY']
                        if buy_transactions:
                            # Use the earliest buy date as reference
                            earliest_buy = min(buy_transactions, key=lambda x: x.timestamp)
                            holding_days = (datetime.datetime.now() - earliest_buy.timestamp).days
                            is_long_term = holding_days > 365
                        else:
                            holding_days = 0
                            is_long_term = False
                        
                        opportunities.append({
                            'ticker': ticker,
                            'shares': data['owned'],
                            'current_price': current_price,
                            'cost_basis': data['cost_basis'],
                            'current_value': current_value,
                            'unrealized_loss': loss_amount,
                            'loss_percentage': (loss_amount / data['cost_basis']) * 100,
                            'holding_days': holding_days,
                            'is_long_term': is_long_term,
                            'tax_impact': 'high' if is_long_term else 'medium'  # Long-term losses are more valuable
                        })
            except Exception:
                continue
    
    # Sort by loss amount (highest losses first for maximum tax benefit)
    opportunities.sort(key=lambda x: x['unrealized_loss'], reverse=True)
    
    return opportunities

def print_tax_loss_opportunities(opportunities: List[Dict[str, any]]):
    """Print tax-loss harvesting opportunities in order of most beneficial to least beneficial."""
    
    # Sort opportunities by unrealized_loss in descending order (most beneficial first)
    sorted_opportunities = sorted(opportunities, key=lambda x: x['unrealized_loss'], reverse=True)
    
    if not sorted_opportunities:
        print("\n" + "=" * 50)
        print("TAX-LOSS OPPORTUNITIES")
        print("=" * 50)
        print("No tax-loss opportunities found in current portfolio.")
        print("=" * 50)
        return

    print("\n" + "=" * 50)
    print("TAX-LOSS OPPORTUNITIES (Most Beneficial to Least Beneficial)")
    print("=" * 50)
    print(f"Found {len(sorted_opportunities)} positions with unrealized losses:\n")

    for i, opp in enumerate(sorted_opportunities, 1):
        holding_period = f"{opp['holding_days']} days ({'Long-term' if opp['is_long_term'] else 'Short-term'})"
        print(f"{i}. {opp['ticker']}")
        print(f"   Shares: {opp['shares']}")
        print(f"   Current Price: ${opp['current_price']:.2f}")
        print(f"   Cost Basis: ${opp['cost_basis']:.2f}")
        print(f"   Current Value: ${opp['current_value']:.2f}")
        print(f"   Unrealized Loss: ${opp['unrealized_loss']:.2f} ({opp['loss_percentage']:.1f}%)")
        print(f"   Holding Period: {holding_period}")
        print(f"   Tax Impact: {opp['tax_impact'].upper()}")
        print()

    print("=" * 50)

def get_current_portfolio_value(portfolio: Dict[str, Dict[str, int]], transactions: List[Transaction]) -> tuple[float, float, Dict[str, float]]:
    """
    Get current portfolio value, unrealized gains, and tax summary using today's prices.
    
    Unrealized Gain/Loss Calculation:
    - Based ONLY on shares currently owned (not shares already sold)
    - Uses the cost basis of currently owned shares from portfolio summary
    - Current market value - cost basis of owned shares = unrealized gain/loss
    
    Tax Summary:
    - Short-term gains/losses: held ≤1 year
    - Long-term gains/losses: held >1 year
    """
    total_value = 0.0
    total_unrealized_gains = 0.0
    tax_summary = {
        'short_term_gains': 0.0,
        'short_term_losses': 0.0,
        'long_term_gains': 0.0,
        'long_term_losses': 0.0
    }
    
    # Calculate realized gains/losses for tax summary
    for transaction in transactions:
        if transaction.transaction_type == 'SELL':
            gain_loss = transaction.realized_gain_loss
            holding_period = (transaction.timestamp - datetime.datetime(2020, 1, 1)).days
            
            if holding_period <= 365:  # Short-term (≤1 year)
                if gain_loss >= 0:
                    tax_summary['short_term_gains'] += gain_loss
                else:
                    tax_summary['short_term_losses'] += abs(gain_loss)
            else:  # Long-term (>1 year)
                if gain_loss >= 0:
                    tax_summary['long_term_gains'] += gain_loss
                else:
                    tax_summary['long_term_losses'] += abs(gain_loss)
    
    for ticker, data in portfolio.items():
        if data['owned'] > 0:  # Only calculate for positions we still own
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = hist.iloc[-1]['Close']
                    position_value = current_price * data['owned']
                    total_value += position_value
                    
                    # Calculate unrealized gain/loss for currently owned shares only
                    # Use the cost basis of currently owned shares (from portfolio summary)
                    unrealized_gain = position_value - data['cost_basis']
                    total_unrealized_gains += unrealized_gain
            except Exception:
                continue
    
    return total_value, total_unrealized_gains, tax_summary

def generate_random_timestamp():
    start_date = datetime.datetime(2020, 1, 1)
    end_date = datetime.datetime.now()
    time_between_dates = end_date - start_date
    random_number_of_days = random.randrange(time_between_dates.days)
    random_date = start_date + datetime.timedelta(days=random_number_of_days)
    random_hour = random.randint(0, 23)
    random_minute = random.randint(0, 59)
    random_second = random.randint(0, 59)
    random_timestamp = random_date.replace(hour=random_hour, minute=random_minute, second=random_second)
    return random_timestamp

def select_tickers():
    nasdaq_tickers = load_tickers('NASDAQ')
    sp500_tickers = load_tickers('S&P500')
    rus1000_tickers = load_tickers('Rus1000')

    selected_tickers = []

    while len(selected_tickers) < 5:
        # Generate a random timestamp
        random_timestamp = generate_random_timestamp()
        # Check if the timestamp is valid for trading
        if is_valid_trading_time(random_timestamp):
            # 55% chance for S&P500
            if random.random() < 0.55:
                selected_tickers.append(random.choice(sp500_tickers))
            # 30% chance for NASDAQ
            elif random.random() < 0.30:
                selected_tickers.append(random.choice(nasdaq_tickers))
            # 15% chance for Rus1000
            elif random.random() < 0.15:
                selected_tickers.append(random.choice(rus1000_tickers))
            # Ensure we have exactly 5 tickers
            while len(selected_tickers) < 5:
                # Randomly select from any of the three lists
                source = random.choice(['NASDAQ', 'S&P500', 'Rus1000'])
                if source == 'NASDAQ':
                    selected_tickers.append(random.choice(nasdaq_tickers))
                elif source == 'S&P500':
                    selected_tickers.append(random.choice(sp500_tickers))
                else:
                    selected_tickers.append(random.choice(rus1000_tickers))

    return selected_tickers

# ---------------------------------------------------------------------------
# Run modes: SIM vs ACTUAL
#
#   python random_tickers.py --mode sim     # random $50k paper portfolio (default)
#   python random_tickers.py --mode actual  # YOUR real holdings -> live TLH scan
#
# SIM mode generates random BUY/SELL transactions against a fake cash budget.
# ACTUAL mode takes holdings you type in (ticker, shares, buy price, buy date),
# fetches live prices, and reports tax-loss harvesting (TLH) opportunities.
# MP.py / MP.html always use ACTUAL mode via the helpers below.
# ---------------------------------------------------------------------------
MODE_SIM = "sim"
MODE_ACTUAL = "actual"

PORTFOLIO_FORMAT_HELP = """\
One holding per line, in any of these formats:
  AAPL 10 @ 175.50 on 2023-06-01
  AAPL, 10, 175.50, 2023-06-01
  AAPL 10 175.50 2023-06-01
  AAPL 10 @ 175.50              (buy date defaults to today -> short-term)
Lines starting with # and blank lines are ignored. Date format: YYYY-MM-DD."""

EXAMPLE_HOLDINGS_TEXT = """\
AAPL 10 @ 175.50 on 2023-06-01
MSFT, 5, 300.00, 2022-06-01
TSLA 8 @ 220.00 on 2024-11-15"""


def parse_portfolio_text(text: str) -> List[Dict]:
    """Parse user-typed portfolio holdings into a list of dicts.

    Each dict has: ticker (str), shares (float), buy_price (float),
    buy_date (datetime). Raises ValueError listing every bad line.
    """
    holdings = []
    errors = []
    today = datetime.datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)

    for lineno, raw_line in enumerate((text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Normalize: commas/@/the word "on" all become spaces, then split.
        cleaned = line.replace(",", " ").replace("@", " ")
        cleaned = cleaned.replace(" on ", " ").replace(" ON ", " ")
        parts = cleaned.split()
        if len(parts) < 3:
            errors.append(f"line {lineno}: {raw_line!r} (need TICKER SHARES PRICE [DATE])")
            continue
        ticker, shares_raw, price_raw = parts[0].upper(), parts[1], parts[2]
        date_raw = parts[3] if len(parts) >= 4 else None
        try:
            shares = float(shares_raw)
            if shares <= 0:
                raise ValueError("shares must be positive")
        except ValueError:
            errors.append(f"line {lineno}: bad share count {shares_raw!r}")
            continue
        try:
            buy_price = float(price_raw)
            if buy_price <= 0:
                raise ValueError("price must be positive")
        except ValueError:
            errors.append(f"line {lineno}: bad price {price_raw!r}")
            continue
        if date_raw:
            try:
                buy_date = datetime.datetime.strptime(date_raw, "%Y-%m-%d")
                buy_date = buy_date.replace(hour=9, minute=30, second=0, microsecond=0)
            except ValueError:
                errors.append(f"line {lineno}: bad date {date_raw!r} (use YYYY-MM-DD)")
                continue
        else:
            buy_date = today
        if buy_date > datetime.datetime.now():
            errors.append(f"line {lineno}: buy date {date_raw!r} is in the future")
            continue
        holdings.append({
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date,
        })

    if errors:
        raise ValueError("Could not parse portfolio:\n" + "\n".join(errors))
    return holdings


def holdings_from_ticker_dates(entries: List, buy_price_fn=None) -> List[Dict]:
    """Simplest actual-mode input: one (ticker, buy-date) pair per row.

    Cost basis per share is looked up automatically as the close on (or just
    before) the buy date, so users only type a ticker, share count, and date.
    `entries` items are (ticker, date) or (ticker, date, shares) where date
    is YYYY-MM-DD, a datetime.date, or a datetime. Shares default to 1.0.
    Raises ValueError for unknown tickers/dates.

    With the default lookup, all buy dates are priced with ONE batched
    download (plus disk cache) instead of one request per holding.
    """
    lookup = buy_price_fn or get_price_at
    errors = []
    prefetch = {}
    if buy_price_fn is None:
        def _needs_lookup(e):
            if len(e) < 4:
                return True
            try:
                return not float(e[3]) > 0
            except (ValueError, TypeError):
                return True
        try:
            prefetch = batch_buy_prices([e for e in entries if _needs_lookup(e)])
        except Exception:
            prefetch = {}

    def _probe_fallback(task):
        """Single-holding weekend/holiday walk-back for the thread pool."""
        ticker, probe = task
        for _ in range(7):  # walk back over weekends/holidays to last close
            try:
                buy_price = lookup(ticker, probe)
            except Exception:
                buy_price = None
            if buy_price:
                return float(buy_price), probe.date().isoformat()
            probe -= datetime.timedelta(days=1)
        return None, None

    out = []  # holding dicts in entry order; fallback slots resolved together
    fallbacks = []  # (slot_index, ticker, shares, buy_date, probe_task)

    def _flush_fallbacks():
        found_all = _map_concurrent(_probe_fallback, [fb[4] for fb in fallbacks])
        for (slot, ticker, shares, buy_date, _), found in zip(fallbacks, found_all):
            price, price_date = found or (None, None)
            if not price:
                errors.append(f"{ticker}: no price history near {buy_date.date().isoformat()}")
                out[slot] = None
                continue
            out[slot] = {"ticker": ticker, "shares": shares,
                         "buy_price": float(price), "buy_date": buy_date,
                         "price_date": price_date}
        fallbacks.clear()

    for item in entries:
        ticker_raw, date_raw = item[0], item[1]
        shares_raw = item[2] if len(item) > 2 else 1.0
        ticker = (ticker_raw or "").strip().upper()
        if not ticker:
            continue
        try:
            shares = float(shares_raw)
            if shares <= 0:
                raise ValueError("shares must be positive")
        except (ValueError, TypeError):
            errors.append(f"{ticker}: bad share count {shares_raw!r}")
            continue
        try:
            if isinstance(date_raw, datetime.datetime):
                buy_date = date_raw
            elif isinstance(date_raw, datetime.date):
                buy_date = datetime.datetime(date_raw.year, date_raw.month, date_raw.day, 9, 30)
            else:
                buy_date = datetime.datetime.strptime(str(date_raw).strip(), "%Y-%m-%d").replace(hour=9, minute=30)
        except (ValueError, TypeError, AttributeError):
            errors.append(f"{ticker or '?'}: bad date {date_raw!r} (use YYYY-MM-DD)")
            continue
        if buy_date > datetime.datetime.now():
            errors.append(f"{ticker}: buy date is in the future")
            continue
        try:
            override = float(item[3]) if len(item) > 3 and item[3] not in (None, "") else None
        except (ValueError, TypeError):
            errors.append(f"{ticker}: bad buy price {item[3]!r}")
            continue
        if override is not None:
            if override <= 0:
                errors.append(f"{ticker}: bad buy price {item[3]!r}")
                continue
            out.append({"ticker": ticker, "shares": shares,
                        "buy_price": override, "buy_date": buy_date,
                        "price_date": buy_date.date().isoformat()})
            continue
        buy_price, price_date = None, None
        batched = prefetch.get((ticker, buy_date.date().isoformat()))
        if batched:
            buy_price, price_date = batched, buy_date.date().isoformat()
            out.append({"ticker": ticker, "shares": shares,
                        "buy_price": float(buy_price), "buy_date": buy_date,
                        "price_date": price_date})
            continue
        fallbacks.append((len(out), ticker, shares, buy_date, (ticker, buy_date)))
        out.append(None)  # resolved concurrently below, order preserved
    _flush_fallbacks()
    holdings = [h for h in out if h is not None]
    if errors:
        raise ValueError("Could not price these holdings:\n" + "\n".join(errors))
    return holdings


def _parse_csv_date(value) -> str:
    """Parse a messy date cell into YYYY-MM-DD. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and 20000 <= value <= 80000:  # Excel serial
        base = datetime.date(1899, 12, 30) + datetime.timedelta(days=value)
        return base.isoformat()
    try:
        return pd.to_datetime(str(value).strip(), errors="raise").date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _is_ticker_like(value) -> bool:
    import re
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9.\-]{0,7}$", (value or "").strip()))


def parse_csv_holdings(content: str, fallback_date: str = None) -> Dict:
    """Parse an uploaded CSV into analyzer entries.

    Handles headers or no headers; comma/semicolon/tab delimiters; common
    column names (Symbol/Qty/Bought/Price/Total cost...); Excel serial dates.
    Missing shares default to 1, missing buy price means auto-lookup, missing
    dates use `fallback_date` (default: 2023-01-02) and are flagged.

    Returns {"entries": [(ticker, date_iso, shares, buy_price|None)],
             "mapping": {canonical: original header}, "warnings": [str]}.
    """
    import csv as csv_mod

    lines = [ln for ln in (content or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    entries, warnings, mapping = [], [], {}
    if not lines:
        return {"entries": entries, "mapping": mapping, "warnings": ["File is empty."]}

    try:
        dialect = csv_mod.Sniffer().sniff("\n".join(lines[:5]), delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ","
    rows = list(csv_mod.reader(lines, delimiter=delim))
    if not rows:
        return {"entries": entries, "mapping": mapping, "warnings": ["No rows found."]}

    synonyms = {
        "ticker": {"ticker", "symbol", "stock", "holding", "security", "code"},
        "shares": {"shares", "share", "qty", "quantity", "units", "amount", "number of shares", "no. of shares",
                   "shares held", "position", "balance"},
        "date": {"date", "buy date", "bought", "bought on", "purchase date", "buy_date", "transaction date",
                 "trade date", "trade_date", "buydate", "date acquired", "acquired", "open date"},
        "price": {"price", "buy price", "bought at", "cost", "cost per share", "paid", "avg cost", "average cost",
                  "book cost", "price paid", "purchase price", "purchase_price", "buy_price", "avg price",
                  "average price", "fill price"},
        "total": {"total", "total cost", "total paid", "amount paid", "cost basis", "total basis"},
        "type": {"transaction type", "type", "action", "side", "transaction", "buy/sell"},
        "current": {"current price", "current", "market price", "last price", "last", "close"},
    }
    first = [c.strip().lower() for c in rows[0]]
    col_index = {}
    for i, cell in enumerate(first):
        for canon, names in synonyms.items():
            if cell in names and canon not in col_index:
                col_index[canon] = i
    has_header = "ticker" in col_index or any(
        c and not _is_ticker_like(c) and not _parse_csv_date(c) for c in first
        if not c.replace(".", "", 1).replace("-", "", 1).strip().replace(" ", "").isdigit())
    if not has_header and len(rows) > 1 and all(
            _is_ticker_like(c) and not c.strip().replace(".", "", 1).isdigit() for c in first):
        has_header = True  # unrecognized header words (e.g. Name,Count,When)
        warnings.append("row 1: looks like a header with unknown column names, skipped")
    data = rows[1:] if has_header else rows
    if has_header:
        # Prefer a purchase-specific date (Trade Date) over a generic quote Date.
        if col_index.get("date") is not None and first[col_index["date"]] == "date":
            specific = {"trade date", "trade_date", "purchase date", "buy date", "bought",
                        "bought on", "date acquired", "acquired", "open date", "buy_date"}
            for i, cell in enumerate(first):
                if cell in specific:
                    col_index["date"] = i
                    break
        mapping = {canon: rows[0][i] for canon, i in col_index.items()}

    fallback = _parse_csv_date(fallback_date) or "2023-01-02"

    def num(cell):
        try:
            return float(str(cell).strip().replace(",", "").replace("$", ""))
        except (ValueError, TypeError, AttributeError):
            return None

    records = []  # (ticker, date_iso|None, shares, buy_price|None, is_sell, live|None)
    for lineno, row in enumerate(data, 2 if has_header else 1):
        try:
            is_sell, live = False, None
            if has_header:
                get = lambda canon: row[col_index[canon]].strip() if canon in col_index and col_index[canon] < len(row) else ""
                ticker, shares_raw, date_raw = get("ticker").upper(), get("shares"), get("date")
                price_raw, total_raw = get("price"), get("total")
                type_raw, current_raw = get("type"), get("current")
                shares = num(shares_raw) if shares_raw else 1.0
                buy_price = num(price_raw) if price_raw else None
                if buy_price is None and total_raw and shares:
                    total = num(total_raw)
                    buy_price = total / shares if total and shares > 0 else None
                date_iso = _parse_csv_date(date_raw) if date_raw else None
                is_sell = "sell" in type_raw.lower()
                live = num(current_raw) if current_raw else None
                if live is not None and live <= 0:
                    live = None
            else:
                cells = [c.strip() for c in row if c.strip() != ""]
                if not cells or not _is_ticker_like(cells[0]):
                    warnings.append(f"row {lineno}: skipped (no ticker found)")
                    continue
                ticker = cells[0].upper()
                rest = cells[1:]
                date_iso, nums = None, []
                for cell in rest:
                    parsed = _parse_csv_date(cell)
                    if parsed and not cell.replace(".", "", 1).isdigit():
                        date_iso = date_iso or parsed
                    else:
                        n = num(cell)
                        if n is not None:
                            nums.append(n)
                shares, buy_price = 1.0, None
                if len(nums) == 1:
                    shares = nums[0]
                elif len(nums) >= 2:
                    shares, buy_price = nums[0], nums[1]
            if not _is_ticker_like(ticker):
                warnings.append(f"row {lineno}: skipped (bad ticker {ticker!r})")
                continue
            if not shares or shares <= 0:
                warnings.append(f"row {lineno}: skipped (bad share count for {ticker})")
                continue
            records.append((ticker, date_iso, shares, buy_price, is_sell, live))
        except Exception as exc:
            warnings.append(f"row {lineno}: skipped ({exc})")

    # Net BUY/SELL rows per ticker so exports with sales stay accurate.
    lots: Dict[str, Dict] = {}
    live_prices: Dict[str, float] = {}
    for i, (ticker, date_iso, shares, buy_price, is_sell, live) in enumerate(records):
        if live is not None:
            live_prices[ticker] = live
        lot = lots.get(ticker)
        if is_sell:
            if lot is None:
                warnings.append(f"row ?: {ticker} SELL without a prior BUY, ignored")
                continue
            lot["shares"] -= shares
            if lot["shares"] <= 0:
                lots.pop(ticker, None)
                warnings.append(f"{ticker}: fully sold, excluded")
            continue
        if lot is None:
            lots[ticker] = {"date": date_iso, "shares": shares,
                            "price": buy_price, "missing_date": date_iso is None}
        else:
            lot["shares"] += shares
    for ticker, lot in lots.items():
        date_iso = lot["date"] or fallback
        if lot["missing_date"]:
            warnings.append(f"{ticker} has no date, using {fallback}")
        entries.append((ticker, date_iso, lot["shares"], lot["price"]))
    return {"entries": entries, "mapping": mapping, "warnings": warnings,
            "live": live_prices}


# ---------------------------------------------------------------------------
# Tax rules (simplified federal estimates, 2026 brackets per IRS Rev. Proc.
# 2025-32: same 10%-37% rates, inflation-adjusted thresholds)
#
# Short-term gains/losses are taxed like ordinary income (10%-37% brackets).
# Long-term gains get 0%/15%/20% by income. Net capital losses can offset
# gains dollar-for-dollar; up to $3,000 of leftover loss per year offsets
# ordinary income, the rest carries forward. Wash sales disallow losses.
# These helpers estimate savings so users see the dollar value of a harvest.
# Educational only, not tax advice.
# ---------------------------------------------------------------------------
FEDERAL_ORDINARY_2026 = {
    "single": [(12400, 0.10), (50400, 0.12), (105700, 0.22), (201775, 0.24),
               (256225, 0.32), (640600, 0.35), (float("inf"), 0.37)],
    "joint": [(24800, 0.10), (100800, 0.12), (211400, 0.22), (403550, 0.24),
              (512450, 0.32), (768700, 0.35), (float("inf"), 0.37)],
}
FEDERAL_LT_THRESHOLDS_2026 = {
    "single": (49450, 545500),   # 0% below, 15% between, 20% above
    "joint": (98900, 613700),
}
ANNUAL_LOSS_DEDUCTION_CAP = 3000.0


def marginal_rates(taxable_income: float, status: str = "single"):
    """Return (short-term rate, long-term rate) for 2026 federal brackets."""
    status = status if status in FEDERAL_ORDINARY_2026 else "single"
    st_rate = next(rate for cap, rate in FEDERAL_ORDINARY_2026[status]
                   if taxable_income <= cap)
    low, high = FEDERAL_LT_THRESHOLDS_2026[status]
    lt_rate = 0.0 if taxable_income <= low else (0.15 if taxable_income <= high else 0.20)
    return st_rate, lt_rate


def position_gains(positions: List[Dict]) -> List[Dict]:
    """Winners from an analyze_actual_portfolio positions list (best first)."""
    gains = [{
        "ticker": p["ticker"],
        "shares": p["shares"],
        "gain": p["unrealized_gain_loss"],
        "gain_percentage": p["gain_loss_percentage"],
        "holding_days": p["holding_days"],
        "is_long_term": p["is_long_term"],
    } for p in positions if p["unrealized_gain_loss"] > 0]
    gains.sort(key=lambda g: g["gain"], reverse=True)
    return gains


def estimate_tax_savings(opportunities: List[Dict], gains: List[Dict],
                         taxable_income: float = 150000.0,
                         status: str = "single") -> Dict:
    """Estimate federal tax savings from harvesting the given losses.

    Netting follows IRS order: short-term losses offset short-term gains,
    long-term offset long-term, leftovers cross over. Loss beyond total gains
    deducts up to $3,000 of ordinary income (rest carries forward, reported
    but not valued here).
    """
    st_rate, lt_rate = marginal_rates(taxable_income, status)
    st_loss = sum(o["unrealized_loss"] for o in opportunities if not o["is_long_term"])
    lt_loss = sum(o["unrealized_loss"] for o in opportunities if o["is_long_term"])
    st_gain = sum(g["gain"] for g in gains if not g["is_long_term"])
    lt_gain = sum(g["gain"] for g in gains if g["is_long_term"])

    savings, detail = 0.0, {}
    for label, loss, gain, r in (("short-term", st_loss, st_gain, st_rate),
                                 ("long-term", lt_loss, lt_gain, lt_rate)):
        sheltered = min(loss, gain)
        savings += sheltered * r
        detail[label] = {"loss": loss, "gain": gain, "sheltered": sheltered,
                         "rate": r, "savings": sheltered * r,
                         "cross_sheltered": 0.0, "cross_savings": 0.0}
    # Cross leftovers over: spare short-term loss shelters long-term gains and vice versa.
    rem_st_loss = st_loss - detail["short-term"]["sheltered"]
    rem_lt_loss = lt_loss - detail["long-term"]["sheltered"]
    rem_st_gain = st_gain - detail["short-term"]["sheltered"]
    rem_lt_gain = lt_gain - detail["long-term"]["sheltered"]
    cross_lt = min(rem_st_loss, rem_lt_gain)
    cross_st = min(rem_lt_loss, rem_st_gain)
    savings += cross_lt * lt_rate + cross_st * st_rate
    detail["long-term"]["cross_sheltered"] = cross_lt
    detail["long-term"]["cross_savings"] = cross_lt * lt_rate
    detail["short-term"]["cross_sheltered"] = cross_st
    detail["short-term"]["cross_savings"] = cross_st * st_rate
    leftover = rem_st_loss + rem_lt_loss - cross_lt - cross_st
    deducted = min(max(leftover, 0.0), ANNUAL_LOSS_DEDUCTION_CAP)
    savings += deducted * st_rate
    carryforward = max(leftover - deducted, 0.0)
    return {"short_term_rate": st_rate, "long_term_rate": lt_rate,
            "detail": detail, "deducted_vs_income": deducted,
            "carryforward": carryforward, "total_savings": savings}


def suggest_harvest_pairs(opportunities: List[Dict], gains: List[Dict],
                          st_rate: float = 0.24, lt_rate: float = 0.15,
                          max_pairs: int = 3) -> List[Dict]:
    """Suggest the best loss+gain sell pairs: each pair realizes one loss and
    one gain so the loss wipes out (some of) the gain's tax bill.

    Pairs the biggest unused loss with the biggest unused gain it fully
    covers when possible (a fully tax-free rebalance), else the biggest gain.
    Savings = tax the gain would have cost on its own, minus tax on the net.
    """
    losses = sorted(opportunities, key=lambda o: o["unrealized_loss"], reverse=True)
    remaining_gains = sorted(gains, key=lambda g: g["gain"], reverse=True)
    pairs = []
    for loss in losses:
        if not remaining_gains or len(pairs) >= max_pairs:
            break
        gain = next((g for g in remaining_gains if g["gain"] <= loss["unrealized_loss"]),
                    remaining_gains[0])
        remaining_gains.remove(gain)
        rate = lt_rate if gain["is_long_term"] else st_rate
        tax_without = gain["gain"] * rate
        net_taxable = max(gain["gain"] - loss["unrealized_loss"], 0.0)
        pairs.append({
            "loss_ticker": loss["ticker"],
            "loss_amount": loss["unrealized_loss"],
            "loss_long_term": loss["is_long_term"],
            "loss_shares": loss.get("shares", 0),
            "loss_price": loss.get("current_price", 0),
            "loss_proceeds": loss.get("shares", 0) * loss.get("current_price", 0),
            "gain_ticker": gain["ticker"],
            "gain_amount": gain["gain"],
            "gain_long_term": gain["is_long_term"],
            "net_taxable": net_taxable,
            "fully_covered": net_taxable == 0.0,
            "savings_vs_gain_alone": tax_without - net_taxable * rate,
        })
    return pairs


# ---------------------------------------------------------------------------
# Tax Triples: sell a loser (TLH) + sell a winner (gain) + buy an alternative
# with the proceeds. The alternative keeps similar market exposure without
# repurchasing a "substantially identical" security (wash-sale rule).
# ---------------------------------------------------------------------------
SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Financial": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}
STOCK_ALTERNATIVES = {
    "AAPL": ("MSFT", "fellow mega-cap tech stock"),
    "MSFT": ("AAPL", "fellow mega-cap tech stock"),
    "NVDA": ("AMD", "fellow chipmaker"),
    "AMD": ("NVDA", "fellow chipmaker"),
    "GOOGL": ("META", "fellow mega-cap tech stock"),
    "GOOG": ("META", "fellow mega-cap tech stock"),
    "META": ("GOOGL", "fellow mega-cap tech stock"),
    "AMZN": ("MSFT", "fellow mega-cap tech stock"),
    "TSLA": ("F", "fellow automaker"),
    "F": ("GM", "fellow automaker"),
    "GM": ("F", "fellow automaker"),
    "JPM": ("BAC", "fellow money-center bank"),
    "BAC": ("JPM", "fellow money-center bank"),
    "XOM": ("CVX", "fellow oil major"),
    "CVX": ("XOM", "fellow oil major"),
    "JNJ": ("UNH", "fellow large-cap healthcare stock"),
    "UNH": ("JNJ", "fellow large-cap healthcare stock"),
}


def get_sector(ticker: str):
    """Look up a ticker's sector via yfinance. Returns None when unknown.

    Cached on disk forever: a company's sector basically never changes, so
    every repeat visit and redeploy costs zero API calls.
    """
    key = f"SEC|{(ticker or '').strip().upper()}"
    try:
        hit = _disk_cache_read().get(key)
        if hit:
            return hit
    except Exception:
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = yf.Ticker(ticker).info
        sector = (info or {}).get("sector") or None
    except Exception:
        sector = None
    if sector:
        try:
            _disk_cache_write({key: sector})
        except Exception:
            pass
    return sector


def get_sectors(tickers: List[str]) -> Dict[str, str]:
    """Sector lookups for many tickers at once (concurrent, disk-cached)."""
    uniq = []
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in uniq:
            uniq.append(u)
    return {t: s for t, s in zip(uniq, _map_concurrent(get_sector, uniq)) if s}


def suggest_alternative(ticker: str, sector: str = None):
    """Return (alternative_ticker, reason) for the proceeds of a TLH sale.

    Prefers a curated same-sector stock, then a sector ETF, then SPY.
    Pass `sector` to skip the network lookup (tests, dashboards).
    """
    key = (ticker or "").strip().upper()
    if key in STOCK_ALTERNATIVES:
        return STOCK_ALTERNATIVES[key]
    sector = sector or get_sector(key)
    etf = SECTOR_ETF.get(sector or "")
    if etf:
        return etf, f"tracks the {sector} sector -- similar exposure, different security"
    return "SPY", "broad-market fallback when no closer match is known"


def update_alt_cache(cache: Dict, loss_tickers: List[str],
                     fetch_fn=None, suggest_fn=None) -> Dict:
    """Maintain the MP tax-tab cache: {loss_ticker: [alt, reason, price|None]}.

    Only new tickers trigger an alternative lookup and only alternatives
    missing prices trigger a price fetch, so slider drags stay instant.
    """
    fetch_fn = fetch_fn or fetch_live_prices
    suggest_fn = suggest_fn or (lambda t: suggest_alternative(t))
    cache = dict(cache or {})
    new = [t for t in loss_tickers if t not in cache]
    if new:
        for ticker, found in zip(new, _map_concurrent(suggest_fn, new)):
            if found:
                cache[ticker] = [found[0], found[1], None]
    missing = sorted({a for a, _, p in cache.values() if not p})
    if missing:
        try:
            fetched = fetch_fn(missing) or {}
        except Exception:
            fetched = {}
        for ticker in cache:
            if cache[ticker][0] in fetched:
                cache[ticker][2] = fetched[cache[ticker][0]]
    return cache


def suggest_tax_triples(opportunities: List[Dict], gains: List[Dict],
                        st_rate: float = 0.24, lt_rate: float = 0.15,
                        max_n: int = 3, alt_prices: Dict[str, float] = None,
                        sector_fn=None, alt_overrides: Dict[str, tuple] = None) -> List[Dict]:
    """Build Tax Triples: best loss+gain pair plus a wash-sale-safe buy.

    Each triple says what to sell at a loss, what to sell at a gain, and what
    to buy with the loss-sale proceeds (with affordable share count when the
    alternative's live price is known via `alt_prices`). `alt_overrides`
    maps a loss ticker straight to its (alternative, reason), skipping lookup.
    """
    alt_prices = alt_prices or {}
    alt_overrides = alt_overrides or {}
    triples = []
    for pair in suggest_harvest_pairs(opportunities, gains, st_rate, lt_rate, max_n):
        if pair["loss_ticker"] in alt_overrides:
            buy_ticker, reason = alt_overrides[pair["loss_ticker"]]
        else:
            sector = sector_fn(pair["loss_ticker"]) if sector_fn else None
            buy_ticker, reason = suggest_alternative(pair["loss_ticker"], sector)
        buy_price = alt_prices.get(buy_ticker)
        proceeds = pair["loss_proceeds"]
        triple = dict(pair)
        triple.update({
            "buy_ticker": buy_ticker,
            "buy_reason": reason,
            "buy_proceeds": proceeds,
            "buy_price": buy_price,
            "buy_shares": (proceeds / buy_price) if buy_price else None,
        })
        triples.append(triple)
    return triples


def build_transactions_from_holdings(holdings: List[Dict]) -> List[Transaction]:
    """Convert parsed holdings into chronologically sorted BUY Transactions."""
    transactions = [
        Transaction(
            ticker=h["ticker"],
            transaction_type="BUY",
            timestamp=h["buy_date"],
            price=h["buy_price"],
            shares=h["shares"],
        )
        for h in holdings
    ]
    transactions.sort(key=lambda t: t.timestamp)
    return transactions


def fetch_tape_quotes(tickers: List[str]) -> Dict[str, tuple]:
    """Batch-fetch {ticker: (last_price, day_change_pct)} for a ticker tape.

    One network round-trip for the whole list. Tickers with no data are
    absent from the result; callers fall back to bare symbols.
    """
    quotes: Dict[str, tuple] = {}
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return quotes
    import time as _time
    try:
        cached = _disk_cache_read().get("TAPE")
        if (isinstance(cached, dict) and cached.get("quotes")
                and _time.time() - cached.get("saved", 0) < 1800):
            return {t: (float(p), float(c)) for t, (p, c) in cached["quotes"].items()}
    except Exception:
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = yf.download(" ".join(tickers), period="5d", progress=False,
                               auto_adjust=False, threads=True)
        if data is None or data.empty:
            return quotes
        closes = data["Close"]
        for ticker in tickers:
            try:
                col = closes[ticker].dropna() if len(tickers) > 1 else closes.dropna()
                if len(col) < 1:
                    continue
                last = float(col.iloc[-1])
                prev = float(col.iloc[-2]) if len(col) >= 2 else last
                if last > 0:
                    quotes[ticker] = (last, (last - prev) / prev * 100 if prev else 0.0)
            except (KeyError, TypeError, ValueError, IndexError):
                continue
    except Exception:
        pass
    if quotes:
        try:
            _disk_cache_write({"TAPE": {"saved": _time.time(),
                                        "quotes": {t: [p, c] for t, (p, c) in quotes.items()}}})
        except Exception:
            pass
    return quotes


def fetch_live_prices(tickers: List[str]) -> Dict[str, float]:
    """Fetch one live price per ticker with a single batched download.

    Duplicate symbols are fetched once; prices younger than _LIVE_TTL are
    served from memory (rapid re-renders, triage, and refresh clicks cost
    zero API calls). Falls back to per-ticker history — concurrently — when
    the batch has gaps. Tickers with no data are simply absent from the
    returned dict.
    """
    prices: Dict[str, float] = {}
    ordered = []
    for t in tickers:
        u = t.upper()
        if u not in ordered:
            ordered.append(u)
    if not ordered:
        return prices
    now = _time.time()
    fresh = {}
    for ticker in ordered:
        hit = _LIVE_CACHE.get(ticker)
        if hit is not None and now - hit[0] < _LIVE_TTL:
            prices[ticker] = hit[1]
        else:
            fresh[ticker] = True
    tickers = list(fresh)
    if tickers:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                batch = yf.download(" ".join(tickers), period="1d", progress=False,
                                    auto_adjust=False, threads=True)
                if batch is not None and not batch.empty:
                    closes = batch["Close"]
                    row = closes.iloc[-1]
                    for ticker in tickers:
                        try:
                            value = row[ticker] if len(tickers) > 1 else row
                            price = float(value)
                            if price > 0:
                                prices[ticker] = price
                        except (KeyError, TypeError, ValueError):
                            continue
            except Exception:
                pass
            missing = [t for t in tickers if t not in prices]

            def _one(ticker):
                try:
                    hist = yf.Ticker(ticker).history(period="1d")
                    if hist is not None and not hist.empty:
                        price = float(hist.iloc[-1]["Close"])
                        if price > 0:
                            return ticker, price
                except Exception:
                    pass
                return ticker, None

            for ticker, price in _map_concurrent(_one, missing):
                if price:
                    prices[ticker] = price
    for ticker, price in prices.items():
        _LIVE_CACHE[ticker] = (now, price)
    return prices


def price_holdings(entries: List, live_overrides: Dict[str, float] = None):
    """One-call bundle for a Flask POST: holdings + live prices, concurrently.

    Buy-date lookup and the live-price batch are independent, so they run on
    two threads instead of back-to-back; tickers covered by `live_overrides`
    (e.g. broker CSV prices) are never fetched. Shared disk/memory caches
    keep repeat posts near zero API calls. Returns (holdings, live).
    Raises ValueError exactly like holdings_from_ticker_dates.
    """
    live_overrides = {(k or "").strip().upper(): v
                      for k, v in (live_overrides or {}).items()}
    tickers = sorted({(e[0] or "").strip().upper() for e in entries if e and e[0]})
    wanted = [t for t in tickers if t not in live_overrides]
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=2) as ex:
        fut_holdings = ex.submit(holdings_from_ticker_dates, entries)
        fut_live = ex.submit(fetch_live_prices, wanted)
        holdings = fut_holdings.result()
        live = dict(live_overrides)
        live.update(fut_live.result())
    return holdings, live


def analyze_actual_portfolio(holdings: List[Dict], min_loss_percentage: float = 5.0,
                             price_overrides: Dict[str, float] = None) -> Dict:
    """ACTUAL-mode analysis: live TLH scan of the user's real holdings.

    `price_overrides` (ticker -> price) skips the network and makes the
    analysis deterministic for tests and dashboards. Returns positions,
    TLH opportunities (highest harvestable loss first), and totals.
    """
    transactions = build_transactions_from_holdings(holdings)
    portfolio, _, _ = calculate_portfolio(transactions)
    owned = {t: d for t, d in portfolio.items() if d["owned"] > 0}

    live = dict(price_overrides or {})
    missing = [t for t in owned if t not in live]
    if missing:
        for ticker, price in fetch_live_prices(missing).items():
            live[ticker] = price

    now = datetime.datetime.now()
    positions, opportunities, skipped = [], [], []
    for ticker, data in owned.items():
        if ticker not in live:
            skipped.append(ticker)
            continue
        current_price = live[ticker]
        current_value = current_price * data["owned"]
        unrealized = current_value - data["cost_basis"]
        loss_pct = (abs(unrealized) / data["cost_basis"] * 100) if data["cost_basis"] else 0.0
        buys = [t for t in transactions if t.ticker == ticker]
        earliest = min(buys, key=lambda t: t.timestamp).timestamp if buys else now
        holding_days = max(0, (now - earliest).days)
        is_long_term = holding_days > 365
        position = {
            "ticker": ticker,
            "shares": data["owned"],
            "buy_price": data["cost_basis"] / data["owned"] if data["owned"] else 0.0,
            "cost_basis": data["cost_basis"],
            "current_price": current_price,
            "current_value": current_value,
            "unrealized_gain_loss": unrealized,
            "gain_loss_percentage": (unrealized / data["cost_basis"] * 100) if data["cost_basis"] else 0.0,
            "holding_days": holding_days,
            "is_long_term": is_long_term,
        }
        positions.append(position)
        if unrealized < 0 and loss_pct >= min_loss_percentage:
            opportunities.append({
                "ticker": ticker,
                "shares": data["owned"],
                "current_price": current_price,
                "cost_basis": data["cost_basis"],
                "current_value": current_value,
                "unrealized_loss": abs(unrealized),
                "loss_percentage": loss_pct,
                "holding_days": holding_days,
                "is_long_term": is_long_term,
                "tax_impact": "high" if is_long_term else "medium",
            })
    opportunities.sort(key=lambda o: o["unrealized_loss"], reverse=True)
    positions.sort(key=lambda p: p["unrealized_gain_loss"])
    harvestable = sum(o["unrealized_loss"] for o in opportunities)
    return {
        "mode": MODE_ACTUAL,
        "min_loss_percentage": min_loss_percentage,
        "positions": positions,
        "opportunities": opportunities,
        "skipped": skipped,
        "totals": {
            "cost_basis": sum(p["cost_basis"] for p in positions),
            "current_value": sum(p["current_value"] for p in positions),
            "unrealized_gain_loss": sum(p["unrealized_gain_loss"] for p in positions),
            "harvestable_loss": harvestable,
            "opportunity_count": len(opportunities),
        },
    }


def run_sim_mode(num_transactions: int = 10, budget: float = 50000.0,
                 min_loss_percentage: float = 5.0) -> Dict:
    """SIM mode: random paper portfolio against a fake cash budget."""
    print(f"Generating {num_transactions} transactions with ${budget:,.2f} budget...")
    transactions = generate_transactions(num_transactions=num_transactions, budget=budget)
    if not transactions:
        print("No transactions could be generated. Please try again.")
        return {"mode": MODE_SIM, "transactions": []}

    print("\n" + "=" * 50)
    print("TRANSACTIONS (Chronologically Sorted)")
    print("=" * 50)
    for i, transaction in enumerate(transactions, 1):
        print(f"{i}. {transaction}")
        print(f"   Total: ${transaction.total_value:.2f}")
        if transaction.transaction_type == "SELL":
            print(f"   Cost Basis: ${transaction.cost_basis:.2f}")
            print(f"   Realized Gain/Loss: ${transaction.realized_gain_loss:.2f}")

    print("Validating accounting...")
    if not validate_accounting(transactions, initial_cash=budget):
        print("Accounting validation failed. Please check the transaction logic.")
        return {"mode": MODE_SIM, "transactions": transactions}

    portfolio, cash_balance, total_realized_gains = calculate_portfolio(transactions)
    print_portfolio_summary(portfolio)
    print(f"\nCash Balance: ${cash_balance:.2f}")

    print("Calculating current portfolio value...")
    current_value, total_unrealized_gains, tax_summary = get_current_portfolio_value(portfolio, transactions)
    print_tax_summary(tax_summary)
    print_tax_loss_opportunities(
        identify_tax_loss_opportunities(portfolio, transactions, min_loss_percentage))

    total_account_value = cash_balance + current_value
    print(f"\nCurrent Holdings Value: ${current_value:.2f}")
    print(f"Total Account Value (Cash + Holdings): ${total_account_value:.2f}")
    print(f"Realized Gains/Loss: ${total_realized_gains:.2f}")
    print(f"Unrealized Gains/Loss: ${total_unrealized_gains:.2f}")
    print(f"Total Gains/Loss: ${total_realized_gains + total_unrealized_gains:.2f}")

    # Accounting reconciliation check
    calculated_total_gain = total_account_value - budget
    reported_total_gain = total_realized_gains + total_unrealized_gains
    print("\nAccounting Reconciliation:")
    print(f"Initial Cash: ${budget:.2f}")
    print(f"Final Account Value: ${total_account_value:.2f}")
    print(f"Calculated Total Gain: ${calculated_total_gain:.2f}")
    print(f"Reported Total Gain: ${reported_total_gain:.2f}")
    if abs(calculated_total_gain - reported_total_gain) < 0.01:
        print("Accounting reconciliation: BALANCED")
    else:
        print("Accounting reconciliation: MISMATCH")
    print("=" * 50)
    return {"mode": MODE_SIM, "transactions": transactions,
            "total_account_value": total_account_value}


def run_actual_mode(holdings_text: str = None, holdings_file: str = None,
                    min_loss_percentage: float = 5.0,
                    price_overrides: Dict[str, float] = None) -> Dict:
    """ACTUAL mode: scan the user's real holdings for TLH opportunities."""
    if holdings_text is None and holdings_file:
        with open(holdings_file, "r") as fh:
            holdings_text = fh.read()
    if holdings_text is None:
        print("ACTUAL mode: enter your real holdings so TLH can be found.")
        print(PORTFOLIO_FORMAT_HELP)
        print("Enter one holding per line, blank line when done:\n")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line)
        holdings_text = "\n".join(lines)

    holdings = parse_portfolio_text(holdings_text)
    if not holdings:
        print("No holdings entered. Nothing to analyze.")
        return {"mode": MODE_ACTUAL, "positions": [], "opportunities": []}
    print(f"Analyzing {len(holdings)} holding(s) with live prices...")
    result = analyze_actual_portfolio(holdings, min_loss_percentage, price_overrides)

    totals = result["totals"]
    print(f"\nCost Basis: ${totals['cost_basis']:,.2f}")
    print(f"Current Value: ${totals['current_value']:,.2f}")
    print(f"Unrealized P/L: ${totals['unrealized_gain_loss']:,.2f}")
    if result["skipped"]:
        print(f"Skipped (no price data): {', '.join(result['skipped'])}")
    print_tax_loss_opportunities(result["opportunities"])
    return result


def main(argv=None) -> Dict:
    """CLI entry point: pick SIM or ACTUAL mode, then run it."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Portfolio TLH scanner: sim mode (random paper portfolio) "
                    "or actual mode (your real holdings).")
    parser.add_argument("--mode", choices=[MODE_SIM, MODE_ACTUAL], default=None,
                        help="sim = random paper portfolio; actual = your real holdings")
    parser.add_argument("--num", type=int, default=10, help="sim mode transaction count")
    parser.add_argument("--budget", type=float, default=50000.0, help="sim mode cash budget")
    parser.add_argument("--min-loss", type=float, default=5.0,
                        help="minimum %% loss to flag as a TLH opportunity")
    parser.add_argument("--holdings", default=None,
                        help="actual mode holdings text (quote it); one holding per line")
    parser.add_argument("--holdings-file", default=None,
                        help="actual mode file with one holding per line")
    args = parser.parse_args(argv)

    mode = args.mode
    if mode is None:
        if sys.stdin.isatty():
            choice = input("Run in (s)im mode with random data or (a)ctual mode with your holdings? [s/a]: ").strip().lower()
            mode = MODE_ACTUAL if choice.startswith("a") else MODE_SIM
        else:
            mode = MODE_SIM

    if mode == MODE_ACTUAL:
        if args.holdings is None and args.holdings_file is None and sys.stdin.isatty():
            min_loss = args.min_loss
            raw = input(f"Min %% loss to flag as TLH (default {min_loss}): ").strip()
            if raw:
                try:
                    min_loss = float(raw)
                except ValueError:
                    print(f"Invalid input. Using default: {min_loss}%%")
            return run_actual_mode(min_loss_percentage=min_loss)
        return run_actual_mode(holdings_text=args.holdings,
                               holdings_file=args.holdings_file,
                               min_loss_percentage=args.min_loss)

    # SIM mode keeps the old interactive threshold prompt.
    min_loss = args.min_loss
    if sys.stdin.isatty() and argv is None:
        raw = input(f"Min %% loss to flag as TLH (default {min_loss}): ").strip()
        if raw:
            try:
                min_loss = float(raw)
            except ValueError:
                print(f"Invalid input. Using default: {min_loss}%%")
    return run_sim_mode(num_transactions=args.num, budget=args.budget,
                        min_loss_percentage=min_loss)


if __name__ == "__main__":
    main()
    

def get_portfolio_data(transactions: List[Transaction]) -> Dict:
    """
    Returns portfolio data including cash balance, portfolio holdings, and gains/losses.
    """
    portfolio, cash_balance, total_realized_gains = calculate_portfolio(transactions)
    
    # Calculate unrealized gains/losses
    unrealized_gains_losses = {}
    for ticker, data in portfolio.items():
        if data['owned'] > 0:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="1d")
                if not hist.empty:
                    current_price = hist.iloc[-1]['Close']
                    current_value = current_price * data['owned']
                    unrealized_gain_loss = current_value - data['cost_basis']
                    unrealized_gains_losses[ticker] = {
                        'current_value': current_value,
                        'cost_basis': data['cost_basis'],
                        'unrealized_gain_loss': unrealized_gain_loss
                    }
            except Exception:
                unrealized_gains_losses[ticker] = {
                    'current_value': None,
                    'cost_basis': data['cost_basis'],
                    'unrealized_gain_loss': None
                }
    
    return {
        'cash_balance': cash_balance,
        'portfolio': portfolio,
        'gains_losses': {
            'realized': total_realized_gains,
            'unrealized': unrealized_gains_losses
        }
    }

def get_tax_opportunities(transactions: List[Transaction]) -> List[Dict[str, any]]:
    """
    Returns tax opportunities based on the current portfolio.
    """
    portfolio, _, _ = calculate_portfolio(transactions)
    return identify_tax_loss_opportunities(portfolio, transactions)