import json
import time
import subprocess
import os
import requests
import urllib.request
import gzip
import csv
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# UPSTOX CONFIGURATION
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not UPSTOX_ACCESS_TOKEN:
    raise ValueError("Missing critical environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and UPSTOX_ACCESS_TOKEN must be set.")

try:
    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "900"))
except ValueError:
    POLL_INTERVAL_SECONDS = 900

# ==========================================
# STATE
# ==========================================
SUBSCRIBERS_FILE = "telegram_subscribers.json"
ALERTED_OPTIONS_FILE = "alerted_options.json"
LAST_UPDATE_ID = None

def load_alerted_options():
    try:
        with open(ALERTED_OPTIONS_FILE, 'r') as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_alerted_options(alerted_set):
    try:
        with open(ALERTED_OPTIONS_FILE, 'w') as f:
            json.dump(list(alerted_set), f)
    except Exception:
        pass

ALERTED_OPTIONS = load_alerted_options()

# ==========================================
# TELEGRAM ALERT & SUBSCRIBER LOGIC
# ==========================================
def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        # Default to the main user (you)
        subs = {int(TELEGRAM_CHAT_ID)}
        save_subscribers(subs)
        return subs

def save_subscribers(subs_set):
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(list(subs_set), f)

def check_new_subscribers():
    global LAST_UPDATE_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    # On first run, grab the latest updates to clear history and avoid spamming old messages
    if LAST_UPDATE_ID is None:
        try:
            res = requests.get(url, timeout=5).json()
            if res.get("ok") and res.get("result"):
                LAST_UPDATE_ID = res["result"][-1]["update_id"] + 1
            else:
                LAST_UPDATE_ID = 0
        except Exception:
            LAST_UPDATE_ID = 0
        return
        
    if LAST_UPDATE_ID > 0:
        url += f"?offset={LAST_UPDATE_ID}"
        
    try:
        res = requests.get(url, timeout=5).json()
        if not res.get("ok"): return
        
        updates = res.get("result", [])
        if not updates: return
            
        subs = load_subscribers()
        new_user_added = False
        welcomed_this_cycle = set()
        
        for update in updates:
            LAST_UPDATE_ID = update["update_id"] + 1
            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"]
                chat_id = update["message"]["chat"]["id"]
                
                if text.startswith("/start"):
                    if chat_id not in subs:
                        subs.add(chat_id)
                        new_user_added = True
                        print(f"\n[*] NEW TELEGRAM USER SUBSCRIBED! Chat ID: {chat_id}")
                    
                    if chat_id not in welcomed_this_cycle:
                        welcomed_this_cycle.add(chat_id)
                        welcome_msg = (
                            "👋 <b>Welcome to the Live NSE Option Pivot Bot!</b>\n\n"
                            "🤖 <b>What this bot does:</b>\n"
                            "This bot continuously scans the NSE F&O market during live hours and sends high-probability 15-minute OTM Breakout alerts based on R3 Pivot Points. It helps you catch huge momentum moves automatically!\n\n"
                            "✅ <b>You are now subscribed.</b> You will receive live alerts here during market hours."
                        )
                        
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": welcome_msg, "parse_mode": "HTML"})
        
        if new_user_added:
            save_subscribers(subs)
            print(f"[*] Total Active Subscribers: {len(subs)}\n")
            
    except Exception as e:
        pass

def send_telegram_alert(message):
    subs = load_subscribers()
    for chat_id in subs:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception:
            pass
        # Sleep slightly to prevent hitting Telegram broadcast limits (30 msg/sec)
        time.sleep(0.05)

# ==========================================
# NSE / MARKET FETCHING LOGIC
# ==========================================
def fetch_nse_curl(url):
    try:
        result = subprocess.run([
            "curl", "-s",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "-H", "Accept-Language: en-US,en;q=0.9",
            url
        ], capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        pass
    return None

def get_market_variations(index_type="gainers"):
    url = f"https://www.nseindia.com/api/live-analysis-variations?index={index_type}"
    data = fetch_nse_curl(url)
    if data and "FOSec" in data:
        return data.get("FOSec", {}).get("data", [])
    return []

UPSTOX_INSTRUMENTS = None
def get_upstox_instrument_key(symbol):
    global UPSTOX_INSTRUMENTS
    if UPSTOX_INSTRUMENTS is None:
        print("Downloading Upstox instruments mapping...")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            req = urllib.request.Request('https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz')
            with urllib.request.urlopen(req, context=ctx) as response:
                with gzip.GzipFile(fileobj=response) as uncompressed:
                    content = uncompressed.read().decode('utf-8')
            UPSTOX_INSTRUMENTS = {}
            for row in csv.DictReader(content.splitlines()):
                UPSTOX_INSTRUMENTS[row['tradingsymbol']] = row['instrument_key']
        except Exception as e:
            print(f"Error downloading instruments: {e}")
            return f"NSE_EQ|{symbol}"
    return UPSTOX_INSTRUMENTS.get(symbol, f"NSE_EQ|{symbol}")

def get_option_chain_upstox(symbol):
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'}
    instrument_key = get_upstox_instrument_key(symbol)
    
    contract_url = f"https://api.upstox.com/v2/option/contract?instrument_key={instrument_key}"
    try:
        res = requests.get(contract_url, headers=headers, timeout=10).json()
        if res.get('status') != 'success' or not res.get('data'): return []
        
        expiries = sorted([c['expiry'] for c in res['data'] if 'expiry' in c])
        if not expiries: return []
        nearest_expiry = expiries[0]
        
        chain_url = f"https://api.upstox.com/v2/option/chain?instrument_key={instrument_key}&expiry_date={nearest_expiry}"
        chain_data = requests.get(chain_url, headers=headers, timeout=10).json()
        if chain_data.get('status') != 'success' or not chain_data.get('data'): return []
        
        normalized_data = []
        for strike_data in chain_data['data']:
            strike_price = strike_data.get('strike_price')
            entry = {"strikePrice": strike_price}
            if 'call_options' in strike_data and strike_data['call_options']:
                co = strike_data['call_options']
                market_data = co.get('market_data', {})
                ohlc = market_data.get('ohlc', {})
                entry['CE'] = {
                    "strikePrice": strike_price,
                    "instrument_key": co.get("instrument_key"),
                    "openPrice": ohlc.get('open', 0),
                    "highPrice": ohlc.get('high', 0),
                    "lowPrice": ohlc.get('low', 0),
                    "lastPrice": market_data.get('ltp', 0),
                    "volume": market_data.get('volume', 0)
                }
            if 'put_options' in strike_data and strike_data['put_options']:
                po = strike_data['put_options']
                market_data = po.get('market_data', {})
                ohlc = market_data.get('ohlc', {})
                entry['PE'] = {
                    "strikePrice": strike_price,
                    "instrument_key": po.get("instrument_key"),
                    "openPrice": ohlc.get('open', 0),
                    "highPrice": ohlc.get('high', 0),
                    "lowPrice": ohlc.get('low', 0),
                    "lastPrice": market_data.get('ltp', 0),
                    "volume": market_data.get('volume', 0)
                }
            normalized_data.append(entry)
        return normalized_data
    except Exception:
        return []

def get_completed_15m_closes(instrument_key):
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'}
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/1minute"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get('status') != 'success' or not res.get('data') or not res['data'].get('candles'):
            return []
            
        minute_candles = res['data']['candles'] # descending order
        closes = {}
        for c in minute_candles:
            dt = datetime.fromisoformat(c[0])
            interval_start = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
            if interval_start not in closes:
                # First seen descending = last minute of that interval = 15m close
                closes[interval_start] = c[4]
                
        now = datetime.now(list(closes.keys())[0].tzinfo)
        today_date = now.date()
        completed_closes = []
        for interval_start, close_price in sorted(closes.items()):
            if interval_start.date() != today_date:
                continue
            if now >= interval_start + timedelta(minutes=15):
                completed_closes.append({'time': interval_start.strftime('%H:%M'), 'close': close_price})
        return completed_closes
    except Exception as e:
        print(f"Error fetching 1m candles for 15m conversion: {e}")
        return []

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_yesterday_ohlcv(instrument_key):
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today_str = ist_now.strftime("%Y-%m-%d")
    week_ago = (ist_now - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'}
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{today_str}/{week_ago}"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if 'data' in res and 'candles' in res['data'] and res['data']['candles']:
            for c in res['data']['candles']:
                candle_date = c[0].split('T')[0]
                if candle_date < today_str:
                    return c[1], c[2], c[3], c[4], c[5]
    except Exception as e:
        pass
    return 0, 0, 0, 0, 0

def calculate_zerodha_r3(y_open, y_high, y_low, y_close):
    if not all([y_open, y_high, y_low, y_close]) or y_high == 0 or y_low == 0: return 0.0
    pp = (y_high + y_low + y_close) / 3.0
    r3 = pp + 2 * (y_high - y_low)
    return r3

def find_5_otm_options(options_data, target_price, opt_type):
    valid_opts = []
    for opt in options_data:
        if opt_type not in opt: continue
        strike = opt.get("strikePrice", 0)
        if opt_type == "CE" and strike >= target_price:
            valid_opts.append(opt[opt_type])
        elif opt_type == "PE" and strike <= target_price:
            valid_opts.append(opt[opt_type])
            
    if opt_type == "CE":
        valid_opts.sort(key=lambda x: x["strikePrice"])
    else:
        valid_opts.sort(key=lambda x: x["strikePrice"], reverse=True)
        
    return valid_opts[:5]

def process_option(data, opt_type, symbol, underlying_ltp, underlying_pchange, alerts):
    global ALERTED_OPTIONS
    
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    today_str = ist_now.strftime("%Y-%m-%d")
    
    inst_key = data.get("instrument_key")
    strike = data.get("strikePrice")
    today_volume = data.get("volume", 0)
    opt_ltp = data.get("lastPrice", 0)
    
    # Prefix the alert key with today's date so it resets automatically every day!
    alert_key = f"{today_str}_{symbol}_{strike}_{opt_type}"
    
    if alert_key in ALERTED_OPTIONS or not inst_key:
        return
        
    if today_volume <= 0:
        return
        
    y_open, y_high, y_low, y_close, y_vol = get_yesterday_ohlcv(inst_key)
    
    if y_vol <= 0:
        return
    
    r3 = calculate_zerodha_r3(y_open, y_high, y_low, y_close)
    if r3 <= 0: return
    
    target_value = 2 * r3
    print(f"      -> {strike} {opt_type}: LTP={opt_ltp:.2f}, R3={r3:.2f}, 2*R3={target_value:.2f}")
    
    # Check 15m completed candles
    completed_15m = get_completed_15m_closes(inst_key)
    for c in completed_15m:
        if c['close'] > target_value:
            trend_emoji = "📈" if underlying_pchange >= 0 else "📉"
            msg = f"""🚨 <b>15M OTM BREAKOUT ALERT</b> 🚨

{trend_emoji} <b>Stock:</b> {symbol}
💰 <b>Stock LTP:</b> {underlying_ltp} (Move: {underlying_pchange:.2f}%)

🎯 <b>Option Strike:</b> {strike} {opt_type}
🏷️ <b>Option LTP:</b> ₹{opt_ltp:.2f}
📊 <b>15m Close Price:</b> ₹{c['close']}
⏰ <b>Time of Close:</b> {c['time']}
📦 <b>Today's Vol:</b> {int(today_volume):,}
📦 <b>Yday's Vol:</b> {int(y_vol):,}

<b>--- Pivot Targets ---</b>
📍 <b>Zerodha R3:</b> ₹{r3:.2f}
🔥 <b>2*R3 Target:</b> ₹{target_value:.2f}

✅ <b>Condition Met:</b> Close (₹{c['close']}) &gt; 2*R3 (₹{target_value:.2f})"""
            alerts.append(msg)
            ALERTED_OPTIONS.add(alert_key)
            save_alerted_options(ALERTED_OPTIONS)
            print(f"      *** ALERT MATCHED: {strike} {opt_type} at {c['time']}! ***")
            break # Alerted for this option

# ==========================================
# MAIN LOOP
# ==========================================
IST = timezone(timedelta(hours=5, minutes=30))

def is_market_open():
    now_ist = datetime.now(IST)
    if now_ist.weekday() > 4: # 0=Mon, 4=Fri
        return False
    
    market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_start <= now_ist <= market_end

def run_scanner():
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting Upstox F&O Scanner (Every {POLL_INTERVAL_SECONDS} sec)...")
    while True:
        # Check for new users sending /start to the bot
        check_new_subscribers()
        
        if not is_market_open():
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Market is closed (Mon-Fri 09:15-15:30 IST).")
            # Loop for 60s checking telegram frequently so bot stays responsive
            for _ in range(12):
                time.sleep(5)
                check_new_subscribers()
            continue
            
        print(f"\n[{datetime.now(IST).strftime('%H:%M:%S')}] Fetching FOSec Movers directly from NSE site...")
        gainers = get_market_variations("gainers")
        losers = get_market_variations("loosers")
        alerts = []
        
        # Process Gainers
        for stock in gainers:
            symbol = stock.get("symbol")
            ltp = stock.get("ltp")
            pchange = stock.get("perChange", 0)
            
            if not symbol or not ltp: continue
                
            print(f"  [Gainer] {symbol} @ {ltp} ({pchange:.2f}%)")
            oc_data = get_option_chain_upstox(symbol)
            if not oc_data: continue
                
            otm_opts = find_5_otm_options(oc_data, ltp * 1.05, "CE")
            for opt in otm_opts:
                process_option(opt, "CE", symbol, ltp, pchange, alerts)
                
        # Process Losers
        for stock in losers:
            symbol = stock.get("symbol")
            ltp = stock.get("ltp")
            pchange = stock.get("perChange", 0)
            
            if not symbol or not ltp: continue
                
            print(f"  [Loser] {symbol} @ {ltp} ({pchange:.2f}%)")
            oc_data = get_option_chain_upstox(symbol)
            if not oc_data: continue
                
            otm_opts = find_5_otm_options(oc_data, ltp * 0.95, "PE")
            for opt in otm_opts:
                process_option(opt, "PE", symbol, ltp, pchange, alerts)
                
        if alerts:
            print(f"Sending {len(alerts)} alerts to Telegram...")
            for alert_msg in alerts:
                send_telegram_alert(alert_msg)
                time.sleep(1)
                
        # Sleep for POLL_INTERVAL_SECONDS but check subscribers frequently
        sleep_intervals = max(1, POLL_INTERVAL_SECONDS // 5)
        for _ in range(sleep_intervals):
            time.sleep(5)
            check_new_subscribers()

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
        
    def log_message(self, format, *args):
        # Suppress logging to prevent cluttering console output
        return

def start_health_check_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"[*] Starting health check server on port {port} for Render Free Tier...")
    server.serve_forever()

def main():
    print("=======================================")
    print("LIVE NSE 15-MIN OPTION PIVOTS SCANNER")
    print("=======================================")
    
    # Start the background HTTP health check server for Render Free Tier support
    health_thread = threading.Thread(target=start_health_check_server, daemon=True)
    health_thread.start()
    
    while True:
        run_scanner()
        print(f"Sleeping for {POLL_INTERVAL_SECONDS} seconds...\n")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
