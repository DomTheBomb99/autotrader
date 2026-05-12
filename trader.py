import os
import time
import threading
import requests
import numpy as np
import pandas as pd
from flask import Flask
from flask_socketio import SocketIO
from plyer import notification

# =========================================================
# CONFIG
# =========================================================
API_KEY = os.environ.get("ALPACA_API_KEY", "YOUR_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET", "YOUR_SECRET")
BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

PORT = int(os.environ.get("PORT", 7777))

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# =========================================================
# STATE
# =========================================================
bot = {
    "running": True,
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"],
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "last_summary_date": ""
}

last_actions = []

# =========================================================
# NOTIFICATIONS & HELPERS
# =========================================================
def send_desktop_notify(title, message):
    try:
        notification.notify(
            title=title,
            message=message,
            app_name='QuantumBot',
            timeout=10
        )
    except:
        pass

def get_account_data():
    r = requests.get(BASE_URL + "/v2/account", headers=HEADERS)
    return r.json() if r.status_code == 200 else {}

def get_positions():
    r = requests.get(BASE_URL + "/v2/positions", headers=HEADERS)
    return r.json() if r.status_code == 200 else []

def order(symbol, side="buy", qty=1):
    try:
        res = requests.post(
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
        if res.status_code == 200:
            msg = f"{side.upper()} {symbol} (1 Qty)"
            last_actions.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            send_desktop_notify("Trade Executed", msg)
        else:
            last_actions.append(f"ORDER FAILED: {symbol}")
    except Exception as e:
        last_actions.append(f"ERROR: {str(e)}")

# =========================================================
# SIGNAL ENGINE (YOUR ORIGINAL ML LOGIC GOES HERE)
# =========================================================
def get_bars(symbol):
    # This detects if it's crypto or stock and hits the right Alpaca endpoint
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    if "/" in symbol:
        url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        
    r = requests.get(url, headers=HEADERS, params={"timeframe": "1Min", "limit": 50})
    if r.status_code != 200: return None
    data = r.json().get("bars", [])
    return pd.DataFrame(data) if data else None

def score_symbol(symbol):
    df = get_bars(symbol)
    if df is None or len(df) < 20: return None

    # --- YOUR ORIGINAL ML / LOGIC STARTS HERE ---
    price = df["c"].iloc[-1]
    trend = df["c"].iloc[-1] - df["c"].iloc[-10]
    # (Insert any scikit-learn or complex math from your original code here)
    score = trend 
    # --- END ORIGINAL LOGIC ---

    return {"symbol": symbol, "score": float(score), "price": float(price)}

# =========================================================
# MAIN ENGINE
# =========================================================
def engine():
    while True:
        if not bot["running"]:
            time.sleep(1); continue

        # Check market clock
        clock = requests.get(BASE_URL + "/v2/clock", headers=HEADERS).json()
        is_open = clock.get("is_open", False)

        # 1. Daily Summary at Close
        curr_time = time.strftime("%H:%M")
        curr_date = time.strftime("%Y-%m-%d")
        if curr_time == "16:01" and bot.get("last_summary_date") != curr_date:
            acc = get_account_data()
            send_desktop_notify("Market Close Summary", f"Final Equity: ${acc.get('equity', '0')}")
            bot["last_summary_date"] = curr_date

        # 2. Pick Watchlist (Crypto if closed)
        active_list = bot["watchlist"] if is_open else bot["crypto_watchlist"]
        
        ranked = []
        for s in active_list:
            r = score_symbol(s)
            if r: ranked.append(r)

        ranked.sort(key=lambda x: x["score"], reverse=True)

        # 3. Execution Logic
        for r in ranked[:1]:
            if r["score"] > 0.5:
                order(r["symbol"], "buy", 1)

        # 4. Sync UI
        socketio.emit("update", {
            "account": get_account_data(),
            "positions": get_positions(),
            "ranked": ranked,
            "activity": last_actions[-12:],
            "market_status": "OPEN" if is_open else "CRYPTO MODE"
        })
        time.sleep(5)

# =========================================================
# UI (FIXED & MODERNIZED)
# =========================================================
@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>QUANTUMBOT TERMINAL</title>
    <style>
        :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --green: #238636; --red: #da3633; }
        body { margin:0; background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif; }
        .header { display: flex; justify-content: space-between; padding: 15px 25px; background: var(--card); border-bottom: 1px solid var(--border); }
        .grid { display: grid; grid-template-columns: 300px 1fr 320px; gap: 15px; padding: 15px; height: 90vh; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 15px; overflow-y: auto; }
        .label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; }
        .stat-val { font-size: 24px; font-weight: bold; margin: 5px 0 15px 0; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; color: #8b949e; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
        td { padding: 10px 0; border-bottom: 1px solid var(--border); }
        .green { color: #39d353; } .red { color: #f85149; }
        .item { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <div><b>QUANTUM</b>BOT <span id="mkt" style="margin-left:15px; font-size:12px; color:#8b949e;">SYNCING...</span></div>
        <div id="status">● LIVE</div>
    </div>
    <div class="grid">
        <div class="card">
            <div class="label">Total Equity</div>
            <div class="stat-val" id="equity">$0.00</div>
            <div class="label">Buying Power</div>
            <div class="stat-val" id="cash" style="font-size:18px;">$0.00</div>
            <hr style="border:0; border-top:1px solid var(--border); margin:15px 0;">
            <div class="label">Signals</div>
            <div id="ranked"></div>
        </div>
        <div class="card">
            <div class="label">Open Positions</div>
            <table>
                <thead><tr><th>Asset</th><th>Qty</th><th>Value</th><th>Day P/L</th></tr></thead>
                <tbody id="pos-list"></tbody>
            </table>
        </div>
        <div class="card">
            <div class="label">Activity Logs</div>
            <div id="logs" style="font-family:monospace; font-size:11px; margin-top:10px;"></div>
        </div>
    </div>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <script>
        const socket = io();
        const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
        socket.on("update", (d) => {
            document.getElementById("equity").innerText = f.format(d.account.equity || 0);
            document.getElementById("cash").innerText = f.format(d.account.buying_power || 0);
            document.getElementById("mkt").innerText = "MODE: " + d.market_status;
            document.getElementById("pos-list").innerHTML = d.positions.map(p => `
                <tr>
                    <td><b>${p.symbol}</b></td>
                    <td>${p.qty}</td>
                    <td>${f.format(p.market_value)}</td>
                    <td class="${p.unrealized_intraday_pl >= 0 ? 'green' : 'red'}">${f.format(p.unrealized_intraday_pl)}</td>
                </tr>`).join("");
            document.getElementById("ranked").innerHTML = d.ranked.map(r => `
                <div class="item">
                    <span>${r.symbol}</span>
                    <span class="${r.score >= 0 ? 'green' : 'red'}">${r.score.toFixed(2)}</span>
                </div>`).join("");
            document.getElementById("logs").innerHTML = d.activity.map(a => `<div style="margin-bottom:5px;">${a}</div>`).join("");
        });
    </script>
</body>
</html>
"""

threading.Thread(target=engine, daemon=True).start()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=PORT)
