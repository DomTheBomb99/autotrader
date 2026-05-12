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
        requests.post(
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
        last_actions.append(f"{side.upper()} {symbol}")

    except Exception as e:
        last_actions.append(f"ORDER ERROR {str(e)}")

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
# SIMPLE SIGNAL ENGINE
# =========================================================

def score_symbol(symbol):

    df = get_bars(symbol)
    if df is None or len(df) < 20:
        return None

    price = df["c"].iloc[-1]
    trend = df["c"].iloc[-1] - df["c"].iloc[-10]

    score = trend

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

        ranked = []

        for s in bot["watchlist"]:
            r = score_symbol(s)
            if r:
                ranked.append(r)

        ranked.sort(key=lambda x: x["score"], reverse=True)

        acc = account()
        pos = positions()

        # simple execution logic
        for r in ranked[:2]:

            if r["score"] > 0.5:
                order(r["symbol"], "buy", 1)

        # stream updates
        socketio.emit("update", {
            "account": acc,
            "positions": pos,
            "ranked": ranked,
            "activity": last_actions[-12:]
        })

        time.sleep(3)

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

body {
    margin:0;
    background:#0b1220;
    color:#e5e7eb;
    font-family:Arial;
}

/* HEADER */
.header {
    display:flex;
    justify-content:space-between;
    padding:15px 20px;
    background:#111827;
    border-bottom:1px solid #1f2937;
}

/* GRID */
.grid {
    display:grid;
    grid-template-columns:1.2fr 1fr 1fr;
    gap:15px;
    padding:15px;
}

/* CARDS */
.card {
    background:rgba(255,255,255,0.05);
    border:1px solid rgba(255,255,255,0.08);
    border-radius:14px;
    padding:15px;
}

/* TITLES */
h2,h3 {
    margin:0 0 10px 0;
}

/* TEXT */
.muted {
    color:#9ca3af;
    font-size:13px;
}

/* ITEMS */
.item {
    padding:6px 0;
    border-bottom:1px solid #1f2937;
    font-size:14px;
}

/* BIG NUMBER */
.big {
    font-size:26px;
    font-weight:bold;
}

.green { color:#22c55e; }
.red { color:#ef4444; }

</style>
</head>

<body>

<div class="header">
    <div>
        <b>AI Swing Trading Terminal</b>
        <div class="muted">Live Alpaca Paper Trading Bot</div>
    </div>
</div>

<div class="grid">

    <!-- ACCOUNT -->
    <div class="card">
        <h3>Account</h3>
        <div class="big" id="equity">Loading...</div>
        <div class="muted">Equity</div>

        <br>

        <div id="cash"></div>
        <div class="muted">Cash</div>
    </div>

    <!-- SIGNALS -->
    <div class="card">
        <h3>Top Opportunities</h3>
        <div id="ranked"></div>
    </div>

    <!-- ACTIVITY -->
    <div class="card">
        <h3>Activity</h3>
        <div id="activity"></div>
    </div>

</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>

<script>

const socket = io();

socket.on("update", (d) => {

    document.getElementById("equity").innerHTML =
        "$" + (d.account.equity || "0");

    document.getElementById("cash").innerHTML =
        "$" + (d.account.cash || "0");

    document.getElementById("ranked").innerHTML =
        d.ranked.map(r =>
            `<div class="item">
                <b>${r.symbol}</b>
                <span class="${r.score > 0 ? 'green' : 'red'}">
                    ${r.score.toFixed(2)}
                </span>
                <div class="muted">$${r.price}</div>
            </div>`
        ).join("");

    document.getElementById("activity").innerHTML =
        d.activity.map(a => `<div class="item">${a}</div>`).join("");

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
