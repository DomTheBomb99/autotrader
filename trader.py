import os
import time
import threading
import numpy as np
import pandas as pd
import requests
from flask import Flask, request, redirect, session
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

USERNAME = "admin"
PASSWORD = "trader123"

# =========================================================
# APP
# =========================================================

app = Flask(__name__)
app.secret_key = "ml_trader"

# =========================================================
# ML MODEL STORAGE
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
    "watchlist": ["AAPL","TSLA","NVDA","AMD","SPY","QQQ"]
}

# =========================================================
# API HELPERS
# =========================================================

def get(url):
    return requests.get(BASE_URL + url, headers=HEADERS)

def post(url, payload):
    return requests.post(BASE_URL + url, json=payload, headers=HEADERS)

def positions():
    r = get("/v2/positions")
    return r.json() if r.status_code == 200 else []

def account():
    r = get("/v2/account")
    return r.json() if r.status_code == 200 else {}

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
# FEATURE ENGINE (THIS IS WHAT ML LEARNS FROM)
# =========================================================

def build_features(df):

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

def train_model():

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
# ML PREDICTION
# =========================================================

def predict(features):

    if not model_ready:
        return 0.5

    X = scaler.transform([features])

    return model.predict_proba(X)[0][1]

# =========================================================
# TRADE MEMORY (SELF LEARNING)
# =========================================================

def record_trade(features, profit):

    training_data.append(features)

    labels.append(1 if profit > 0 else 0)

# =========================================================
# STRATEGY ENGINE (NOW ML CONTROLLED)
# =========================================================

def evaluate(symbol):

    df = bars(symbol, "1Min", 60)

    if df is None or len(df) < 25:
        return None

    features = build_features(df)

    confidence = predict(features)

    return symbol, confidence, features

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

            candidates = []

            for s in bot["watchlist"]:

                result = evaluate(s)

                if result:

                    sym, conf, features = result

                    if conf > 0.65:
                        candidates.append((sym, conf, features))

            candidates.sort(key=lambda x: x[1], reverse=True)

            for sym, conf, features in candidates[:3]:

                buy(sym)

            train_model()

            time.sleep(20)

        except Exception as e:
            bot["last"] = str(e)
            time.sleep(5)

# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        if request.form["username"] == USERNAME and request.form["password"] == PASSWORD:
            session["auth"] = True
            return redirect("/")

    return "<h2>Login</h2>"

# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def dash():

    if not session.get("auth"):
        return redirect("/login")

    return f"""
    <body style='background:#0b0f1a;color:white;font-family:Arial'>
    <h2>ML Trading Bot</h2>

    <p>Status: {bot['last']}</p>

    <p>ML Ready: {model_ready}</p>

    <p>Training Samples: {len(training_data)}</p>

    </body>
    """

# =========================================================
# START
# =========================================================

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
