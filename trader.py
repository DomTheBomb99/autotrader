import os
import time
import threading
import requests
import math
import traceback
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, jsonify

# =========================================================
# CONFIG - HARDCODED KEYS
# =========================================================
API_KEY = "PKFLSLLJIOI2P6BVOCOUOC37MS"
API_SECRET = "2pNzQVEBscePX1zMBgBpjXDhCdSmmQWyX91Ps4JcDEvg"

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

PORT = int(os.environ.get("PORT", 10000))
app = Flask(__name__)

UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMD", "META", "AMZN", "MSFT", "GOOGL", "NFLX", 
    "COIN", "MARA", "RIOT", "MSTR", "PLTR", "SNOW", "UBER", "ROKU", "SQ", 
    "PYPL", "HOOD", "GME", "AMC", "BA", "DIS", "LCID", "RIVN", "SOFI", "DKNG"
]

bot = {
    "running": True,
    "base_trade_usd": 25.00,
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD"]
}

activity_log = ["TradeBot Engine v7.0 Online... Safe Mode Engaged."]

global_state = {
    "account": {"equity": "0.00", "cash": "0.00"},
    "positions": [],
    "ranked": [],
    "activity": activity_log[::-1],
    "bot_stats": {"wins": 0, "profit": 0.0, "exposure": "0%", "ai_state": "SWINGING", "volatility": "NORMAL"},
    "market_status": "WAITING...",
    "is_open": False,
    "next_event": "",
    "market_regime": "SCANNING..."
}

def log_event(msg):
    timestamp = time.strftime('%H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    activity_log.append(full_msg)
    if len(activity_log) > 30:
        activity_log.pop(0)
    global_state["activity"] = activity_log[::-1]

def sanitize_data(obj):
    if isinstance(obj, dict): return {k: sanitize_data(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [sanitize_data(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj): return 0.0
        return obj
    return obj

def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=5)
        return r.json() if r.status_code == 200 else {"equity": "0.00", "cash": "0.00"}
    except: return {"equity": "0.00", "cash": "0.00"}

def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=5)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_daily_bars(symbol, limit=100):
    is_crypto = "/" in symbol
    url = "https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/bars"
    
    start_date = (datetime.utcnow() - timedelta(days=150)).strftime('%Y-%m-%dT%H:%M:%SZ')
    params = {"symbols": symbol, "timeframe": "1Day", "limit": limit, "start": start_date}
    if not is_crypto: params["feed"] = "iex"
        
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code != 200: return None, f"Code {r.status_code}: {r.text}"
        data = r.json()
        bars = data.get("bars", {}).get(symbol, [])
        if not bars: return None, "Alpaca returned empty data array."
        return pd.DataFrame(bars), "OK"
    except Exception as e: return None, str(e)

def analyze_swing_symbol(symbol, regime):
    try:
        df, error_msg = get_daily_bars(symbol, 100)
        
        if df is None or len(df) < 20: 
            return {
                "symbol": symbol, "confidence": 0, "reasons": [], 
                "counter_reasons": [f"API ERROR: {error_msg}"], 
                "price": 0.0, "multiplier": 0.0, "risk": "HIGH"
            }
        
        df['8_EMA'] = df['c'].ewm(span=8, adjust=False).mean()
        df['21_EMA'] = df['c'].ewm(span=21, adjust=False).mean()
        df['50_SMA'] = df['c'].rolling(window=50).mean()
        df.fillna(0.0, inplace=True)
        
        price_now = float(df["c"].iloc[-1])
        ema8 = float(df["8_EMA"].iloc[-1])
        ema21 = float(df["21_EMA"].iloc[-1])
        sma50 = float(df["50_SMA"].iloc[-1])
        
        confidence = 50 
        reasons = []
        counter_reasons = []

        if price_now > sma50: confidence += 20; reasons.append("Price > 50 SMA")
        else: confidence -= 20; counter_reasons.append("Price < 50 SMA")

        if ema8 > ema21: confidence += 20; reasons.append("8 EMA > 21 EMA")
        else: confidence -= 15; counter_reasons.append("Waiting for EMA Cross")

        if price_now > ema8: confidence += 10; reasons.append("Momentum is Positive")
        
        if regime == "BULLISH": confidence += 15; reasons.append("Bullish Market")
        elif regime == "BEARISH": confidence -= 25; counter_reasons.append("Bearish Headwind")

        confidence = max(0, min(100, confidence))
        multiplier = 1.5 if confidence >= 80 else (1.0 if confidence >= 65 else 0.0)

        return {
            "symbol": symbol, "confidence": confidence, "reasons": reasons, 
            "counter_reasons": counter_reasons, "price": float(price_now), 
            "multiplier": multiplier, "risk": "LOW" if price_now > ema21 else "HIGH"
        }
    except Exception as e: 
        return {"symbol": symbol, "confidence": 0, "reasons": [], "counter_reasons": [f"Crash: {str(e)}"], "price": 0.0, "multiplier": 0.0, "risk": "HIGH"}

def engine():
    global global_state
    time.sleep(3) 
    while True:
        try:
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS, timeout=5)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            acc = get_account()
            pos = get_positions()
            cash = float(acc.get("cash") or 0)
            equity = float(acc.get("equity") or 0)
            
            spy_df, spy_err = get_daily_bars("SPY", 20)
            regime = "API ERROR"
            if spy_df is not None and len(spy_df) > 10:
                spy_c = float(spy_df['c'].iloc[-1])
                spy_ema = float(spy_df['c'].ewm(span=10, adjust=False).mean().iloc[-1])
                regime = "BULLISH" if spy_c > spy_ema else "BEARISH"

            active_list = UNIVERSE + bot["crypto_watchlist"]
            ranked_results = []
            
            for s in active_list:
                res = analyze_swing_symbol(s, regime)
                if res: ranked_results.append(res)
                
            ranked_results.sort(key=lambda x: x["confidence"], reverse=True)
            log_event(f"Radar Swept {len(ranked_results)} targets. Regime: {regime}")

            for r in ranked[:2]:
                if r["multiplier"] > 0 and not any(p.get('symbol') == r['symbol'] for p in pos):
                    if cash >= (bot.get("base_trade_usd") * r["multiplier"]):
                        val = bot.get("base_trade_usd") * r["multiplier"]
                        qty = round(val / r["price"], 5)
                        payload = {"symbol": r['symbol'], "qty": qty, "side": "buy", "type": "market", "time_in_force": "gtc"}
                        requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=5)
                        log_event(f"BUY: {r['symbol']} (${val:.2f})")

            invested = sum(float(p.get("market_value") or 0) for p in pos if p.get('symbol') != "USD")
            exposure = int((invested / equity) * 100) if equity > 0 else 0

            global_state["account"] = acc
            global_state["positions"] = pos
            global_state["ranked"] = ranked_results[:8]
            global_state["bot_stats"]["exposure"] = str(exposure) + "%"
            global_state["bot_stats"]["ai_state"] = "SWINGING"
            global_state["market_status"] = "MARKET OPEN" if is_open else "MARKET CLOSED"
            global_state["market_regime"] = regime

        except Exception as e:
            log_event(f"ENGINE ERROR: {str(e)}")
        
        time.sleep(60)

threading.Thread(target=engine, daemon=True).start()

@app.route("/api/data")
def api_data(): 
    try:
        return jsonify(sanitize_data(global_state))
    except Exception as e:
        return jsonify({"error": "Failed to generate JSON", "details": str(e)}), 500

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradeBot Terminal</title>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --yellow: #eab308; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:sans-serif; overflow-x:hidden; }
    .top-status-bar { display:flex; justify-content:flex-end; gap:20px; padding:8px 25px; background:#000; border-bottom:1px solid var(--border); font-size:10px; font-weight:bold; color:#a1a1aa; letter-spacing:1px; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    .grid { display:grid; grid-template-columns:330px 1fr 340px; gap:20px; padding:20px; height: calc(100vh - 110px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; }
    .big { font-size:32px; font-weight:bold; color:#fff; }
    .muted { color:#9ca3af; font-size:11px; text-transform:uppercase; font-weight:800; letter-spacing:1px; margin-bottom:5px; }
    table { width:100%; border-collapse:collapse; font-size:13px; text-align:left; }
    th { color:#9ca3af; padding-bottom:12px; border-bottom:1px solid var(--border); font-size:11px; text-transform:uppercase; }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .pill { background:#4b5563; color:#fff; padding:4px 10px; border-radius:20px; font-size:11px; font-weight:900; }
</style>
</head>
<body>
<div class="top-status-bar">
    <div>AI STATE: <span id="g-state" style="color:var(--blue)">---</span></div>
    <div>EXPOSURE: <span id="g-exp" style="color:#fff">0%</span></div>
    <div>VOLATILITY: <span id="g-vol" style="color:var(--orange)">---</span></div>
    <div><span id="regime" style="color:var(--orange);">REGIME: SCANNING</span></div>
</div>
<div class="header">
    <div><span style="font-weight:900; font-size:18px;">TRADE<span style="color:var(--green)">BOT v7.0 (SAFE MODE)</span></span><span id="mkt" style="margin-left:20px; font-size:11px; color:#9ca3af; font-weight:bold;">...</span></div>
    <div class="pill" id="status" style="background:var(--orange)">CONNECTING...</div>
</div>
<div class="grid">
    <div class="card" style="overflow-y:auto;">
        <div class="muted">Net Equity</div><div class="big" id="equity">$0.00</div>
        <div class="muted" style="margin-top:15px;">Buying Power</div><div id="cash" style="font-weight:bold; font-size:18px; margin-bottom:15px;">$0.00</div>
        <hr style="border:0; border-top:1px solid var(--border); margin:20px 0;">
        <div class="muted" style="margin-bottom:10px;">Minervini Radar</div><div id="ranked">
            <div style="padding:15px; text-align:center; color:var(--orange);">Waiting for Engine...</div>
        </div>
    </div>
    <div style="display:flex; flex-direction:column; gap:20px;">
        <div class="card" style="flex-grow:1; overflow-y:auto;"><div class="muted" style="margin-bottom:15px;">Live Swings</div><div id="pos-container">
            <div style="text-align:center; padding:20px; color:var(--orange);">Connecting to broker...</div>
        </div></div>
    </div>
    <div class="card" style="overflow-y:auto;"><div class="muted" style="margin-bottom:15px;">Activity Log</div><div id="logs" style="font-family:monospace; font-size:11px; line-height:1.6; color:#a1a1aa;"></div></div>
</div>

<script>
    // BARE METAL ES5 JAVASCRIPT - NO FANCY SYNTAX, NO CANVAS
    document.getElementById("logs").innerHTML = "<div style='color:var(--green); padding:5px 0;'>[UI] Safe Mode Interface loaded. Requesting data...</div>";

    function updateDashboard() {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "/api/data?t=" + new Date().getTime(), true);
        xhr.timeout = 5000;
        
        xhr.onload = function() {
            if (xhr.status === 200) {
                try {
                    var data = JSON.parse(xhr.responseText);
                    
                    document.getElementById("status").innerText = "LIVE SYNC";
                    document.getElementById("status").style.background = "var(--green)";
                    
                    document.getElementById("g-state").innerText = data.bot_stats.ai_state || "---";
                    document.getElementById("g-exp").innerText = data.bot_stats.exposure || "0%";
                    
                    var regimeSpan = document.getElementById("regime");
                    regimeSpan.innerText = "REGIME: " + (data.market_regime || "UNKNOWN");
                    regimeSpan.style.color = (data.market_regime === "BULLISH") ? "var(--green)" : "var(--red)";
                    
                    var eq = parseFloat(data.account.equity || 0);
                    var ca = parseFloat(data.account.cash || 0);
                    document.getElementById("equity").innerText = "$" + eq.toFixed(2);
                    document.getElementById("cash").innerText = "$" + ca.toFixed(2);
                    
                    // Positions
                    var posHtml = "";
                    if (data.positions && data.positions.length > 0) {
                        posHtml += '<table><thead><tr><th>Asset</th><th>Entry</th><th>Price</th><th>P/L</th></tr></thead><tbody>';
                        for (var i = 0; i < data.positions.length; i++) {
                            var p = data.positions[i];
                            var pl = parseFloat(p.unrealized_intraday_pl || 0);
                            var color = pl >= 0 ? "var(--green)" : "var(--red)";
                            posHtml += '<tr><td><b>' + p.symbol + '</b></td><td>$' + parseFloat(p.avg_entry_price).toFixed(2) + '</td><td>$' + parseFloat(p.current_price).toFixed(2) + '</td><td style="color:' + color + '; font-weight:bold;">$' + pl.toFixed(2) + '</td></tr>';
                        }
                        posHtml += '</tbody></table>';
                    } else {
                        posHtml = "<div style='text-align:center; padding:20px; color:var(--orange);'>No Swings Active. Scouting...</div>";
                    }
                    document.getElementById("pos-container").innerHTML = posHtml;
                    
                    // Radar
                    var rankHtml = "";
                    if (data.ranked && data.ranked.length > 0) {
                        for (var j = 0; j < data.ranked.length; j++) {
                            var r = data.ranked[j];
                            var rColor = r.confidence >= 80 ? "var(--green)" : (r.confidence >= 65 ? "var(--yellow)" : "var(--red)");
                            var stat = r.multiplier > 0 ? "ACQUIRING" : "WATCHING";
                            if (r.confidence === 0) stat = "REJECTED/ERROR";
                            
                            rankHtml += "<div style='margin-bottom:12px; border-bottom:1px solid var(--border); padding-bottom:10px;'>";
                            rankHtml += "<div style='display:flex; justify-content:space-between;'>";
                            rankHtml += "<div><b>" + r.symbol + "</b><br><span style='font-size:10px; color:#a1a1aa;'>$" + parseFloat(r.price).toFixed(2) + "</span></div>";
                            rankHtml += "<div style='text-align:right;'><b style='color:" + rColor + ";'>" + r.confidence + "%</b><br><span style='font-size:9px; color:" + rColor + ";'>" + stat + "</span></div>";
                            rankHtml += "</div></div>";
                        }
                    } else {
                        rankHtml = "<div style='text-align:center; padding:15px; color:var(--orange);'>Sweeping Universe...</div>";
                    }
                    document.getElementById("ranked").innerHTML = rankHtml;
                    
                    // Logs
                    var logHtml = "";
                    if (data.activity && data.activity.length > 0) {
                        for (var k = 0; k < data.activity.length; k++) {
                            var a = data.activity[k];
                            var lColor = (a.indexOf("ERROR") !== -1 || a.indexOf("Crash") !== -1) ? "var(--red)" : "#a1a1aa";
                            logHtml += "<div style='padding:5px 0; border-bottom:1px solid #1f2937; color:" + lColor + ";'>" + a + "</div>";
                        }
                    }
                    document.getElementById("logs").innerHTML = logHtml;
                    
                } catch (e) {
                    document.getElementById("logs").innerHTML = "<div style='color:red;'>[JSON Parse Error] " + e.message + "</div>" + document.getElementById("logs").innerHTML;
                }
            } else {
                document.getElementById("status").innerText = "SERVER ERROR";
                document.getElementById("status").style.background = "var(--red)";
            }
        };
        
        xhr.onerror = function() {
            document.getElementById("status").innerText = "NETWORK ERROR";
            document.getElementById("status").style.background = "var(--red)";
        };
        
        xhr.ontimeout = function() {
            document.getElementById("status").innerText = "TIMEOUT";
            document.getElementById("status").style.background = "var(--orange)";
        };
        
        xhr.send();
    }

    setInterval(updateDashboard, 3000);
    updateDashboard();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
