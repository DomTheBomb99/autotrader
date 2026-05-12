import os
import time
import threading
import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from flask import Flask, request, redirect

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

# =========================================================
# ML SYSTEM
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
# AUTO DATA GENERATION (FIX FOR 0 SAMPLES ISSUE)
# =========================================================

def generate_training_data():

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 80)

        if df is None or len(df) < 30:
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

            outcome = 1 if future["c"] > window["c"].iloc[-1] else 0

            X_data.append(x)
            y_data.append(outcome)

# =========================================================
# TRAIN
# =========================================================

def train():

    global model_ready

    if len(X_data) < 30:
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
# EVALUATE
# =========================================================

def evaluate(symbol):

    df = bars(symbol, "1Min", 60)

    if df is None or len(df) < 30:
        return None

    x = features(df)

    return symbol, predict(x), x

# =========================================================
# BUY (SIMPLIFIED PAPER ORDER)
# =========================================================

def buy(symbol):

    requests.post(BASE_URL + "/v2/orders", json={
        "symbol": symbol,
        "qty": 1,
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc"
    }, headers=HEADERS)

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

            generate_training_data()
            train()

            picks = []

            for s in bot["watchlist"]:

                r = evaluate(s)

                if r:

                    sym, conf, x = r

                    if conf > 0.65:
                        picks.append((sym, conf))

            picks.sort(key=lambda x: x[1], reverse=True)

            for sym, conf in picks[:2]:

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
# DASHBOARD (FIXED + BUTTONS)
# =========================================================

@app.route("/")
def dash():

    html = f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="10">

    <style>

    body {{
        background:#0b0f1a;
        color:white;
        font-family:Arial;
    }}

    .box {{
        background:rgba(255,255,255,0.06);
        padding:15px;
        margin:10px;
        border-radius:12px;
    }}

    button {{
        padding:10px;
        margin:5px;
        background:#2563eb;
        color:white;
        border:none;
        border-radius:8px;
    }}

    </style>
    </head>

    <body>

    <div class="box">
        <h2>ML Trading Bot</h2>
        <p>Status: {bot['last']}</p>
        <p>ML Ready: {model_ready}</p>
        <p>Training Samples: {len(X_data)}</p>

        <a href="/toggle"><button>Start / Stop Bot</button></a>
    </div>

    <div class="box">
        <h3>Watchlist</h3>
        <p>{", ".join(bot['watchlist'])}</p>
    </div>

    <div class="box">
        <h3>Manual Trades</h3>

        <a href="/buy/AAPL"><button>Buy AAPL</button></a>
        <a href="/buy/TSLA"><button>Buy TSLA</button></a>
        <a href="/buy/NVDA"><button>Buy NVDA</button></a>
    </div>

    </body>
    </html>
    """

    return html

# =========================================================
# START
# =========================================================

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
