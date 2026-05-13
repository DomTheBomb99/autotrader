import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime
from flask import Flask
from flask_socketio import SocketIO

# =========================================================
# CONFIG - PAPER KEYS
# =========================================================
API_KEY = "PKRY2XRZW4K4TWX4NYSEROPQEK"
API_SECRET = "8pySz6LGdhjNr8tHfbgMcCgF2cK5Qd3afmy4CbwmZznQ"

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

PORT = int(os.environ.get("PORT", 7777))

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", ping_timeout=60, ping_interval=15)

UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMD", "META", "AMZN", "MSFT", "GOOGL", "NFLX", 
    "COIN", "MARA", "RIOT", "MSTR", "PLTR", "SNOW", "UBER", "ROKU", "SQ", 
    "PYPL", "HOOD", "GME", "AMC", "BA", "DIS", "LCID", "RIVN", "SOFI", "DKNG"
]

bot = {
    "running": True,
    "watchlist": [], 
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
    "trade_amount_usd": 20.00,
    "last_eod_date": "",
    "last_scan_date": "",
    "risk_pct": 2.0,    
    "reward_pct": 6.0   
}

activity_log = ["System Initialized... Fixing Crypto Rejections"]

def log_event(msg):
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    activity_log.append(full_msg)
    if len(activity_log) > 25:
        activity_log.pop(0)

def run_daily_scanner():
    log_event("📡 SCANNER: Pulling top movers for today...")
    try:
        url = f"{DATA_URL}/stocks/snapshots"
        params = {"symbols": ",".join(UNIVERSE), "feed": "iex"}
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return False
        data = r.json()
        movers = []
        for symbol, snap in data.items():
            try:
                prev_close = snap["prevDailyBar"]["c"]
                current_price = snap["latestTrade"]["p"]
                if prev_close > 0:
                    pct_change = ((current_price - prev_close) / prev_close) * 100
                    movers.append({"symbol": symbol, "change": pct_change})
            except: continue
        movers.sort(key=lambda x: x["change"], reverse=True)
        top_5 = [m["symbol"] for m in movers[:5]]
        bot["watchlist"] = top_5
        log_event(f"🎯 TARGETS: {', '.join(top_5)}")
        return True
    except: return False

def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
        return r.json() if r.status_code == 200 else {"equity": "0.00", "cash": "0.00"}
    except: return {"equity": "0.00", "cash": "0.00"}

def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
        return r.json() if r.status_code == 200 else []
    except: return []

def place_order(symbol, current_price):
    try:
        is_crypto = "/" in symbol
        qty = round(bot["trade_amount_usd"] / current_price, 5)
        
        # Calculate Exits
        tp_price = current_price * (1 + (bot["reward_pct"] / 100))
        sl_price = current_price * (1 - (bot["risk_pct"] / 100))

        # Fix Error 1: Ensure at least $0.01 spread for crypto take-profit (DOGE Fix)
        if is_crypto:
            if (tp_price - current_price) < 0.01:
                tp_price = current_price + 0.011
            if (current_price - sl_price) < 0.01:
                sl_price = current_price - 0.011

        take_profit = round(tp_price, 4 if is_crypto else 2)
        stop_loss = round(sl_price, 4 if is_crypto else 2)

        # Fix Error 2: Use "simple" for Crypto, "bracket" for Stocks
        if is_crypto:
            payload = {
                "symbol": symbol, "qty": qty, "side": "buy", "type": "market",
                "time_in_force": "gtc"
            }
        else:
            payload = {
                "symbol": symbol, "qty": qty, "side": "buy", "type": "market",
                "time_in_force": "day",
                "order_class": "bracket",
                "take_profit": {"limit_price": take_profit},
                "stop_loss": {"stop_price": stop_loss}
            }
            
        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload)
        if r.status_code == 200:
            log_event(f"BOUGHT {symbol} | Target: ${take_profit} | Stop: ${stop_loss}")
        else:
            log_event(f"REJECTED {symbol}: {r.text}")
    except Exception as e:
        log_event(f"ORDER ERROR: {str(e)}")

def get_bars(symbol):
    is_crypto = "/" in symbol
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"symbols": symbol, "timeframe": "1Min", "limit": 30} if is_crypto else {"timeframe": "1Min", "limit": 30, "feed": "iex"}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return None
        data = r.json()
        bars = data.get("bars", {}).get(symbol, []) if is_crypto else data.get("bars", [])
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except: return None

def score_symbol(symbol):
    try:
        df = get_bars(symbol)
        if df is None or len(df) < 2: return {"symbol": symbol, "score": 0.0, "price": 0.0}
        current_price = float(df["c"].iloc[-1])
        past_price = float(df["c"].iloc[0]) 
        percent_change = ((current_price - past_price) / past_price) * 100 if past_price > 0 else 0.0
        return {"symbol": symbol, "score": percent_change, "price": current_price}
    except: return {"symbol": symbol, "score": 0.0, "price": 0.0}

def engine():
    while True:
        try:
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            if is_open and bot["last_scan_date"] != clock_data.get('timestamp', '')[:10]:
                run_daily_scanner()
                bot["last_scan_date"] = clock_data.get('timestamp', '')[:10]

            active_list = bot["watchlist"] + bot["crypto_watchlist"]
            if len(bot["watchlist"]) == 0:
                active_list = ["TSLA", "NVDA", "AMD", "COIN", "MARA"] + bot["crypto_watchlist"]

            ranked = []
            for s in active_list:
                r = score_symbol(s)
                if r: ranked.append(r)

            ranked.sort(key=lambda x: x["score"], reverse=True)
            acc = get_account()
            pos = get_positions()

            # 1. HANDLE BUYING
            for r in ranked[:2]:
                if r["score"] > 0.15:
                    if not any(p.get('symbol') == r['symbol'] for p in pos):
                        place_order(r["symbol"], r["price"])

            # 2. HANDLE CRYPTO EXITS (Manual SL/TP since we can't use brackets)
            for p in pos:
                if "/" in p['symbol']:
                    entry = float(p['avg_entry_price'])
                    curr = float(p['current_price'])
                    gain = ((curr - entry) / entry) * 100
                    if gain >= bot["reward_pct"] or gain <= -bot["risk_pct"]:
                        log_event(f"EXIT {p['symbol']} at {gain:.2f}%")
                        requests.delete(f"{BASE_URL}/v2/positions/{p['symbol']}", headers=HEADERS)

            socketio.emit("update", {
                "account": acc, "positions": pos,
                "ranked": ranked[:10], "activity": activity_log[::-1],
                "market_status": "MARKET OPEN" if is_open else "MARKET CLOSED",
                "is_open": is_open, "next_event": clock_data.get('next_close', '') if is_open else clock_data.get('next_open', '')
            })
        except Exception as e:
            log_event(f"ENGINE ERROR: {str(e)}")
        time.sleep(3)

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>QuantumBot</title>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system, sans-serif; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:300px 1fr 350px; gap:20px; padding:20px; height: calc(100vh - 80px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; overflow:hidden; }
    .card-body { overflow-y:auto; flex-grow:1; }
    .muted { color:#9ca3af; font-size:11px; text-transform:uppercase; font-weight:800; letter-spacing:1px; }
    .big { font-size:32px; font-weight:bold; color:#fff; margin: 10px 0; }
    table { width:100%; border-collapse:collapse; font-size:13px; text-align:left; }
    th { color:#9ca3af; padding-bottom:12px; border-bottom:1px solid var(--border); }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .pill { background:#4b5563; color:#fff; padding:4px 10px; border-radius:20px; font-size:11px; font-weight:900; }
    .countdown-box { text-align:center; padding:40px 20px; border: 2px dashed var(--border); border-radius:10px; margin-top:20px; }
    @media (max-width: 1024px) { .grid { grid-template-columns: 1fr; height: auto; } .card { max-height: 500px; } }
</style>
</head>
<body>
<div class="header">
    <div><span style="font-weight:900; font-size:18px;">QUANTUM<span style="color:var(--green)">BOT</span></span><span id="mkt" style="margin-left:20px; font-size:12px; color:#9ca3af; font-weight:bold;">...</span></div>
    <div class="pill" id="status">CONNECTING</div>
</div>
<div class="grid">
    <div class="card"><div class="muted">Net Equity</div><div class="big" id="equity">$0.00</div><div class="muted">Buying Power</div><div id="cash" style="font-weight:bold;">$0.00</div><hr style="border:0; border-top:1px solid var(--border); margin:20px 0;"><div class="muted" style="margin-bottom:10px;">Scanned Active Targets</div><div class="card-body" id="ranked"></div></div>
    <div class="card"><div class="muted">Live 1:3 Risk/Reward Positions</div><div class="card-body" id="pos-container"><table><thead><tr><th>Asset</th><th>Entry</th><th>Current</th><th>P/L</th><th>Stop</th><th>Target</th></tr></thead><tbody id="pos-table"></tbody></table></div></div>
    <div class="card"><div class="muted">Execution Log</div><div class="card-body" id="logs" style="font-family:monospace; font-size:11px; color:#a1a1aa;"></div></div>
</div>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io({ transports: ["websocket", "polling"], upgrade: true });
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    function updateCountdown(endTime) {
        if (!endTime) return "Calculating...";
        const diff = new Date(endTime) - new Date();
        if (diff <= 0) return "Closing Session...";
        const h = Math.floor(diff / 3600000), m = Math.floor((diff % 3600000) / 60000), s = Math.floor((diff % 60000) / 1000);
        return `${h}h ${m}m ${s}s`;
    }
    socket.on("update", (d) => {
        document.getElementById("status").innerText = "LIVE SYNC"; document.getElementById("status").style.background = "#22c55e";
        document.getElementById("equity").innerText = f.format(d.account.equity); document.getElementById("cash").innerText = f.format(d.account.cash);
        document.getElementById("mkt").innerText = d.market_status;

        const posContainer = document.getElementById("pos-table");
        if (d.positions.length > 0) {
            posContainer.innerHTML = d.positions.map(p => {
                const entry = parseFloat(p.avg_entry_price);
                const isCrypto = p.symbol.includes("/");
                // For crypto, the bot manages exits manually, so we show what it's aiming for
                const sl = isCrypto ? entry * 0.98 : (p.stop_loss || entry * 0.98);
                const tp = isCrypto ? entry * 1.06 : (p.take_profit || entry * 1.06);
                return `<tr><td><b>${p.symbol}</b></td><td>${f.format(entry)}</td><td>${f.format(p.current_price)}</td><td style="color:${p.unrealized_intraday_pl >= 0 ? 'var(--green)' : 'var(--red)'}">${f.format(p.unrealized_intraday_pl)}</td><td style="color:var(--red)">${f.format(sl)}</td><td style="color:var(--blue)">${f.format(tp)}</td></tr>`;
            }).join("");
        } else {
            document.getElementById("pos-container").innerHTML = `<div class="countdown-box"><div class="muted">No Active Positions</div><div style="font-size:14px; color:var(--orange); font-weight:bold; margin:10px 0;">📡 Hunting for Breakouts...</div><hr style="border:0; border-top:1px solid var(--border); margin:15px 0;"><div class="muted">${d.is_open ? 'Session Closes In:' : 'Next Session Starts In:'}</div><div style="font-size:24px; font-weight:bold; color:#fff;">${updateCountdown(d.next_event)}</div></div>`;
        }

        document.getElementById("ranked").innerHTML = d.ranked.map(r => {
            const threshold = 0.15;
            let estMins = r.score > 0 ? Math.round(((threshold-r.score)/r.score) * 15) : "---";
            let status = r.score >= threshold ? "⚡ TRIGGERED" : (r.score > 0 ? `📈 Est. ${estMins}m to target` : "📉 Sleeping/Reversing");
            let color = r.score >= threshold ? "var(--green)" : (r.score > 0 ? "var(--orange)" : "var(--red)");
            return `<div style="padding:10px 0; border-bottom:1px solid var(--border);"><div style="display:flex; justify-content:space-between;"><b>${r.symbol}</b> <span>${r.score.toFixed(2)}%</span></div><div style="font-size:11px; color:${color}; font-weight:bold; margin-top:4px;">${status}</div></div>`;
        }).join("");
        document.getElementById("logs").innerHTML = d.activity.map(a => `<div style="padding:4px 0; border-bottom:1px solid #1f2937;">${a}</div>`).join("");
    });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
