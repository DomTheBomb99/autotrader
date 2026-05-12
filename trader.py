import os
import time
import threading
import numpy as np
import pandas as pd
import requests

from flask import Flask
from flask_socketio import SocketIO

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

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
# APP + SOCKETS
# =========================================================

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# =========================================================
# AI MODEL
# =========================================================

model = LogisticRegression()
scaler = StandardScaler()

X_data = []
y_data = []
model_ready = False

# =========================================================
# STATE
# =========================================================

bot = {
    "running": True,
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"],
    "last_cycle": 0,
    "max_positions": 4
}

last_actions = []

# =========================================================
# ALPACA API
# =========================================================

def get_account():
    r = requests.get(BASE_URL + "/v2/account", headers=HEADERS)
    return r.json() if r.status_code == 200 else {}

def get_positions():
    r = requests.get(BASE_URL + "/v2/positions", headers=HEADERS)
    return r.json() if r.status_code == 200 else []

def place_order(symbol, side, qty=1):

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

        if r.status_code in [200, 201]:
            last_actions.append(f"{side.upper()} {symbol}")
        else:
            last_actions.append(f"ORDER FAIL {symbol}")

    except Exception as e:
        last_actions.append(f"ERROR {str(e)}")

# =========================================================
# MARKET DATA
# =========================================================

def get_bars(symbol):

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
    return [
        df["c"].iloc[-1],
        df["c"].rolling(5).mean().iloc[-1],
        df["c"].rolling(20).mean().iloc[-1],
        df["v"].iloc[-1],
        df["h"].max() - df["l"].min()
    ]

# =========================================================
# ML TRAIN
# =========================================================

def train():

    global model_ready

    if len(X_data) < 50:
        return

    X = np.array(X_data)
    y = np.array(y_data)

    scaler.fit(X)
    Xs = scaler.transform(X)

    model.fit(Xs, y)

    model_ready = True

# =========================================================
# PREDICT
# =========================================================

def predict(x):

    if not model_ready:
        return 0.5

    return model.predict_proba(scaler.transform([x]))[0][1]

# =========================================================
# SCANNER
# =========================================================

def scan_market():

    ranked = []

    for s in bot["watchlist"]:

        df = get_bars(s)

        if df is None or len(df) < 30:
            continue

        x = features(df)
        conf = predict(x)

        ranked.append({
            "symbol": s,
            "confidence": float(conf),
            "price": float(df["c"].iloc[-1])
        })

    ranked.sort(key=lambda x: x["confidence"], reverse=True)

    return ranked[:5]

# =========================================================
# TRADING ENGINE
# =========================================================

def engine():

    while True:

        if not bot["running"]:
            time.sleep(1)
            continue

        train()

        account = get_account()
        positions = get_positions()
        ranked = scan_market()

        # =====================================================
        # RISK CONTROL
        # =====================================================

        if len(positions) >= bot["max_positions"]:
            last_actions.append("MAX POSITIONS REACHED")
            time.sleep(3)
            continue

        # =====================================================
        # EXECUTION LOGIC
        # =====================================================

        for r in ranked:

            symbol = r["symbol"]
            conf = r["confidence"]

            last_actions.append(f"{symbol} {conf:.2f}")

            if conf > 0.72:
                place_order(symbol, "buy", 1)

            if conf < 0.35:
                # simple exit logic
                for p in positions:
                    if p["symbol"] == symbol:
                        place_order(symbol, "sell", p["qty"])

        # =====================================================
        # STREAM DATA TO UI
        # =====================================================

        socketio.emit("update", {
            "account": {
                "equity": account.get("equity"),
                "cash": account.get("cash")
            },
            "positions": positions,
            "ranked": ranked,
            "last_actions": last_actions[-15:],
            "model_ready": model_ready
        })

        time.sleep(3)

# =========================================================
# UI (NO REFRESH - SOCKET ONLY)
# =========================================================

@app.route("/")
def index():

    return """
<!DOCTYPE html>
<html>
<head>
<title>AI Quant Terminal</title>

<style>
body {
    margin:0;
    background:#0a0f1f;
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

<h2 style="padding:15px;">Pro AI Trading Terminal</h2>

<div class="grid">

<div class="box">
<h3>Account</h3>
<div id="account">loading...</div>
</div>

<div class="box">
<h3>Top Trades</h3>
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

socket.on("update", (data) => {

document.getElementById("account").innerHTML =
"Equity: $" + data.account.equity + "<br>Cash: $" + data.account.cash;

document.getElementById("ranked").innerHTML =
data.ranked.map(r =>
`<div><b>${r.symbol}</b> - ${(r.confidence*100).toFixed(1)}% - $${r.price}</div>`
).join("");

document.getElementById("activity").innerHTML =
data.last_actions.map(a => `<div>${a}</div>`).join("");

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
