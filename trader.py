import os
import time
import threading
import numpy as np
import pandas as pd
import requests
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
# FLASK
# =========================================================

from flask import Flask

app = Flask(__name__)

# =========================================================
# ML MODEL
# =========================================================

model = LogisticRegression()
scaler = StandardScaler()

training_data = []
labels = []
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
# API
# =========================================================

def get(url):
    return requests.get(BASE_URL + url, headers=HEADERS)

def post(url, payload):
    return requests.post(BASE_URL + url, json=payload, headers=HEADERS)

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
# TRAIN MODEL
# =========================================================

def train():

    global model_ready

    if len(training_data) < 30:
        return

    X = np.array(training_data)
    y = np.array(labels)

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
# EVALUATE SYMBOL
# =========================================================

def evaluate(symbol):

    df = bars(symbol,"1Min",60)

    if df is None or len(df) < 25:
        return None

    x = features(df)

    return symbol, predict(x), x

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

# =========================================================
# LOOP
# =========================================================

def loop():

    while True:

        try:

            if not bot["running"]:
                time.sleep(5)
                continue

            picks = []

            for s in bot["watchlist"]:

                r = evaluate(s)

                if r:

                    sym, conf, x = r

                    if conf > 0.65:
                        picks.append((sym, conf, x))

            picks.sort(key=lambda x: x[1], reverse=True)

            for sym, conf, x in picks[:3]:

                buy(sym)

            train()

            time.sleep(20)

        except Exception as e:
            bot["last"] = str(e)
            time.sleep(5)

# =========================================================
# DASHBOARD (FIXED - NO LOGIN, NOT BLANK)
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

    </style>
    </head>

    <body>

    <div class="box">
        <h2>ML Trading Bot</h2>
        <p>Status: {bot['last']}</p>
        <p>ML Ready: {model_ready}</p>
        <p>Training Samples: {len(training_data)}</p>
    </div>

    <div class="box">
        <h3>Watchlist</h3>
        <p>{", ".join(bot['watchlist'])}</p>
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
