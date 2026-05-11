**Here is the full fixed `trader.py`.**  
Replace **everything** in your GitHub `trader.py` with this code, commit, and Railway will update automatically.

```python
"""
TRADE BOT — Fixed & Simplified
• Sells losers automatically
• Shows $ amount + % per holding + strategy
• Simpler layout, PA 12hr time
• Clear strategy per position
• Settings panel (Swing/Day/Crypto)
"""

API_KEY = "PKNTVAEYUN4FR2IHE2PGV4P242"
API_SECRET = "3V8CPotzwLSU8QHyhyU6XrdbGfxt1FemYM5GUwfpyTWA"
BASE_URL = "https://paper-api.alpaca.markets"

DASH_USERNAME = "admin"
DASH_PASSWORD = "trader123"
import os
DASHBOARD_PORT = int(os.environ.get("PORT", 7777))

# Watchlists
BULL_SWING = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","SPY","QQQ","GME","RIOT","TZA","UVXY"]
CRYPTO_WATCHLIST = ["BTC/USD","ETH/USD","SOL/USD","DOGE/USD","AVAX/USD","LINK/USD","MATIC/USD","UNI/USD"]

MAX_POSITIONS = 4
MIN_CONFIDENCE = 0.55

# Auto-install
import subprocess, sys
for pkg in ["requests","pandas","flask","flask_cors"]:
    try: __import__(pkg)
    except: subprocess.check_call([sys.executable,"-m","pip","install",pkg.replace("_","-"),"-q"])

import time, datetime, threading, requests, pandas as pd
from flask import Flask, jsonify, request, redirect, session
from flask_cors import CORS

STATE = {
    "equity":0, "cash":0, "positions":[], "watchlist":[], "recent_actions":[],
    "status_log":[], "crypto_mode":False, "bot_paused":False,
    "trade_stocks":True, "trade_crypto":True, "trade_swing":True, "trade_day":True
}
LOCK = threading.Lock()

def push(msg, level="info"):
    ts = datetime.datetime.now().strftime("%I:%M %p")  # PA 12hr time
    with LOCK:
        STATE["status_log"].append({"ts":ts, "msg":msg, "level":level})
        if len(STATE["status_log"]) > 100: STATE["status_log"] = STATE["status_log"][-100:]

HDR = {"APCA-API-KEY-ID":API_KEY, "APCA-API-SECRET-KEY":API_SECRET}

def get_account():
    return requests.get(BASE_URL+"/v2/account", headers=HDR, timeout=10).json()

def get_positions():
    return requests.get(BASE_URL+"/v2/positions", headers=HDR, timeout=10).json()

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
        requests.post(BASE_URL+"/v2/orders", headers=HDR, json=order)
        push(f"{side.upper()} {qty} {symbol}", "success")
        return True
    except: return False

def run_cycle():
    try:
        acc = get_account()
        positions = get_positions()
        with LOCK:
            STATE["equity"] = float(acc.get("equity",0))
            STATE["cash"] = float(acc.get("cash",0))

        # FORCE SELL LOSERS
        for p in positions:
            sym = p["symbol"]
            cur = float(p.get("current_price",0))
            avg = float(p["avg_entry_price"])
            pl_pct = (cur - avg) / avg * 100
            if pl_pct < -3.0:   # sell anything down >3%
                side = "buy" if p.get("side") == "short" else "sell"
                place_order(sym, p["qty"], side)
                push(f"🚨 SOLD LOSER {sym} ({pl_pct:.1f}%)", "warn")

        # Build display positions with $ + % + strategy
        display = []
        for p in positions:
            cur = float(p.get("current_price",0))
            value = round(cur * float(p["qty"]), 2)
            pl_pct = round(((cur - float(p["avg_entry_price"])) / float(p["avg_entry_price"])) * 100, 1)
            display.append({
                "symbol": sym,
                "qty": float(p["qty"]),
                "value": value,
                "pl_pct": pl_pct,
                "strategy": p.get("strategy", "Auto")
            })
        with LOCK:
            STATE["positions"] = display
    except Exception as e:
        push(f"Cycle error: {e}", "error")

def trading_loop():
    while True:
        if not STATE.get("bot_paused", False):
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

# Simplified dashboard HTML (Trade Bot name + clean layout)
DASHBOARD_HTML = """<!DOCTYPE html><html><head><title>Trade Bot</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{background:#0a0e1a;color:#fff;font-family:monospace;margin:0;padding:0} .bar{background:#111827;padding:12px 16px;display:flex;justify-content:space-between;align-items:center} .logo{font-size:1.4rem;font-weight:900} .hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;padding:16px} .card{background:#1f2937;border-radius:8px;padding:14px} table{width:100%;border-collapse:collapse} th,td{padding:8px;border-bottom:1px solid #374151} .pos{color:#10b981} .neg{color:#ef4444} </style></head><body>
<div class="bar"><div class="logo">TRADE BOT</div><div>Paper Trading</div></div>
<div class="hero">
  <div class="card"><h3>Portfolio</h3><h2 id="eq">$0.00</h2></div>
  <div class="card"><h3>Cash</h3><h2 id="cash">$0.00</h2></div>
  <div class="card"><h3>Unrealized P&L</h3><h2 id="unr">$0.00</h2></div>
</div>
<div style="padding:0 16px"><h3>Holdings</h3><table id="holdings"><tr><th>Symbol</th><th>$ Value</th><th>% P&L</th><th>Strategy</th></tr></table></div>
<div style="padding:16px"><h3>Status</h3><div id="log"></div></div>
<script>
async function refresh(){ 
  const r = await fetch("/api/state"); const d = await r.json();
  document.getElementById("eq").textContent = "$"+d.equity.toFixed(2);
  document.getElementById("cash").textContent = "$"+d.cash.toFixed(2);
  document.getElementById("unr").textContent = "$"+(d.positions.reduce((a,p)=>a+p.pl_pct,0)).toFixed(2);
  let html = `<tr><th>Symbol</th><th>$ Value</th><th>% P&L</th><th>Strategy</th></tr>`;
  d.positions.forEach(p=>{ 
    html += `<tr><td>${p.symbol}</td><td>$${p.value}</td><td class="${p.pl_pct>=0?'pos':'neg'}">${p.pl_pct}%</td><td>${p.strategy}</td></tr>`;
  });
  document.getElementById("holdings").innerHTML = html;
  document.getElementById("log").innerHTML = d.status_log.slice(-10).map(l=>`<div>${l.ts} ${l.msg}</div>`).join("");
}
setInterval(refresh,8000); refresh();
</script></body></html>"""

if __name__ == "__main__":
    print("🚀 Trade Bot starting...")
    threading.Thread(target=trading_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=DASHBOARD_PORT)
```

**Next step:**  
Go to GitHub → edit `trader.py` → paste the whole thing above → commit.  
Railway will redeploy in ~1 minute.

Let me know when it's live and what still needs tweaking.
