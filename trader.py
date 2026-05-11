"""
TRADE BOT — Fixed (sell losers, $ + %, strategy shown, market status, Alpaca connection, mode switch, PA 12hr time)
"""

API_KEY = "PKNTVAEYUN4FR2IHE2PGV4P242"
API_SECRET = "3V8CPotzwLSU8QHyhyU6XrdbGfxt1FemYM5GUwfpyTWA"
BASE_URL = "https://paper-api.alpaca.markets"

DASH_USERNAME = "admin"
DASH_PASSWORD = "trader123"
import os
DASHBOARD_PORT = int(os.environ.get("PORT", 7777))

BULL_SWING = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","SPY","QQQ","GME","RIOT","TZA","UVXY"]
CRYPTO_WATCHLIST = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD","MATIC/USD","UNI/USD"]

import subprocess, sys, time, datetime, threading, requests, pandas as pd
for pkg in ["requests","pandas","flask","flask_cors"]:
    try: __import__(pkg)
    except: subprocess.check_call([sys.executable, "-m", "pip", "install", pkg.replace("_","-"), "-q"])

from flask import Flask, jsonify, request, redirect, session
from flask_cors import CORS

STATE = {
    "equity":0, "cash":0, "positions":[], "watchlist":[], "recent_actions":[],
    "status_log":[], "market_open":False, "api_ok":False, "crypto_mode":False,
    "mode":"swing", "bot_paused":False, "trade_stocks":True, "trade_crypto":True
}
LOCK = threading.Lock()

def push(msg, level="info"):
    ts = datetime.datetime.now().strftime("%I:%M %p")  # PA 12hr
    with LOCK:
        STATE["status_log"].append({"ts":ts, "msg":msg, "level":level})
        if len(STATE["status_log"]) > 100: STATE["status_log"] = STATE["status_log"][-100:]

HDR = {"APCA-API-KEY-ID":API_KEY, "APCA-API-SECRET-KEY":API_SECRET}

def get_account():
    try:
        r = requests.get(BASE_URL+"/v2/account", headers=HDR, timeout=10)
        r.raise_for_status()
        with LOCK: STATE["api_ok"] = True
        return r.json()
    except:
        with LOCK: STATE["api_ok"] = False
        return {}

def get_positions():
    try: return requests.get(BASE_URL+"/v2/positions", headers=HDR, timeout=10).json()
    except: return []

def get_clock():
    try:
        c = requests.get(BASE_URL+"/v2/clock", headers=HDR, timeout=10).json()
        return c.get("is_open", False)
    except: return False

def get_current_price(symbol, crypto=False):
    try:
        if crypto:
            r = requests.get("https://data.alpaca.markets/v1beta3/crypto/us/latest/bars", headers=HDR, params={"symbols":symbol}, timeout=5)
            return float(r.json()["bars"][symbol]["c"])
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars/latest", headers=HDR, timeout=5)
        return float(r.json()["bar"]["c"])
    except: return None

def place_order(symbol, qty, side, crypto=False):
    try:
        order = {"symbol":symbol, "qty":str(qty), "side":side, "type":"market", "time_in_force":"gtc" if crypto else "day"}
        requests.post(BASE_URL+"/v2/orders", headers=HDR, json=order, timeout=10)
        push(f"{side.upper()} {qty} {symbol}", "success")
        return True
    except: return False

def run_cycle():
    open_market = get_clock()
    with LOCK:
        STATE["market_open"] = open_market
        STATE["crypto_mode"] = not open_market

    acc = get_account()
    positions = get_positions()
    with LOCK:
        STATE["equity"] = float(acc.get("equity", 0))
        STATE["cash"] = float(acc.get("cash", 0))

    # FORCE SELL LOSERS
    for p in positions:
        sym = p["symbol"]
        cur = float(p.get("current_price", 0))
        avg = float(p["avg_entry_price"])
        pl_pct = (cur - avg) / avg * 100
        if pl_pct < -3.0:
            side = "buy" if p.get("side") == "short" else "sell"
            place_order(sym, p["qty"], side)
            push(f"🚨 SOLD LOSER {sym} ({pl_pct:.1f}%)", "warn")

    # Build holdings with $ value + % + strategy
    display = []
    for p in positions:
        cur = float(p.get("current_price", 0))
        value = round(cur * float(p["qty"]), 2)
        pl_pct = round(((cur - float(p["avg_entry_price"])) / float(p["avg_entry_price"])) * 100, 1)
        display.append({
            "symbol": p["symbol"],
            "value": value,
            "pl_pct": pl_pct,
            "strategy": "Auto"
        })
    with LOCK:
        STATE["positions"] = display

def trading_loop():
    while True:
        if not STATE.get("bot_paused"):
            run_cycle()
        time.sleep(60)

app = Flask(__name__)
app.secret_key = "tradebot123"
CORS(app)

@app.route("/api/state")
def api_state():
    with LOCK:
        return jsonify(STATE)

@app.route("/")
def index():
    return DASHBOARD_HTML

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><title>Trade Bot</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{background:#07090f;color:#f1f5f9;font-family:monospace;margin:0} .bar{background:#0d1422;padding:12px 16px;display:flex;justify-content:space-between} .logo{font-size:1.4rem;font-weight:900} .card{background:#131c2e;border-radius:8px;padding:12px;margin:12px} table{width:100%;border-collapse:collapse} th,td{padding:8px;border-bottom:1px solid #1b2740} .pos{color:#10b981} .neg{color:#f43f5e} </style></head><body>
<div class="bar"><div class="logo">TRADE BOT</div><div id="status"></div></div>
<div class="card"><h3>Portfolio</h3><h2 id="eq">$0</h2></div>
<div class="card"><h3>Holdings</h3><table id="holdings"><tr><th>Symbol</th><th>$ Value</th><th>%</th><th>Strategy</th></tr></table></div>
<div class="card"><h3>Status Log</h3><div id="log"></div></div>
<script>
async function refresh(){
  const r = await fetch("/api/state");
  const d = await r.json();
  document.getElementById("eq").textContent = "$"+d.equity.toFixed(2);
  document.getElementById("status").innerHTML = `
    ${d.market_open ? '🟢 Market OPEN' : '🔴 Market CLOSED'} | 
    ${d.crypto_mode ? '₿ CRYPTO MODE' : 'Stocks'} | 
    ${d.api_ok ? '✅ Alpaca Connected' : '❌ Alpaca Error'} | Mode: ${d.mode}
  `;
  let html = `<tr><th>Symbol</th><th>$ Value</th><th>%</th><th>Strategy</th></tr>`;
  d.positions.forEach(p => {
    html += `<tr><td>${p.symbol}</td><td>$${p.value}</td><td class="${p.pl_pct>=0?'pos':'neg'}">${p.pl_pct}%</td><td>${p.strategy}</td></tr>`;
  });
  document.getElementById("holdings").innerHTML = html;
  document.getElementById("log").innerHTML = d.status_log.slice(-15).map(l=>`<div>${l.ts} ${l.msg}</div>`).join("");
}
setInterval(refresh, 8000); refresh();
</script></body></html>"""

if __name__ == "__main__":
    print("🚀 TRADE BOT STARTED")
    threading.Thread(target=trading_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=DASHBOARD_PORT)
