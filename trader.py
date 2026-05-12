import os
import time
import threading
import requests
import numpy as np
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

# =========================================================
# APP & STATE
# =========================================================
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

bot = {
    "running": True,
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"],
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD"]
}

last_actions = ["Bot initialized on Railway... Connecting to Alpaca"]

# =========================================================
# ALPACA API HELPERS
# =========================================================
def account():
    r = requests.get(BASE_URL + "/v2/account", headers=HEADERS)
    return r.json() if r.status_code == 200 else {}

def positions():
    r = requests.get(BASE_URL + "/v2/positions", headers=HEADERS)
    return r.json() if r.status_code == 200 else []

def order(symbol, side="buy", qty=1):
    try:
        r = requests.post(
            BASE_URL + "/v2/orders",
            headers=HEADERS,
            json={
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "gtc"
            }
        )
        if r.status_code == 200:
            last_actions.append(f"[{time.strftime('%H:%M:%S')}] {side.upper()} {symbol} SUCCESS")
        else:
            last_actions.append(f"[{time.strftime('%H:%M:%S')}] ORDER FAILED: {r.text}")
    except Exception as e:
        last_actions.append(f"ORDER ERROR {str(e)}")

# =========================================================
# MARKET DATA & ORIGINAL ML SIGNAL ENGINE
# =========================================================
def get_bars(symbol):
    is_crypto = "/" in symbol
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/{symbol}/bars"
    
    try:
        r = requests.get(url, headers=HEADERS, params={"timeframe": "1Min", "limit": 50})
        if r.status_code != 200:
            return None
        data = r.json().get("bars", [])
        return pd.DataFrame(data) if data else None
    except:
        return None

def score_symbol(symbol):
    df = get_bars(symbol)
    if df is None or len(df) < 20:
        return None

    # --- YOUR ML LOGIC GOES HERE ---
    price = df["c"].iloc[-1]
    trend = df["c"].iloc[-1] - df["c"].iloc[-10]
    
    score = trend
    # --- END ML LOGIC ---

    return {
        "symbol": symbol,
        "score": float(score),
        "price": float(price)
    }

# =========================================================
# TRADING ENGINE
# =========================================================
def engine():
    while True:
        if not bot["running"]:
            time.sleep(1)
            continue

        try:
            # Check market clock to swap lists
            clock_req = requests.get(BASE_URL + "/v2/clock", headers=HEADERS)
            is_open = clock_req.json().get("is_open", False) if clock_req.status_code == 200 else False
            
            active_list = bot["watchlist"] if is_open else bot["crypto_watchlist"]
            ranked = []

            for s in active_list:
                r = score_symbol(s)
                if r:
                    ranked.append(r)

            ranked.sort(key=lambda x: x["score"], reverse=True)

            acc = account()
            pos = positions()

            # simple execution logic
            for r in ranked[:2]:
                if r["score"] > 0.5:
                    # Basic check to avoid buying the same thing infinitely
                    if not any(p.get('symbol') == r['symbol'] for p in pos):
                        order(r["symbol"], "buy", 1)

            # stream updates to UI
            socketio.emit("update", {
                "account": {
                    "equity": acc.get("equity", "0"),
                    "cash": acc.get("buying_power", "0")
                },
                "positions": pos if isinstance(pos, list) else [],
                "ranked": ranked[:8],
                "activity": last_actions[-12:][::-1],
                "market_status": "OPEN" if is_open else "CLOSED (CRYPTO MODE)"
            })

        except Exception as e:
            print(f"Engine Loop Error: {e}")

        time.sleep(5)

# =========================================================
# UI
# =========================================================
@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<title>AI Trading Terminal</title>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Arial, sans-serif; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:300px 1fr 300px; gap:20px; padding:20px; height: calc(100vh - 100px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; overflow-y:auto; }
    .muted { color:#9ca3af; font-size:12px; text-transform:uppercase; font-weight:bold; letter-spacing:1px; margin-bottom:5px; }
    .big { font-size:28px; font-weight:bold; margin-bottom:15px; }
    .item { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border); font-size:14px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th { text-align:left; color:#9ca3af; padding-bottom:10px; border-bottom:1px solid var(--border); }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .pill { background:var(--green); color:#000; padding:3px 8px; border-radius:12px; font-size:11px; font-weight:bold; }
</style>
</head>
<body>

<div class="header">
    <div>
        <span style="font-weight:900; font-size:18px;">AI SWING TERMINAL</span>
        <span id="mkt" style="margin-left:15px; font-size:12px; color:#9ca3af;">SYNCING...</span>
    </div>
    <div class="pill" id="status">LIVE DATA</div>
</div>

<div class="grid">
    <div class="card">
        <div class="muted">Net Equity</div>
        <div class="big" id="equity">$0.00</div>
        <div class="muted">Buying Power</div>
        <div id="cash" style="font-size:18px; font-weight:bold; margin-bottom:25px;">$0.00</div>
        
        <div class="muted">Top Opportunities</div>
        <div id="ranked"></div>
    </div>

    <div class="card">
        <div class="muted">Active Positions</div>
        <table>
            <thead><tr><th>Symbol</th><th>Qty</th><th>Value</th><th>P/L Today</th></tr></thead>
            <tbody id="pos-table"></tbody>
        </table>
    </div>

    <div class="card">
        <div class="muted">Execution Log</div>
        <div id="logs" style="font-family:monospace; font-size:12px; margin-top:10px; line-height:1.6;"></div>
    </div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io();
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    socket.on("update", (d) => {
        document.getElementById("equity").innerText = f.format(d.account.equity || 0);
        document.getElementById("cash").innerText = f.format(d.account.cash || 0);
        document.getElementById("mkt").innerText = d.market_status;

        document.getElementById("pos-table").innerHTML = d.positions.map(p => `
            <tr>
                <td><b>${p.symbol}</b></td>
                <td>${p.qty}</td>
                <td>${f.format(p.market_value)}</td>
                <td style="color:${p.unrealized_intraday_pl >= 0 ? '#22c55e' : '#ef4444'}">
                    ${f.format(p.unrealized_intraday_pl)}
                </td>
            </tr>`).join("");

        document.getElementById("ranked").innerHTML = d.ranked.map(r => `
            <div class="item">
                <span><b>${r.symbol}</b></span>
                <span style="color:${r.score >= 0 ? '#22c55e' : '#ef4444'}">${r.score.toFixed(3)}</span>
            </div>`).join("");

        document.getElementById("logs").innerHTML = d.activity.map(a => `
            <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1f2937;">${a}</div>
        `).join("");
    });
</script>

</body>
</html>
"""

# =========================================================
# START
# =========================================================
threading.Thread(target=engine, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=PORT)
