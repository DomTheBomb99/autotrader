import os
import time
import threading
import numpy as np
import pandas as pd
import requests

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
# BOT STATE
# =========================================================

bot = {
    "running": True,
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"],
    "cycle": 10
}

# adaptive strategy weights (THIS IS THE "LEARNING")
strategy_scores = {
    "momentum": 1.0,
    "mean_revert": 1.0
}

trade_memory = []
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

def order(symbol, side, qty=1):

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

def bars(symbol):

    r = requests.get(
        f"{DATA_URL}/stocks/{symbol}/bars",
        headers=HEADERS,
        params={"timeframe": "1Min", "limit": 60}
    )

    if r.status_code != 200:
        return None

    data = r.json().get("bars", [])
    return pd.DataFrame(data) if data else None

# =========================================================
# FEATURES
# =========================================================

def features(df):

    return {
        "price": df["c"].iloc[-1],
        "trend": df["c"].iloc[-1] - df["c"].iloc[-10],
        "volatility": df["h"].max() - df["l"].min(),
        "volume": df["v"].iloc[-1]
    }

# =========================================================
# AGGRESSIVE SIGNAL ENGINE
# =========================================================

def score(symbol):

    df = bars(symbol)
    if df is None or len(df) < 30:
        return None

    f = features(df)

    momentum_score = f["trend"] * strategy_scores["momentum"]
    mean_revert_score = -abs(f["trend"]) * strategy_scores["mean_revert"]

    total = momentum_score + mean_revert_score

    return {
        "symbol": symbol,
        "score": total,
        "price": f["price"]
    }

# =========================================================
# LEARNING SYSTEM
# =========================================================

def learn(trade_result, strategy_used):

    if trade_result == "win":
        strategy_scores[strategy_used] *= 1.05
    else:
        strategy_scores[strategy_used] *= 0.95

# =========================================================
# ENGINE
# =========================================================

def engine():

    while True:

        if not bot["running"]:
            time.sleep(1)
            continue

        ranked = []

        for s in bot["watchlist"]:
            r = score(s)
            if r:
                ranked.append(r)

        ranked.sort(key=lambda x: x["score"], reverse=True)

        account_data = account()
        pos = positions()

        # aggressive trading logic
        for r in ranked[:3]:

            if r["score"] > 0.5:
                order(r["symbol"], "buy", 1)

        # simulate learning from past trades (simplified)
        if len(trade_memory) > 5:
            last = trade_memory[-1]
            learn(last["result"], "momentum")

        socketio.emit("update", {
            "account": account_data,
            "positions": pos,
            "ranked": ranked,
            "strategies": strategy_scores,
            "activity": last_actions[-10:]
        })

        time.sleep(bot["cycle"])

# =========================================================
# UI
# =========================================================

@app.route("/")
def home():

    return """
<!DOCTYPE html>
<html>
<head>
<title>Aggressive Swing AI</title>

<style>

body {
    margin:0;
    background:#0b0f1a;
    color:white;
    font-family:Arial;
}

.grid {
    display:grid;
    grid-template-columns:1fr 1fr 1fr;
    gap:15px;
    padding:15px;
}

.box {
    background:rgba(255,255,255,0.05);
    padding:15px;
    border-radius:12px;
}

</style>

</head>

<body>

<h2 style="padding:15px;">Aggressive AI Swing Trader</h2>

<div class="grid">

<div class="box">
<h3>Account</h3>
<div id="account"></div>
</div>

<div class="box">
<h3>Top Signals</h3>
<div id="ranked"></div>
</div>

<div class="box">
<h3>Activity</h3>
<div id="activity"></div>
</div>

</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>

<script>

const socket = io();

socket.on("update", (d) => {

document.getElementById("account").innerHTML =
"Equity: $" + (d.account.equity || "loading");

document.getElementById("ranked").innerHTML =
d.ranked.map(r =>
`<div><b>${r.symbol}</b> score: ${r.score.toFixed(2)} $${r.price}</div>`
).join("");

document.getElementById("activity").innerHTML =
d.activity.map(a => `<div>${a}</div>`).join("");

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
