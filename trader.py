import os
import time
import threading
import numpy as np
import pandas as pd
import requests

from flask import Flask, redirect
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

# =========================================================
# RAILWAY CONFIG
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
# FLASK (MUST BE FIRST)
# =========================================================

app = Flask(__name__)

# =========================================================
# ML SYSTEM
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
    "last": "booted",
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"]
}

last_actions = []
next_cycle = 0

# =========================================================
# API
# =========================================================

def get(url):
    return requests.get(BASE_URL + url, headers=HEADERS)

def post(url, payload):
    return requests.post(BASE_URL + url, json=payload, headers=HEADERS)

def account():
    r = get("/v2/account")
    return r.json() if r.status_code == 200 else {}

def positions():
    r = get("/v2/positions")
    return r.json() if r.status_code == 200 else []

# =========================================================
# DATA
# =========================================================

def bars(symbol, tf="1Min", limit=80):

    url = f"{DATA_URL}/stocks/{symbol}/bars"

    r = requests.get(url, headers=HEADERS, params={"timeframe": tf, "limit": limit})

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
        df["v"].rolling(10).mean().iloc[-1],
        df["h"].max() - df["l"].min()
    ]

# =========================================================
# AUTO DATA GENERATION (ML LEARNING)
# =========================================================

def generate():

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 80)

        if df is None or len(df) < 40:
            continue

        for i in range(25, len(df)-1):

            w = df.iloc[:i]
            f = df.iloc[i+1]

            x = [
                w["c"].iloc[-1],
                w["c"].rolling(5).mean().iloc[-1],
                w["c"].rolling(20).mean().iloc[-1],
                w["v"].iloc[-1],
                w["v"].rolling(10).mean().iloc[-1],
                w["h"].max() - w["l"].min()
            ]

            y = 1 if f["c"] > w["c"].iloc[-1] else 0

            X_data.append(x)
            y_data.append(y)

# =========================================================
# TRAIN MODEL
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

    x = scaler.transform([x])

    return model.predict_proba(x)[0][1]

# =========================================================
# RANKING SYSTEM
# =========================================================

def rank():

    ranked = []

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 60)

        if df is None or len(df) < 30:
            continue

        x = features(df)

        conf = predict(x)

        ranked.append((s, conf))

    ranked.sort(key=lambda x: x[1], reverse=True)

    return ranked[:3]

# =========================================================
# BUY
# =========================================================

def buy(symbol):

    post("/v2/orders", {
        "symbol": symbol,
        "qty": 1,
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc"
    })

    bot["last"] = f"BUY {symbol}"
    last_actions.append(f"BUY {symbol}")

# =========================================================
# LOOP
# =========================================================

def loop():

    global next_cycle

    while True:

        try:

            if not bot["running"]:
                time.sleep(2)
                continue

            next_cycle = 20

            last_actions.append("Scanning market...")

            generate()
            train()

            top = rank()

            for s, c in top:

                last_actions.append(f"{s} confidence {c:.2f}")

                if c > 0.65:
                    buy(s)

            for i in range(20):
                next_cycle = 20 - i
                time.sleep(1)

        except Exception as e:
            last_actions.append(str(e))
            time.sleep(3)

# =========================================================
# CONTROLS
# =========================================================

@app.route("/toggle")
def toggle():

    bot["running"] = not bot["running"]

    return redirect("/")

# =========================================================
# DASHBOARD (MODERN TERMINAL UI)
# =========================================================

@app.route("/")
def dash():

    acc = account()
    pos = positions()

    equity = acc.get("equity", "0")
    cash = acc.get("cash", "0")

    total_pl = sum(float(p["unrealized_pl"]) for p in pos) if pos else 0

    portfolio = ""
    for p in pos:
        portfolio += f"""
        <div class="row">
            <span>{p['symbol']}</span>
            <span>{p['qty']}</span>
            <span>${p['market_value']}</span>
            <span style="color:{'lime' if float(p['unrealized_pl'])>=0 else 'red'}">
                ${p['unrealized_pl']}
            </span>
        </div>
        """

    activity = ""
    for a in list(last_actions)[-10:]:
        activity += f"<div class='feed'>{a}</div>"

    ranked = ""
    for s, c in rank():
        ranked += f"""
        <div class="card">
            <b>{s}</b><br>
            Confidence: {round(c*100,1)}%
        </div>
        """

    return f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="5">

    <style>

    body {{
        margin:0;
        background:#0a0f1f;
        color:white;
        font-family:Arial;
    }}

    .top {{
        display:flex;
        justify-content:space-between;
        padding:15px;
        background:#111827;
    }}

    .grid {{
        display:grid;
        grid-template-columns:1fr 1fr 1fr;
        gap:15px;
        padding:15px;
    }}

    .panel {{
        background:rgba(255,255,255,0.05);
        border-radius:14px;
        padding:15px;
    }}

    .row {{
        display:flex;
        justify-content:space-between;
        padding:5px 0;
        border-bottom:1px solid #1f2937;
    }}

    .feed {{
        font-size:12px;
        opacity:0.8;
    }}

    .card {{
        background:rgba(255,255,255,0.06);
        padding:10px;
        margin:5px 0;
        border-radius:10px;
    }}

    button {{
        padding:8px 12px;
        border:none;
        border-radius:8px;
        background:#2563eb;
        color:white;
    }}

    </style>
    </head>

    <body>

    <div class="top">
        <div><b>AI Trading Terminal</b></div>
        <div>Next Update: {next_cycle}s</div>
        <div><a href="/toggle"><button>{"STOP" if bot['running'] else "START"}</button></a></div>
    </div>

    <div class="grid">

        <div class="panel">
            <h3>Account</h3>
            Equity: ${equity}<br>
            Cash: ${cash}<br>
            P/L: <span style="color:{'lime' if total_pl>=0 else 'red'}">${total_pl:.2f}</span>
        </div>

        <div class="panel">
            <h3>Activity</h3>
            {activity}
        </div>

        <div class="panel">
            <h3>Opportunities</h3>
            {ranked}
        </div>

        <div class="panel">
            <h3>Portfolio</h3>
            {portfolio}
        </div>

    </div>

    </body>
    </html>
    """

# =========================================================
# START
# =========================================================

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
