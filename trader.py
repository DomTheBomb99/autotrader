import os
import time
import threading
import numpy as np
import pandas as pd
import requests

from flask import Flask, request, redirect

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

# =========================================================
# RAILWAY SAFE CONFIG
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
# FLASK (MUST BE FIRST BEFORE ROUTES)
# =========================================================

app = Flask(__name__)

# =========================================================
# ML MODEL
# =========================================================

model = LogisticRegression()
scaler = StandardScaler()

X_data = []
y_data = []
model_ready = False

# =========================================================
# BOT STATE
# =========================================================

bot = {
    "running": True,
    "last": "booted",
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ","META","AMZN"]
}

# =========================================================
# API HELPERS
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
# MARKET DATA
# =========================================================

def bars(symbol, tf="1Min", limit=80):

    url = f"{DATA_URL}/stocks/{symbol}/bars"

    r = requests.get(url, headers=HEADERS, params={"timeframe": tf, "limit": limit})

    if r.status_code != 200:
        return None

    data = r.json().get("bars", [])
    return pd.DataFrame(data) if data else None

# =========================================================
# FEATURE ENGINE
# =========================================================

def make_features(df):

    return [
        df["c"].iloc[-1],
        df["c"].rolling(5).mean().iloc[-1],
        df["c"].rolling(20).mean().iloc[-1],
        df["v"].iloc[-1],
        df["v"].rolling(10).mean().iloc[-1],
        df["h"].max() - df["l"].min()
    ]

# =========================================================
# AUTO TRAIN DATA GENERATION
# =========================================================

def generate_data():

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 80)

        if df is None or len(df) < 40:
            continue

        for i in range(25, len(df)-1):

            window = df.iloc[:i]
            future = df.iloc[i+1]

            x = [
                window["c"].iloc[-1],
                window["c"].rolling(5).mean().iloc[-1],
                window["c"].rolling(20).mean().iloc[-1],
                window["v"].iloc[-1],
                window["v"].rolling(10).mean().iloc[-1],
                window["h"].max() - window["l"].min()
            ]

            y = 1 if future["c"] > window["c"].iloc[-1] else 0

            X_data.append(x)
            y_data.append(y)

# =========================================================
# TRAIN ML MODEL
# =========================================================

def train_model():

    global model_ready

    if len(X_data) < 40:
        return

    X = np.array(X_data)
    y = np.array(y_data)

    scaler.fit(X)
    Xs = scaler.transform(X)

    model.fit(Xs, y)

    model_ready = True

# =========================================================
# ML PREDICTION
# =========================================================

def predict(x):

    if not model_ready:
        return 0.5

    x = scaler.transform([x])

    return model.predict_proba(x)[0][1]

# =========================================================
# RANKING SYSTEM (TOP PICKS ONLY)
# =========================================================

def rank_symbols():

    ranked = []

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 60)

        if df is None or len(df) < 30:
            continue

        x = make_features(df)

        conf = predict(x)

        ranked.append((s, conf))

    ranked.sort(key=lambda x: x[1], reverse=True)

    return ranked[:3]

# =========================================================
# EXECUTION
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

# =========================================================
# MAIN LOOP
# =========================================================

def loop():

    while True:

        try:

            if not bot["running"]:
                time.sleep(5)
                continue

            generate_data()
            train_model()

            top = rank_symbols()

            for sym, conf in top:

                if conf > 0.65:
                    buy(sym)

            time.sleep(20)

        except Exception as e:
            bot["last"] = str(e)
            time.sleep(5)

# =========================================================
# CONTROLS
# =========================================================

@app.route("/toggle")
def toggle():

    bot["running"] = not bot["running"]

    return redirect("/")

@app.route("/buy/<symbol>")
def manual(symbol):

    buy(symbol)

    return redirect("/")

# =========================================================
# DASHBOARD (RAILWAY SAFE + FULL UI)
# =========================================================

@app.route("/")
def dashboard():

    acc = account()
    pos = positions()

    equity = acc.get("equity", "0")
    cash = acc.get("cash", "0")
    buying_power = acc.get("buying_power", "0")

    total_pl = sum(float(p["unrealized_pl"]) for p in pos) if pos else 0

    portfolio = ""

    for p in pos:

        portfolio += f"""
        <tr>
            <td>{p['symbol']}</td>
            <td>{p['qty']}</td>
            <td>${p['market_value']}</td>
            <td>${p['avg_entry_price']}</td>
            <td style="color:{'lime' if float(p['unrealized_pl'])>=0 else 'red'}">
                ${p['unrealized_pl']}
            </td>
        </tr>
        """

    return f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="10">
    <style>

    body {{
        background:#0b0f1a;
        color:white;
        font-family:Arial;
    }}

    .grid {{
        display:grid;
        grid-template-columns:1fr 1fr 1fr;
        gap:10px;
        padding:10px;
    }}

    .box {{
        background:rgba(255,255,255,0.06);
        padding:12px;
        border-radius:10px;
    }}

    table {{
        width:100%;
    }}

    td,th {{
        padding:6px;
        border-bottom:1px solid #1f2937;
    }}

    button {{
        padding:8px;
        background:#2563eb;
        color:white;
        border:none;
        border-radius:8px;
    }}

    </style>
    </head>

    <body>

    <div class="grid">

        <div class="box">
            <h3>Account</h3>
            Equity: ${equity}<br>
            Cash: ${cash}<br>
            Buying Power: ${buying_power}<br>
        </div>

        <div class="box">
            <h3>Bot</h3>
            Running: {bot['running']}<br>
            ML Ready: {model_ready}<br>
            Samples: {len(X_data)}<br>
            Last: {bot['last']}<br>

            <a href="/toggle"><button>Start/Stop</button></a>
        </div>

        <div class="box">
            <h3>P&L</h3>
            Total: <span style="color:{'lime' if total_pl>=0 else 'red'}">${total_pl:.2f}</span>
        </div>

    </div>

    <div class="box" style="margin:10px">

        <h3>Portfolio</h3>

        <table>
        <tr>
            <th>Symbol</th>
            <th>Qty</th>
            <th>Value</th>
            <th>Entry</th>
            <th>P/L</th>
        </tr>
        {portfolio}
        </table>

    </div>

    </body>
    </html>
    """

# =========================================================
# STARTUP (IMPORTANT ORDER)
# =========================================================

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
