import eventlet
eventlet.monkey_patch()

import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime
from flask import Flask
from flask_socketio import SocketIO

# =========================================================
# CONFIG - HARDCODED PAPER KEYS
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", ping_timeout=60, ping_interval=15)

bot = {
    "running": True,
    "watchlist": ["AAPL", "TSLA", "NVDA", "AMD", "SPY", "QQQ", "GME"],
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "trade_amount_usd": 20.00, # Buys $20 worth of the asset (Fractional Shares)
    "last_eod_date": ""
}

activity_log = ["System Initialized... Booting Cloud Engine"]

def log_event(msg):
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    activity_log.append(full_msg)
    if len(activity_log) > 25:
        activity_log.pop(0)

# =========================================================
# SAFE ALPACA API HELPERS
# =========================================================
def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS)
        return r.json() if r.status_code == 200 else {"equity": "0.00", "buying_power": "0.00", "last_equity": "0.00"}
    except: return {"equity": "0.00", "buying_power": "0.00", "last_equity": "0.00"}

def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_open_orders():
    try:
        r = requests.get(f"{BASE_URL}/v2/orders?status=open", headers=HEADERS)
        return r.json() if r.status_code == 200 else []
    except: return []

def place_order(symbol, current_price):
    try:
        # Calculate Fractional Quantity (e.g., $20.00 / $150.00 = 0.1333 shares)
        raw_qty = bot["trade_amount_usd"] / current_price
        qty = round(raw_qty, 5) # Alpaca accepts up to 9 decimals, 5 is safe

        buy_payload = {
            "symbol": symbol,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc"
        }
        r_buy = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=buy_payload)
        
        if r_buy.status_code == 200:
            log_event(f"BOUGHT {qty} {symbol} (~${bot['trade_amount_usd']})")
            
            # Attach 2% Trailing Stop
            trail_payload = {
                "symbol": symbol,
                "qty": qty,
                "side": "sell",
                "type": "trailing_stop",
                "trail_percent": 2.0,
                "time_in_force": "gtc"
            }
            time.sleep(1) # Let order register
            r_trail = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=trail_payload)
            if r_trail.status_code == 200:
                log_event(f"ATTACHED 2% Trailing Stop to {symbol}")
        else:
            log_event(f"REJECTED {symbol}: {r_buy.json().get('message', 'Unknown Error')}")
            
    except Exception as e:
        log_event(f"ORDER CRASH: {str(e)}")

# =========================================================
# MARKET DATA & AI SIGNAL ENGINE
# =========================================================
def get_bars(symbol):
    is_crypto = "/" in symbol
    if is_crypto:
        url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params = {"symbols": symbol, "timeframe": "1Min", "limit": 20}
    else:
        url = f"{DATA_URL}/stocks/{symbol}/bars"
        params = {"timeframe": "1Min", "limit": 20, "feed": "iex"}
    
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return None
        data = r.json()
        bars = data.get("bars", {})
        if isinstance(bars, dict): bars = bars.get(symbol, [])
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except: return None

def score_symbol(symbol):
    df = get_bars(symbol)
    if df is None or len(df) < 10: return None
    current_price = float(df["c"].iloc[-1])
    past_price = float(df["c"].iloc[-10])
    percent_change = ((current_price - past_price) / past_price) * 100
    return {"symbol": symbol, "score": percent_change, "price": current_price}

# =========================================================
# TRADING ENGINE THREAD
# =========================================================
def engine():
    log_event("Alpaca Connection Established. Scanning markets...")
    while True:
        try:
            # 1. Market Clock & EOD Logic
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            # --- SWING VS DAY TRADE DECISION ENGINE ---
            if is_open and "next_close" in clock_data:
                now_t = datetime.fromisoformat(clock_data['timestamp'])
                close_t = datetime.fromisoformat(clock_data['next_close'])
                mins_to_close = (close_t - now_t).total_seconds() / 60.0
                curr_date = str(now_t.date())
                
                # If less than 15 mins to market close, evaluate holding risk
                if 0 < mins_to_close < 15 and bot["last_eod_date"] != curr_date:
                    log_event("🔔 15 MIN TO CLOSE: Evaluating Swing vs Day Trades...")
                    pos_to_eval = get_positions()
                    for p in pos_to_eval:
                        # Skip crypto for EOD logic since it trades 24/7
                        if "USD" in p['symbol']: continue 
                        
                        pl_pct = float(p.get("unrealized_intraday_plpc", 0))
                        if pl_pct < 0:
                            log_event(f"DAY TRADE CLOSE: Selling {p['symbol']} to avoid overnight gap down.")
                            requests.delete(f"{BASE_URL}/v2/positions/{p['symbol']}", headers=HEADERS)
                        else:
                            log_event(f"SWING TRADE: Holding {p['symbol']} overnight for continued gains.")
                    bot["last_eod_date"] = curr_date

            # 2. Score Assets
            active_list = bot["watchlist"] if is_open else bot["crypto_watchlist"]
            ranked = []
            for s in active_list:
                r = score_symbol(s)
                if r: ranked.append(r)

            ranked.sort(key=lambda x: x["score"], reverse=True)

            # 3. Fetch Portfolio
            acc = get_account()
            pos = get_positions()
            orders = get_open_orders()

            # 4. Execute Trades (Buy if trending > 0.1%)
            for r in ranked[:2]:
                if r["score"] > 0.1:
                    already_hold = any(p.get('symbol') == r['symbol'] for p in pos)
                    already_pending = any(o.get('symbol') == r['symbol'] for o in orders)
                    if not already_hold and not already_pending:
                        place_order(r["symbol"], r["price"])

            # 5. Broadcast to UI
            socketio.emit("update", {
                "account": acc,
                "positions": pos,
                "orders": orders,
                "ranked": ranked[:8],
                "activity": activity_log[::-1],
                "market_status": "MARKET OPEN" if is_open else "MARKET CLOSED (CRYPTO ACTIVE)"
            })

        except Exception as e:
            log_event(f"ENGINE LOOP ERROR: {str(e)}")

        time.sleep(3)

# =========================================================
# UI DASHBOARD
# =========================================================
@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Trading Terminal</title>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system, sans-serif; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:300px 1fr 350px; gap:20px; padding:20px; height: calc(100vh - 80px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; overflow:hidden; }
    .card-body { overflow-y:auto; flex-grow:1; }
    .muted { color:#9ca3af; font-size:11px; text-transform:uppercase; font-weight:800; letter-spacing:1px; margin-bottom:5px; }
    .big { font-size:32px; font-weight:bold; color:#fff; }
    table { width:100%; border-collapse:collapse; font-size:13px; text-align:left; }
    th { color:#9ca3af; padding-bottom:12px; border-bottom:1px solid var(--border); font-weight:600; }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .item { display:flex; justify-content:space-between; padding:12px 0; border-bottom:1px solid var(--border); font-size:14px; }
    .pill { background:#4b5563; color:#fff; padding:4px 10px; border-radius:20px; font-size:11px; font-weight:900; letter-spacing:0.5px;}
    .pill-pending { background:var(--orange); color:#000; padding:3px 8px; border-radius:6px; font-size:10px; font-weight:bold; margin-left:8px; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
</style>
</head>
<body>

<div class="header">
    <div>
        <span style="font-weight:900; font-size:18px; tracking:wide;">QUANTUM<span style="color:var(--green)">BOT</span></span>
        <span id="mkt" style="margin-left:20px; font-size:12px; color:#9ca3af; font-weight:bold;">INITIALIZING SERVER...</span>
    </div>
    <div class="pill" id="status">CONNECTING...</div>
</div>

<div class="grid">
    <div class="card">
        <div class="muted">Net Equity</div>
        <div style="display: flex; align-items: baseline; gap: 10px; margin-bottom: 15px;">
            <div class="big" id="equity">$0.00</div>
            <div id="equity-pct" style="font-size: 16px; font-weight: bold; color: #9ca3af;">0.00%</div>
        </div>
        
        <div class="muted">Buying Power</div>
        <div id="cash" style="font-size:20px; font-weight:bold; margin-bottom:25px; color:#9ca3af;">$0.00</div>
        <hr style="border:0; border-top:1px solid var(--border); margin-bottom:20px;">
        <div class="muted" style="margin-bottom:10px;">Live AI Momentum (% Change)</div>
        <div class="card-body" id="ranked"></div>
    </div>

    <div class="card">
        <div class="muted" style="margin-bottom:15px;">Live Holdings & Active Orders</div>
        <div class="card-body">
            <table>
                <thead>
                    <tr><th>Asset</th><th>Qty</th><th>Current</th><th>P/L</th><th>Status</th></tr>
                </thead>
                <tbody id="pos-table"></tbody>
            </table>
        </div>
    </div>

    <div class="card">
        <div class="muted" style="margin-bottom:15px;">System Execution Log</div>
        <div class="card-body" id="logs" style="font-family:'Courier New', monospace; font-size:12px; line-height:1.6; color:#a1a1aa;"></div>
    </div>
</div>

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
    const socket = io();
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    const pct = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 2 });
    
    const statusBtn = document.getElementById("status");
    socket.on('connect', () => { statusBtn.innerText = "WEB LINKED"; statusBtn.style.background = "#f59e0b"; });
    socket.on('disconnect', () => { statusBtn.innerText = "DISCONNECTED"; statusBtn.style.background = "#ef4444"; });

    socket.on("update", (d) => {
        statusBtn.innerText = "LIVE SYNC";
        statusBtn.style.background = "#22c55e";

        const currentEq = parseFloat(d.account.equity || 0);
        const lastEq = parseFloat(d.account.last_equity || currentEq);
        let pctChange = lastEq > 0 ? ((currentEq - lastEq) / lastEq) * 100 : 0;

        document.getElementById("equity").innerText = f.format(currentEq);
        document.getElementById("cash").innerText = f.format(d.account.cash || 0);
        document.getElementById("mkt").innerText = d.market_status;

        const eqPctEl = document.getElementById("equity-pct");
        eqPctEl.innerText = (pctChange > 0 ? "+" : "") + pctChange.toFixed(2) + "%";
        eqPctEl.style.color = pctChange === 0 ? "#9ca3af" : (pctChange > 0 ? "var(--green)" : "var(--red)");

        let tableHTML = "";

        // 1. Render PENDING Orders first
        if (d.orders && d.orders.length > 0) {
            d.orders.forEach(o => {
                tableHTML += `
                <tr style="background: rgba(245, 158, 11, 0.1);">
                    <td><b style="color:#fff;">${o.symbol}</b> <span class="pill-pending">PENDING</span></td>
                    <td>${o.qty || '-'}</td>
                    <td style="color:#9ca3af;">${o.order_type.toUpperCase()}</td>
                    <td style="color:#9ca3af;">--</td>
                    <td style="color:#f59e0b; font-size:11px;">Waiting Fill</td>
                </tr>`;
            });
        }

        // 2. Render ACTIVE Positions
        if (d.positions && d.positions.length > 0) {
            d.positions.forEach(p => {
                const isProfit = p.unrealized_intraday_pl >= 0;
                const plColor = isProfit ? '#22c55e' : '#ef4444';
                tableHTML += `
                <tr>
                    <td><b style="color:#fff;">${p.symbol}</b></td>
                    <td>${p.qty}</td>
                    <td>${f.format(p.current_price)}</td>
                    <td style="color:${plColor}; font-weight:bold;">${f.format(p.unrealized_intraday_pl)} <br><span style="font-size:11px;">(${pct.format(p.unrealized_intraday_plpc)})</span></td>
                    <td style="color:#22c55e; font-size:11px;">Active</td>
                </tr>`;
            });
        }

        if (tableHTML === "") {
            tableHTML = "<tr><td colspan='5' style='text-align:center; color:#6b7280; padding-top:20px;'>No active or pending positions.</td></tr>";
        }
        
        document.getElementById("pos-table").innerHTML = tableHTML;

        document.getElementById("ranked").innerHTML = d.ranked.map(r => `
            <div class="item">
                <span style="color:#fff;"><b>${r.symbol}</b> <span style="font-size:11px; color:#6b7280; margin-left:5px;">${f.format(r.price)}</span></span>
                <span style="color:${r.score >= 0 ? '#22c55e' : '#ef4444'}; font-weight:bold;">${r.score > 0 ? '+' : ''}${r.score.toFixed(2)}%</span>
            </div>`).join("");

        document.getElementById("logs").innerHTML = d.activity.map(a => `
            <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1f2937;">
                ${a.includes('ERROR') || a.includes('REJECTED') || a.includes('CRASH') ? `<span style="color:#ef4444">${a}</span>` : 
                  a.includes('BOUGHT') ? `<span style="color:#22c55e">${a}</span>` : 
                  a.includes('🔔') || a.includes('DAY TRADE') ? `<span style="color:#f59e0b">${a}</span>` : a}
            </div>
        `).join("");
    });
</script>

</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=PORT)
