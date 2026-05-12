import os
import time
import threading
import requests
import numpy as np
import pandas as pd

from flask import Flask
from flask_socketio import SocketIO

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

# =========================================================
# APP
# =========================================================

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# =========================================================
# STATE
# =========================================================

bot = {
    "running": True,
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"],
}

last_actions = []

# =========================================================
# ALPACA
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
            last_actions.append(f"[{time.strftime('%H:%M:%S')}] {side.upper()} {symbol} - SUCCESS")
        else:
            last_actions.append(f"[{time.strftime('%H:%M:%S')}] {side.upper()} {symbol} - FAILED")

    except Exception as e:
        last_actions.append(f"ORDER ERROR: {str(e)}")

# =========================================================
# MARKET DATA
# =========================================================

def get_bars(symbol):
    r = requests.get(
        f"{DATA_URL}/stocks/{symbol}/bars",
        headers=HEADERS,
        params={"timeframe": "1Min", "limit": 50}
    )

    if r.status_code != 200:
        return None

    data = r.json().get("bars", [])
    return pd.DataFrame(data) if data else None

# =========================================================
# TRADING ENGINE
# =========================================================

def engine():
    while True:
        if not bot["running"]:
            time.sleep(1)
            continue

        ranked = []
        for s in bot["watchlist"]:
            df = get_bars(s)
            if df is not None and len(df) >= 20:
                price = float(df["c"].iloc[-1])
                trend = float(df["c"].iloc[-1] - df["c"].iloc[-10])
                ranked.append({"symbol": s, "score": trend, "price": price})

        ranked.sort(key=lambda x: x["score"], reverse=True)

        # Simple Logic: Buy top ranked if score > 0.5
        for r in ranked[:1]:
            if r["score"] > 0.5:
                order(r["symbol"], "buy", 1)

        acc = account()
        pos = positions()

        socketio.emit("update", {
            "account": acc,
            "positions": pos,
            "ranked": ranked,
            "activity": last_actions[-10:][::-1] # Newest first
        })

        time.sleep(5)

# =========================================================
# UI (MODERNIZED)
# =========================================================

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ALGO TERMINAL v2</title>
    <style>
        :root { --bg: #0a0e17; --card: #161b22; --border: #30363d; --green: #238636; --red: #da3633; --text: #c9d1d9; }
        body { margin:0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica; }
        
        .header { display: flex; justify-content: space-between; align-items: center; padding: 1rem 2rem; background: var(--card); border-bottom: 1px solid var(--border); }
        .status-dot { height: 10px; width: 10px; background-color: var(--green); border-radius: 50%; display: inline-block; margin-right: 5px; }
        
        .grid { display: grid; grid-template-columns: 300px 1fr 350px; gap: 1rem; padding: 1.5rem; height: calc(100vh - 100px); }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; overflow: hidden; }
        .card-header { padding: 10px 15px; background: rgba(255,255,255,0.03); border-bottom: 1px solid var(--border); font-weight: bold; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .card-body { padding: 15px; flex-grow: 1; overflow-y: auto; }
        
        .stat { margin-bottom: 15px; }
        .stat-val { font-size: 24px; font-weight: 800; color: #fff; }
        .stat-label { font-size: 11px; color: #8b949e; text-transform: uppercase; }

        .item { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
        .green { color: #39d353; }
        .red { color: #f85149; }
        
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; color: #8b949e; font-weight: 400; padding-bottom: 10px; }
        td { padding: 8px 0; border-bottom: 1px solid var(--border); }
    </style>
</head>
<body>

<div class="header">
    <div>
        <div style="font-weight: 800; font-size: 18px;">QUANTUM<span style="color:var(--green)">BOT</span></div>
        <div style="font-size: 11px; color: #8b949e;">CONNECTED TO ALPACA PAPER_TRADING</div>
    </div>
    <div id="connection-status"><span class="status-dot"></span> LIVE</div>
</div>

<div class="grid">
    <!-- LEFT: ACCOUNT -->
    <div class="card">
        <div class="card-header">Portfolio Overview</div>
        <div class="card-body">
            <div class="stat">
                <div class="stat-label">Total Equity</div>
                <div class="stat-val" id="equity">$0.00</div>
            </div>
            <div class="stat">
                <div class="stat-label">Buying Power</div>
                <div class="stat-val" id="cash" style="font-size: 18px;">$0.00</div>
            </div>
            <hr style="border:0; border-top:1px solid var(--border); margin: 20px 0;">
            <div class="card-header" style="padding: 0 0 10px 0; background: none; border:0;">Watchlist Signals</div>
            <div id="ranked"></div>
        </div>
    </div>

    <!-- CENTER: POSITIONS -->
    <div class="card">
        <div class="card-header">Open Positions</div>
        <div class="card-body">
            <table>
                <thead>
                    <tr>
                        <th>Asset</th>
                        <th>Qty</th>
                        <th>Market Value</th>
                        <th>P/L Today</th>
                    </tr>
                </thead>
                <tbody id="positions-list"></tbody>
            </table>
        </div>
    </div>

    <!-- RIGHT: LOGS -->
    <div class="card">
        <div class="card-header">Execution Logs</div>
        <div class="card-body" id="activity" style="font-family: monospace; font-size: 11px;">
        </div>
    </div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io();

    socket.on("update", (d) => {
        // Update Account
        document.getElementById("equity").innerText = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(d.account.equity || 0);
        document.getElementById("cash").innerText = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(d.account.cash || 0);

        // Update Signals
        document.getElementById("ranked").innerHTML = d.ranked.map(r => `
            <div class="item">
                <span><b>${r.symbol}</b> <span style="color:#8b949e; margin-left:5px;">$${r.price.toFixed(2)}</span></span>
                <span class="${r.score > 0 ? 'green' : 'red'}">${r.score > 0 ? '▲' : '▼'} ${Math.abs(r.score).toFixed(2)}</span>
            </div>
        `).join("");

        // Update Positions
        document.getElementById("positions-list").innerHTML = d.positions.map(p => `
            <tr>
                <td><b>${p.symbol}</b></td>
                <td>${p.qty}</td>
                <td>$${parseFloat(p.market_value).toFixed(2)}</td>
                <td class="${parseFloat(p.unrealized_intraday_pl) >= 0 ? 'green' : 'red'}">
                    $${parseFloat(p.unrealized_intraday_pl).toFixed(2)}
                </td>
            </tr>
        `).join("");

        // Update Logs
        document.getElementById("activity").innerHTML = d.activity.map(a => `
            <div style="margin-bottom: 5px; color: ${a.includes('FAILED') ? '#f85149' : '#c9d1d9'}">
                ${a}
            </div>
        `).join("");
    });

    socket.on('disconnect', () => {
        document.querySelector('.status-dot').style.backgroundColor = '#da3633';
        document.getElementById('connection-status').innerText = 'DISCONNECTED';
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
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
