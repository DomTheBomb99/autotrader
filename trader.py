import eventlet
eventlet.monkey_patch() # THIS MUST BE AT THE VERY TOP FOR RAILWAY TO WORK

import os
import time
import threading
import requests
import pandas as pd
from flask import Flask
from flask_socketio import SocketIO

# =========================================================
# CONFIG - HARDCODED PAPER KEYS
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
# Added ping_interval settings to keep Railway connections alive
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", ping_timeout=60, ping_interval=15)

bot = {
    "running": True,
    "watchlist": ["AAPL", "TSLA", "NVDA", "AMD", "SPY", "QQQ"],
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD"]
}

activity_log = ["System Initialized... Booting Cloud Engine"]

def log_event(msg):
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    activity_log.append(full_msg)
    if len(activity_log) > 25:
        activity_log.pop(0)

# =========================================================
# SAFE ALPACA API HELPERS
# =========================================================
def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
        if r.status_code != 200:
            log_event(f"Account Sync Error: {r.text}")
            return {"equity": "0.00", "buying_power": "0.00", "last_equity": "0.00"}
        return r.json()
    except Exception as e:
        log_event(f"Network Error: {str(e)}")
        return {"equity": "0.00", "buying_power": "0.00", "last_equity": "0.00"}

def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
        if r.status_code != 200:
            return []
        return r.json()
    except:
        return []

def place_order(symbol, qty, current_price):
    try:
        # 1. First, buy the asset at Market Price
        buy_payload = {
            "symbol": symbol,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc"
        }
        r_buy = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=buy_payload)
        
        if r_buy.status_code == 200:
            log_event(f"BOUGHT {qty} {symbol} at ~${current_price}")
            
            # 2. Immediately attach a Trailing Stop Sell Order
            trail_payload = {
                "symbol": symbol,
                "qty": qty,
                "side": "sell",
                "type": "trailing_stop",
                "trail_percent": 2.0,  # Trails 2% behind the highest price
                "time_in_force": "gtc"
            }
            
            # Tiny delay to let Alpaca register the buy before we place the sell
            time.sleep(1) 
            r_trail = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=trail_payload)
            
            if r_trail.status_code == 200:
                log_event(f"ATTACHED 2% Trailing Stop to {symbol}")
            else:
                log_event(f"TRAIL STOP FAILED {symbol}: {r_trail.json().get('message', r_trail.text)}")
                
        else:
            log_event(f"REJECTED {symbol}: {r_buy.json().get('message', r_buy.text)}")
            
    except Exception as e:
        log_event(f"ORDER FAILED: {str(e)}")
# =========================================================
# MARKET DATA & AI SIGNAL ENGINE
# =========================================================
def get_bars(symbol):
    is_crypto = "/" in symbol
    if is_crypto:
        url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params = {"symbols": symbol, "timeframe": "1Min", "limit": 20}
    else:
        url = f"{DATA_URL}/stocks/{symbol}/bars"
        params = {"timeframe": "1Min", "limit": 20, "feed": "iex"}
    
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200:
            return None
            
        data = r.json()
        bars = data.get("bars", {})
        
        if isinstance(bars, dict):
            bars = bars.get(symbol, [])
            
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except:
        return None

def score_symbol(symbol):
    df = get_bars(symbol)
    if df is None or len(df) < 10:
        return None

    current_price = float(df["c"].iloc[-1])
    past_price = float(df["c"].iloc[-10])
    
    # Percentage Change logic
    percent_change = ((current_price - past_price) / past_price) * 100

    return {"symbol": symbol, "score": percent_change, "price": current_price}

# =========================================================
# TRADING ENGINE THREAD
# =========================================================
def engine():
    log_event("Alpaca Connection Established. Scanning markets...")
    while True:
        try:
            # 1. Market Status
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS)
            is_open = clock_req.json().get("is_open", False) if clock_req.status_code == 200 else False
            
            active_list = bot["watchlist"] if is_open else bot["crypto_watchlist"]
            ranked = []

            # 2. Score Assets
            for s in active_list:
                r = score_symbol(s)
                if r:
                    ranked.append(r)

            ranked.sort(key=lambda x: x["score"], reverse=True)

            # 3. Fetch Portfolio
            acc = get_account()
            pos = get_positions()

            # 4. Execute (0.1% momentum trigger)
            for r in ranked[:2]:
                if r["score"] > 0.1:
                    already_hold = any(p.get('symbol') == r['symbol'] for p in pos)
                    if not already_hold:
                        place_order(r["symbol"], 1, r["price"])

            # 5. Broadcast to UI
            socketio.emit("update", {
                "account": {
                    "equity": acc.get("equity", "0.00"), 
                    "cash": acc.get("buying_power", "0.00"),
                    "last_equity": acc.get("last_equity", "0.00") # Grab yesterday's close for math
                },
                "positions": pos,
                "ranked": ranked[:8],
                "activity": activity_log[::-1],
                "market_status": "MARKET OPEN" if is_open else "MARKET CLOSED (CRYPTO ACTIVE)"
            })

        except Exception as e:
            log_event(f"ENGINE CRASH LOOP: {str(e)}")

        time.sleep(3) # Send data to UI every 3 seconds

# =========================================================
# UI DASHBOARD
# =========================================================
@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Trading Terminal</title>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:300px 1fr 350px; gap:20px; padding:20px; height: calc(100vh - 80px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; overflow:hidden; }
    .card-body { overflow-y:auto; flex-grow:1; }
    .muted { color:#9ca3af; font-size:11px; text-transform:uppercase; font-weight:800; letter-spacing:1px; margin-bottom:5px; }
    .big { font-size:32px; font-weight:bold; color:#fff; }
    table { width:100%; border-collapse:collapse; font-size:13px; text-align:left; }
    th { color:#9ca3af; padding-bottom:12px; border-bottom:1px solid var(--border); font-weight:600; }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .item { display:flex; justify-content:space-between; padding:12px 0; border-bottom:1px solid var(--border); font-size:14px; }
    .pill { background:#4b5563; color:#fff; padding:4px 10px; border-radius:20px; font-size:11px; font-weight:900; letter-spacing:0.5px;}
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
</style>
</head>
<body>

<div class="header">
    <div>
        <span style="font-weight:900; font-size:18px; tracking:wide;">QUANTUM<span style="color:var(--green)">BOT</span></span>
        <span id="mkt" style="margin-left:20px; font-size:12px; color:#9ca3af; font-weight:bold;">INITIALIZING SERVER...</span>
    </div>
    <div class="pill" id="status">CONNECTING...</div>
</div>

<div class="grid">
    <div class="card">
        <div class="muted">Net Equity</div>
        <div style="display: flex; align-items: baseline; gap: 10px; margin-bottom: 15px;">
            <div class="big" id="equity">$0.00</div>
            <div id="equity-pct" style="font-size: 16px; font-weight: bold; color: #9ca3af;">0.00%</div>
        </div>
        
        <div class="muted">Buying Power</div>
        <div id="cash" style="font-size:20px; font-weight:bold; margin-bottom:25px; color:#9ca3af;">$0.00</div>
        <hr style="border:0; border-top:1px solid var(--border); margin-bottom:20px;">
        <div class="muted" style="margin-bottom:10px;">Live AI Momentum (% Change)</div>
        <div class="card-body" id="ranked"></div>
    </div>

    <div class="card">
        <div class="muted" style="margin-bottom:15px;">Live Holdings & Stop Losses</div>
        <div class="card-body">
            <table>
                <thead>
                    <tr><th>Asset</th><th>Qty</th><th>Entry</th><th>Current</th><th>P/L</th><th>Stop Loss</th></tr>
                </thead>
                <tbody id="pos-table"></tbody>
            </table>
        </div>
    </div>

    <div class="card">
        <div class="muted" style="margin-bottom:15px;">System Execution Log</div>
        <div class="card-body" id="logs" style="font-family:'Courier New', monospace; font-size:12px; line-height:1.6; color:#a1a1aa;"></div>
    </div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io();
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    const pct = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 2 });
    
    // Connection Debuggers
    const statusBtn = document.getElementById("status");
    socket.on('connect', () => { statusBtn.innerText = "WEB LINKED"; statusBtn.style.background = "#f59e0b"; });
    socket.on('disconnect', () => { statusBtn.innerText = "DISCONNECTED"; statusBtn.style.background = "#ef4444"; document.getElementById("mkt").innerText = "LOST CONNECTION"; });

    socket.on("update", (d) => {
        statusBtn.innerText = "LIVE SYNC";
        statusBtn.style.background = "#22c55e";

        // MATH FOR THE EQUITY PERCENTAGE CHANGE
        const currentEq = parseFloat(d.account.equity || 0);
        const lastEq = parseFloat(d.account.last_equity || currentEq);
        let pctChange = 0;
        
        if (lastEq > 0) {
            pctChange = ((currentEq - lastEq) / lastEq) * 100;
        }

        document.getElementById("equity").innerText = f.format(currentEq);
        document.getElementById("cash").innerText = f.format(d.account.cash || 0);
        document.getElementById("mkt").innerText = d.market_status;

        // Apply colors and +/- to the new UI element
        const eqPctEl = document.getElementById("equity-pct");
        if (pctChange === 0) {
            eqPctEl.innerText = "0.00%";
            eqPctEl.style.color = "#9ca3af";
        } else {
            const isPos = pctChange > 0;
            eqPctEl.innerText = (isPos ? "+" : "") + pctChange.toFixed(2) + "%";
            eqPctEl.style.color = isPos ? "var(--green)" : "var(--red)";
        }

        if (d.positions.length === 0) {
            document.getElementById("pos-table").innerHTML = "<tr><td colspan='6' style='text-align:center; color:#6b7280; padding-top:20px;'>No active positions.</td></tr>";
        } else {
            document.getElementById("pos-table").innerHTML = d.positions.map(p => {
                const isProfit = p.unrealized_intraday_pl >= 0;
                const plColor = isProfit ? '#22c55e' : '#ef4444';
                const entry = parseFloat(p.avg_entry_price);
                const stopLoss = entry * 0.98;
                return `
                <tr>
                    <td><b style="color:#fff;">${p.symbol}</b></td>
                    <td>${p.qty}</td>
                    <td>${f.format(entry)}</td>
                    <td>${f.format(p.current_price)}</td>
                    <td style="color:${plColor}; font-weight:bold;">${f.format(p.unrealized_intraday_pl)} <br><span style="font-size:11px;">(${pct.format(p.unrealized_intraday_plpc)})</span></td>
                    <td style="color:#ef4444;">${f.format(stopLoss)}</td>
                </tr>`;
            }).join("");
        }

        document.getElementById("ranked").innerHTML = d.ranked.map(r => `
            <div class="item">
                <span style="color:#fff;"><b>${r.symbol}</b> <span style="font-size:11px; color:#6b7280; margin-left:5px;">${f.format(r.price)}</span></span>
                <span style="color:${r.score >= 0 ? '#22c55e' : '#ef4444'}; font-weight:bold;">${r.score > 0 ? '+' : ''}${r.score.toFixed(2)}%</span>
            </div>`).join("");

        document.getElementById("logs").innerHTML = d.activity.map(a => `
            <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1f2937;">
                ${a.includes('ERROR') || a.includes('REJECTED') || a.includes('CRASH') ? `<span style="color:#ef4444">${a}</span>` : 
                  a.includes('BOUGHT') ? `<span style="color:#22c55e">${a}</span>` : a}
            </div>
        `).join("");
    });
</script>

</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=PORT)
