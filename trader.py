import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime
from flask import Flask, jsonify

# =========================================================
# CONFIG - HARDCODED PER USER REQUEST
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
    "watchlist": [], 
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
    "base_trade_usd": 20.00,
    "last_scan_date": "",
    "risk_pct": 2.0,    
    "reward_pct": 6.0,
    "wins": 0,
    "total_profit": 0.0,
    "kill_switch_active": False
}

trailing_stops = {} 
activity_log = ["System Initialized... UI Polish Applied"]

global_state = {
    "account": {"equity": "0.00", "cash": "0.00"},
    "positions": [],
    "ranked": [],
    "activity": activity_log[::-1],
    "bot_stats": {"wins": 0, "profit": 0.0},
    "market_status": "BOOTING ENGINE...",
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
        log_event(f"🎯 DAILY SCAN COMPLETE")
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

def place_order(symbol, current_price, multiplier):
    if bot["kill_switch_active"]: return
    try:
        trade_val = bot["base_trade_usd"] * multiplier
        qty = round(trade_val / current_price, 5)
        tif = "gtc" if "/" in symbol else "day"
        payload = {"symbol": symbol, "qty": qty, "side": "buy", "type": "market", "time_in_force": tif}
        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload)
        if r.status_code == 200:
            log_event(f"🔥 EXECUTED: {symbol} (${trade_val:.2f} | {multiplier}x Size)")
    except Exception as e:
        log_event(f"ORDER ERROR: {str(e)}")

def get_bars(symbol, limit=60):
    is_crypto = "/" in symbol
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/{symbol}/bars"
    params = {"symbols": symbol, "timeframe": "1Min", "limit": limit} if is_crypto else {"timeframe": "1Min", "limit": limit, "feed": "iex"}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200: return None
        data = r.json()
        bars = data.get("bars", {}).get(symbol, []) if is_crypto else data.get("bars", [])
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except: return None

def analyze_symbol(symbol, regime):
    try:
        df = get_bars(symbol, 60)
        if df is None or len(df) < 30: 
            return {"symbol": symbol, "score": 0.0, "confidence": 0, "reasons": ["Not enough data"], "price": 0.0, "multiplier": 0}
        
        price_now = float(df["c"].iloc[-1])
        price_15m = float(df["c"].iloc[-15])
        price_60m = float(df["c"].iloc[0])
        
        trend_1h = ((price_now - price_60m) / price_60m) * 100
        trend_15m = ((price_now - price_15m) / price_15m) * 100
        
        avg_vol = df["v"].iloc[:-1].mean()
        curr_vol = df["v"].iloc[-1]
        vol_spike = bool(curr_vol > (avg_vol * 1.5))
        
        confidence = 0
        reasons = []

        if trend_1h > 0.5:
            confidence += 30
            reasons.append("📈 1H Trend: Strong Bullish")
        elif trend_1h > 0:
            confidence += 10
            reasons.append("↗️ 1H Trend: Mild Bullish")
        else:
            reasons.append("📉 1H Trend: Bearish (Headwind)")

        if trend_15m > 0.15:
            confidence += 40
            reasons.append("🚀 15m Momentum: Breakout Detected")
        elif trend_15m > 0:
            confidence += 15
            reasons.append("⏱️ 15m Momentum: Building")
        else:
            reasons.append("⚠️ 15m Momentum: Reversing")

        if vol_spike:
            confidence += 20
            reasons.append("🔥 Volume: High Accumulation")
        else:
            reasons.append("💤 Volume: Standard / Low")

        if regime == "RISK ON (BULLISH)":
            confidence += 10
            reasons.append("🌊 Market Regime: Favorable Tailwind")
        elif regime == "RISK OFF (BEARISH)":
            confidence -= 20
            reasons.append("🩸 Market Regime: Bearish (High Risk)")

        if confidence >= 80:
            multiplier = 1.5
        elif confidence >= 60:
            multiplier = 1.0
        else:
            multiplier = 0.0

        return {"symbol": symbol, "score": trend_15m, "confidence": confidence, "reasons": reasons, "price": price_now, "multiplier": multiplier}
    except:
        return {"symbol": symbol, "score": 0.0, "confidence": 0, "reasons": ["API Error"], "price": 0.0, "multiplier": 0}

def engine():
    global global_state
    while True:
        try:
            if bot["total_profit"] < -10.00 and not bot["kill_switch_active"]:
                bot["kill_switch_active"] = True
                log_event("🛑 KILL SWITCH ACTIVATED: Max drawdown reached.")

            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            if is_open and bot["last_scan_date"] != clock_data.get('timestamp', '')[:10]:
                run_daily_scanner()
                bot["last_scan_date"] = clock_data.get('timestamp', '')[:10]
            
            acc = get_account()
            pos = get_positions()
            cash = float(acc.get("cash") or 0)

            spy_df = get_bars("SPY", 15)
            market_regime = "CHOP / RANGING"
            if spy_df is not None and len(spy_df) > 10:
                spy_change = ((spy_df["c"].iloc[-1] - spy_df["c"].iloc[0]) / spy_df["c"].iloc[0]) * 100
                if spy_change > 0.05: market_regime = "RISK ON (BULLISH)"
                elif spy_change < -0.05: market_regime = "RISK OFF (BEARISH)"

            for p in pos:
                sym = p.get('symbol')
                if not sym: continue
                curr_plpc = float(p.get("unrealized_plpc") or 0) * 100
                pl_dollars = float(p.get("unrealized_pl") or 0)
                if sym not in trailing_stops or curr_plpc > trailing_stops[sym]:
                    trailing_stops[sym] = curr_plpc
                if (trailing_stops[sym] - curr_plpc) >= bot["risk_pct"]:
                    log_event(f"🛡️ TRAIL STOP: {sym} ({curr_plpc:.2f}%)")
                    requests.delete(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS)
                    if sym in trailing_stops: del trailing_stops[sym]
                elif curr_plpc >= bot["reward_pct"]:
                    log_event(f"🎯 TARGET HIT: {sym} (+{curr_plpc:.2f}%)")
                    bot["wins"] += 1
                    bot["total_profit"] += pl_dollars
                    requests.delete(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS)
                    if sym in trailing_stops: del trailing_stops[sym]

            active_list = bot["watchlist"] + bot["crypto_watchlist"]
            if len(bot["watchlist"]) == 0:
                active_list = ["TSLA", "NVDA", "AMD", "COIN", "MARA"] + bot["crypto_watchlist"]
            
            ranked = [analyze_symbol(s, market_regime) for s in active_list]
            ranked = [r for r in ranked if r is not None]
            ranked.sort(key=lambda x: x["confidence"], reverse=True)

            for r in ranked[:2]:
                if r["multiplier"] > 0 and not bot["kill_switch_active"]:
                    if not any(p.get('symbol') == r['symbol'] for p in pos) and cash >= (bot["base_trade_usd"] * r["multiplier"]):
                        place_order(r["symbol"], r["price"], r["multiplier"])

            global_state = {
                "account": acc,
                "positions": pos,
                "ranked": ranked[:10],
                "activity": activity_log[::-1],
                "bot_stats": {"wins": bot["wins"], "profit": bot["total_profit"]},
                "market_status": "MARKET OPEN" if is_open else "MARKET CLOSED",
                "market_regime": market_regime,
                "is_open": is_open,
                "next_event": clock_data.get('next_close', '') if is_open else clock_data.get('next_open', '')
            }

        except Exception as e:
            log_event(f"ENGINE ERROR: {str(e)}")
        time.sleep(10)

threading.Thread(target=engine, daemon=True).start()

@app.route("/api/data")
def api_data():
    return jsonify(global_state)

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuantumPro Terminal</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    :root { --bg: #0b1220; --card: #111827; --border: #1f2937; --text: #e5e7eb; --green: #22c55e; --red: #ef4444; --orange: #f59e0b; --blue: #3b82f6; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:sans-serif; overflow-x:hidden; }
    .header { display:flex; justify-content:space-between; align-items:center; padding:15px 25px; background:var(--card); border-bottom:1px solid var(--border); }
    /* WIDENED THE SIDEBARS from 300px to 320px for breathing room */
    .grid { display:grid; grid-template-columns:320px 1fr 320px; gap:20px; padding:20px; height: calc(100vh - 80px); }
    .card { background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; }
    .big { font-size:32px; font-weight:bold; color:#fff; }
    .muted { color:#9ca3af; font-size:11px; text-transform:uppercase; font-weight:800; letter-spacing:1px; margin-bottom:5px; }
    table { width:100%; border-collapse:collapse; font-size:13px; text-align:left; }
    th { color:#9ca3af; padding-bottom:12px; border-bottom:1px solid var(--border); }
    td { padding:12px 0; border-bottom:1px solid var(--border); }
    .pill { background:#4b5563; color:#fff; padding:4px 10px; border-radius:20px; font-size:11px; font-weight:900; }
    .status-badge { font-size:9px; padding:2px 6px; border-radius:4px; font-weight:bold; display:inline-block; }
    .countdown-box { text-align:center; padding:40px 20px; border: 2px dashed var(--border); border-radius:10px; margin-top:20px; }
    .chart-container { height: 150px; width: 100%; position: relative; }
    
    .ai-btn { background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.3); color:var(--blue); font-size:10px; padding:3px 6px; border-radius:4px; cursor:pointer; font-weight:bold; transition: 0.2s; }
    .ai-btn:hover { background:rgba(59,130,246,0.3); }
    .ai-panel { display:none; background:rgba(0,0,0,0.3); border-left: 2px solid var(--blue); padding:10px; margin-top:8px; border-radius:0 6px 6px 0; font-size:11px; }
    
    @media (max-width: 1024px) { .grid { grid-template-columns: 1fr; height:auto; } .card { margin-bottom:15px; } }
</style>
</head>
<body>
<div class="header">
    <div><span style="font-weight:900; font-size:18px;">QUANTUM<span style="color:var(--green)">PRO</span></span><span id="mkt" style="margin-left:20px; font-size:11px; color:#9ca3af; font-weight:bold;">...</span></div>
    <div style="display:flex; gap:10px; align-items:center;">
        <span id="regime" style="font-size:10px; font-weight:bold; color:var(--orange);">REGIME: SCANNING</span>
        <div class="pill" id="status" style="background:var(--orange)">FETCHING DATA...</div>
    </div>
</div>

<div class="grid">
    <div class="card">
        <div class="muted">Net Equity</div>
        <div class="big" id="equity">$0.00</div>
        <div class="muted" style="margin-top:20px;">Buying Power</div>
        <div id="cash" style="font-weight:bold; font-size:18px; margin-bottom:20px;">$0.00</div>
        <hr style="border:0; border-top:1px solid var(--border); margin:20px 0;">
        <div class="muted" style="margin-bottom:10px;">Scanned Active Targets</div>
        <div id="ranked"></div>
    </div>
    
    <div style="display:flex; flex-direction:column; gap:20px;">
        <div class="card">
            <div class="muted" style="margin-bottom:15px;">Live 1:3 Risk/Reward Positions</div>
            <div id="pos-container"></div>
        </div>
        
        <div class="card">
            <div class="muted">Session Performance</div>
            <div style="display:flex; gap: 40px; margin-top:15px; margin-bottom: 10px;">
                <div>
                    <div class="muted" style="font-size:10px; margin-bottom:2px;">Wins</div>
                    <div id="winrate" style="font-size:22px; font-weight:bold; color:var(--green);">0</div>
                </div>
                <div>
                    <div class="muted" style="font-size:10px; margin-bottom:2px;">Total Profit</div>
                    <div id="profit" style="font-size:22px; font-weight:bold; color:var(--green);">$0.00</div>
                </div>
            </div>
            <div class="chart-container"><canvas id="equityChart"></canvas></div>
        </div>
    </div>

    <div class="card">
        <div class="muted" style="margin-bottom:15px;">Execution Log</div>
        <div id="logs" style="font-family:monospace; font-size:11px; line-height:1.6; color:#a1a1aa;"></div>
    </div>
</div>

<script>
    const f = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    
    const ctx = document.getElementById('equityChart').getContext('2d');
    const eqChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ data: [], borderColor: '#22c55e', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true, backgroundColor: 'rgba(34,197,94,0.1)' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { display: false } }, animation: { duration: 0 } }
    });

    function toggleAI(symbol) {
        const panel = document.getElementById('ai-' + symbol);
        panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    }

    function updateCountdown(endTime) {
        if (!endTime) return "...";
        const diff = new Date(endTime) - new Date();
        if (diff <= 0) return "Closing Session...";
        const h = Math.floor(diff / 3600000), m = Math.floor((diff % 3600000) / 60000), s = Math.floor((diff % 60000) / 1000);
        return `${h}h ${m}m ${s}s`;
    }

    async function fetchBotData() {
        try {
            const response = await fetch('/api/data');
            const d = await response.json();

            document.getElementById("status").innerText = "LIVE SYNC";
            document.getElementById("status").style.background = "var(--green)";
            document.getElementById("mkt").innerText = d.market_status;
            
            const regBadge = document.getElementById("regime");
            regBadge.innerText = "REGIME: " + d.market_regime;
            regBadge.style.color = d.market_regime.includes("BULLISH") ? "var(--green)" : (d.market_regime.includes("BEARISH") ? "var(--red)" : "var(--orange)");
            
            const currentEq = parseFloat(d.account.equity || 0);
            document.getElementById("equity").innerText = f.format(currentEq);
            document.getElementById("cash").innerText = f.format(d.account.cash);
            document.getElementById("winrate").innerText = d.bot_stats.wins;
            document.getElementById("profit").innerText = f.format(d.bot_stats.profit);
            
            if (currentEq > 0 && d.account.equity !== "0.00") {
                eqChart.data.labels.push("");
                eqChart.data.datasets[0].data.push(currentEq);
                if(eqChart.data.labels.length > 50) { eqChart.data.labels.shift(); eqChart.data.datasets[0].data.shift(); }
                eqChart.update();
            }

            const posContainer = document.getElementById("pos-container");
            if (d.positions && d.positions.length > 0) {
                posContainer.innerHTML = `<table><thead><tr><th>Asset</th><th>Entry</th><th>Current</th><th>P/L</th><th>Stop</th><th>Target</th></tr></thead><tbody>${d.positions.map(p => {
                    const entry = parseFloat(p.avg_entry_price);
                    const pl = parseFloat(p.unrealized_intraday_pl);
                    return `<tr>
                        <td><b style="font-size:14px;">${p.symbol}</b><br><span style="font-size:10px; color:#9ca3af;">${p.qty} shs</span></td>
                        <td>${f.format(entry)}</td>
                        <td>${f.format(p.current_price)}</td>
                        <td style="color:${pl >= 0 ? 'var(--green)' : 'var(--red)'}">${f.format(pl)}<br><span style="font-size:10px;">(${(parseFloat(p.unrealized_intraday_plpc)*100).toFixed(2)}%)</span></td>
                        <td style="color:var(--red)">${f.format(entry * 0.98)}</td>
                        <td style="color:var(--blue)">${f.format(entry * 1.06)}<br><span class="status-badge" style="background:rgba(59,130,246,0.1); color:var(--blue);">TRAILING</span></td>
                    </tr>`;
                }).join("")}</tbody></table>`;
            } else {
                posContainer.innerHTML = `<div class="countdown-box">
                    <div class="muted">No Active Positions</div>
                    <div style="font-size:14px; color:var(--orange); font-weight:bold; margin:10px 0;">📡 Hunting for Breakouts...</div>
                </div>`;
            }

            if (d.ranked) {
                document.getElementById("ranked").innerHTML = d.ranked.map(r => {
                    let confColor = r.confidence >= 80 ? 'var(--green)' : (r.confidence >= 60 ? 'var(--blue)' : 'var(--red)');
                    let actionText = r.multiplier > 1 ? 'AGGRESSIVE BUY' : (r.multiplier > 0 ? 'STANDARD BUY' : 'NO TRADE STATE');
                    
                    // REFORMATTED: Ticker and Score on top, Button and Badge on bottom
                    return `<div style="margin-bottom:12px; border-bottom:1px solid var(--border); padding-bottom:10px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                            <b style="font-size:14px;">${r.symbol}</b>
                            <span style="font-weight:bold; color:${confColor}; font-size:12px;">${r.confidence}% Conf</span>
                        </div>
                        
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="ai-btn" onclick="toggleAI('${r.symbol}')">📊 AI Breakdown ▾</span>
                            ${r.vol_spike ? `<span class="status-badge" style="background:rgba(34,197,94,0.1); color:var(--green);">⚡ VOL SPIKE</span>` : ''}
                        </div>
                        
                        <div id="ai-${r.symbol}" class="ai-panel">
                            <div style="color:var(--blue); font-weight:bold; margin-bottom:5px;">AI Reasoning:</div>
                            <ul style="margin:0; padding-left:15px; color:#a1a1aa; margin-bottom:8px;">
                                ${r.reasons.map(reason => `<li style="margin-bottom:3px;">${reason}</li>`).join('')}
                            </ul>
                            <div style="font-weight:bold; color:${confColor}; border-top:1px solid rgba(255,255,255,0.1); padding-top:5px; margin-top:5px;">
                                Action: ${actionText}
                            </div>
                        </div>
                    </div>`;
                }).join("");
            }

            if (d.activity) {
                document.getElementById("logs").innerHTML = d.activity.map(a => `<div style="padding:4px 0; border-bottom:1px solid #1f2937;">${a}</div>`).join("");
            }
            
        } catch (error) {
            console.error("Fetch Error:", error);
        }
    }

    setInterval(fetchBotData, 3000);
    fetchBotData(); 
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
