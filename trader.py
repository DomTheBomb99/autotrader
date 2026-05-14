import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime
from flask import Flask
from flask_socketio import SocketIO

# =========================================================
# CONFIG - PAPER KEYS
# =========================================================
API_KEY = "PKRY2XRZW4K4TWX4NYSEROPQEK"
API_SECRET = "8pySz6LGdhjNr8tHfbgMcCgF2cK5Qd3afmy4CbwmZznQ"

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

PORT = int(os.environ.get("PORT", 7777))

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", ping_timeout=60, ping_interval=15)

UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMD", "META", "AMZN", "MSFT", "GOOGL", "NFLX", 
    "COIN", "MARA", "RIOT", "MSTR", "PLTR", "SNOW", "UBER", "ROKU", "SQ", 
    "PYPL", "HOOD", "GME", "AMC", "BA", "DIS", "LCID", "RIVN", "SOFI", "DKNG"
]

bot = {
    "running": True,
    "watchlist": [], 
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
    "base_trade_usd": 20.00,
    "last_scan_date": "",
    "risk_pct": 2.0,    
    "reward_pct": 6.0,
    "equity_history": [] # For the upcoming chart
}

# Tracking high-water marks for trailing stops
trailing_stops = {} 

activity_log = ["System Initialized... Aggressive Logic Engaged"]

def log_event(msg):
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    activity_log.append(full_msg)
    if len(activity_log) > 25:
        activity_log.pop(0)

def run_daily_scanner():
    try:
        url = f"{DATA_URL}/stocks/snapshots"
        params = {"symbols": ",".join(UNIVERSE), "feed": "iex"}
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return False
        data = r.json()
        movers = []
        for symbol, snap in data.items():
            try:
                prev_close = snap["prevDailyBar"]["c"]
                current_price = snap["latestTrade"]["p"]
                if prev_close > 0:
                    pct_change = ((current_price - prev_close) / prev_close) * 100
                    movers.append({"symbol": symbol, "change": pct_change})
            except: continue
        movers.sort(key=lambda x: x["change"], reverse=True)
        bot["watchlist"] = [m["symbol"] for m in movers[:5]]
        log_event(f"🎯 NEW TARGETS LOCKED")
        return True
    except: return False

def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
        return r.json() if r.status_code == 200 else {"equity": "0.00", "cash": "0.00"}
    except: return {"equity": "0.00", "cash": "0.00"}

def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
        return r.json() if r.status_code == 200 else []
    except: return []

def place_order(symbol, current_price, strength_multiplier):
    try:
        # Dynamic Position Sizing: Scale up based on signal strength
        trade_val = bot["base_trade_usd"] * strength_multiplier
        qty = round(trade_val / current_price, 5)
        tif = "gtc" if "/" in symbol else "day"
        
        payload = {"symbol": symbol, "qty": qty, "side": "buy", "type": "market", "time_in_force": tif}
        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload)
        if r.status_code == 200:
            log_event(f"🔥 AGGRESSIVE BUY: {symbol} (${trade_val:.2f})")
        else:
            log_event(f"REJECTED: {r.json().get('message')}")
    except Exception as e:
        log_event(f"ORDER ERROR: {str(e)}")

def get_bars(symbol):
    is_crypto = "/" in symbol
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"symbols": symbol, "timeframe": "1Min", "limit": 31} if is_crypto else {"timeframe": "1Min", "limit": 31, "feed": "iex"}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return None
        data = r.json()
        bars = data.get("bars", {}).get(symbol, []) if is_crypto else data.get("bars", [])
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except: return None

def score_symbol(symbol):
    try:
        df = get_bars(symbol)
        if df is None or len(df) < 20: return {"symbol": symbol, "score": 0.0, "price": 0.0, "vol_spike": False}
        
        current_price = float(df["c"].iloc[-1])
        past_price = float(df["c"].iloc[0])
        
        # Volume Spike Logic
        avg_vol = df["v"].iloc[:-1].mean()
        curr_vol = df["v"].iloc[-1]
        vol_spike = curr_vol > (avg_vol * 1.5) # 50% increase in volume
        
        pct_change = ((current_price - past_price) / past_price) * 100
        return {"symbol": symbol, "score": pct_change, "price": current_price, "vol_spike": vol_spike}
    except:
        return {"symbol": symbol, "score": 0.0, "price": 0.0, "vol_spike": False}

def engine():
    while True:
        try:
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            acc = get_account()
            pos = get_positions()
            cash = float(acc.get("cash", 0))

            # --- AI RISK MANAGER (Trailing Stop Logic) ---
            for p in pos:
                sym = p['symbol']
                curr_plpc = float(p.get("unrealized_plpc", 0)) * 100
                
                # Update the "High Water Mark" for trailing
                if sym not in trailing_stops or curr_plpc > trailing_stops[sym]:
                    trailing_stops[sym] = curr_plpc
                
                # If price drops 2% from the highest point it reached, SELL
                if (trailing_stops[sym] - curr_plpc) >= bot["risk_pct"]:
                    log_event(f"🛡️ TRAILING STOP: {sym} closed at {curr_plpc:.2f}%")
                    requests.delete(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS)
                    if sym in trailing_stops: del trailing_stops[sym]
                
                # Take profit at +6% regardless
                elif curr_plpc >= bot["reward_pct"]:
                    log_event(f"🎯 TARGET HIT: {sym} (+{curr_plpc:.2f}%)")
                    requests.delete(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS)
                    if sym in trailing_stops: del trailing_stops[sym]

            # --- BUYING LOGIC with Volume Confirmation ---
            active_list = bot["watchlist"] + bot["crypto_watchlist"]
            ranked = [score_symbol(s) for s in active_list]
            ranked = [r for r in ranked if r is not None]
            ranked.sort(key=lambda x: x["score"], reverse=True)

            for r in ranked[:2]:
                # Require BOTH price momentum AND volume spike
                if r["score"] > 0.15 and r["vol_spike"]:
                    if not any(p.get('symbol') == r['symbol'] for p in pos) and cash >= bot["base_trade_usd"]:
                        # Strength Multiplier: Higher score = Higher bet
                        multiplier = 1.0 if r["score"] < 0.30 else 1.5
                        place_order(r["symbol"], r["price"], multiplier)

            socketio.emit("update", {
                "account": acc, "positions": pos, "ranked": ranked[:10],
                "activity": activity_log[::-1], "market_status": "OPEN" if is_open else "CLOSED",
                "next_event": clock_data.get('next_close', '') if is_open else clock_data.get('next_open', '')
            })
        except Exception as e:
            log_event(f"ENGINE ERROR: {str(e)}")
        time.sleep(3)

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuantumBot Pro</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:sans-serif; }
    .header { display:flex; justify-content:space-between; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:300px 1fr 350px; gap:20px; padding:20px; }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; }
    .big { font-size:32px; font-weight:bold; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .status-badge { font-size:10px; padding:2px 6px; border-radius:4px; font-weight:bold; margin-top:4px; display:inline-block; }
    @media (max-width: 1024px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="header">
    <div style="font-weight:900;">QUANTUM<span style="color:var(--green)">PRO</span></div>
    <div id="status" style="font-size:11px; font-weight:bold; color:var(--green);">LIVE SYNC</div>
</div>

<div class="grid">
    <div class="card">
        <div class="big" id="equity">$0.00</div>
        <canvas id="equityChart" style="height:100px; margin:15px 0;"></canvas>
        <div style="display:flex; justify-content:space-between; font-size:12px; margin-top:10px;">
            <div id="winrate">Wins: 0</div>
            <div id="profit">Profit: $0.00</div>
        </div>
        <hr style="border:0; border-top:1px solid var(--border); margin:20px 0;">
        <div id="ranked"></div>
    </div>
    
    <div class="card">
        <div style="margin-bottom:15px; font-weight:bold;">Live Positions</div>
        <div id="pos-container">
            <table><tbody id="pos-table"></tbody></table>
        </div>
    </div>

    <div class="card">
        <div style="margin-bottom:15px; font-weight:bold;">Execution Log</div>
        <div id="logs" style="font-family:monospace; font-size:11px;"></div>
    </div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io({ transports: ["websocket", "polling"] });
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    
    // Scorecard tracking
    let wins = 0;
    let totalProfit = 0;

    socket.on("update", (d) => {
        document.getElementById("equity").innerText = f.format(d.account.equity);
        
        // Render Positions
        document.getElementById("pos-table").innerHTML = d.positions.map(p => {
            const pl = parseFloat(p.unrealized_intraday_pl);
            return `<tr>
                <td><b>${p.symbol}</b><br><span style="font-size:10px;">${p.qty} shs</span></td>
                <td style="color:${pl >= 0 ? 'var(--green)' : 'var(--red)'}">${f.format(pl)}</td>
                <td><span class="status-badge" style="background:rgba(59,130,246,0.2); color:var(--blue);">TRAILING STOP ON</span></td>
            </tr>`;
        }).join("");

        // Render Targets with Vol Spike Badge
        document.getElementById("ranked").innerHTML = d.ranked.map(r => `
            <div style="margin-bottom:12px;">
                <div style="display:flex; justify-content:space-between;"><b>${r.symbol}</b> <span>${r.score.toFixed(2)}%</span></div>
                <div class="status-badge" style="background:${r.vol_spike ? 'rgba(34,197,94,0.2)' : 'rgba(255,255,255,0.05)'}; color:${r.vol_spike ? 'var(--green)' : '#666'}">
                    ${r.vol_spike ? '⚡ VOLUME SPIKE' : 'SCANNING VOL'}
                </div>
            </div>
        `).join("");

        // Render Logs & Check for wins
        document.getElementById("logs").innerHTML = d.activity.map(a => {
            if(a.includes("TARGET HIT") && !a.dataset_seen) { wins++; a.dataset_seen = true; }
            return `<div style="padding:4px 0; border-bottom:1px solid #1f2937;">${a}</div>`;
        }).join("");
        
        document.getElementById("winrate").innerText = `Wins: ${wins}`;
    });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
