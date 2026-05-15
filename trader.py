import os
import time
import threading
import requests
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
    "watchlist": [], 
    "crypto_watchlist": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
    "base_trade_usd": 25.00,
    "last_scan_date": "",
    "risk_pct": 5.0,    
    "reward_pct": 15.0, 
    "wins": 0,
    "total_profit": 0.0,
    "kill_switch_active": False,
    "exposure_pct": 0
}

activity_log = ["TradeBot Engine Online... Native Graphics Engine Engaged."]

global_state = {
    "account": {"equity": "0.00", "cash": "0.00"},
    "positions": [],
    "ranked": [],
    "activity": activity_log[::-1],
    "bot_stats": {"wins": 0, "profit": 0.0, "exposure": "0%", "ai_state": "BOOTING", "volatility": "SCANNING"},
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
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars" if is_crypto else f"{DATA_URL}/stocks/bars"
    params = {"symbols": symbol, "timeframe": "1Day", "limit": limit} if is_crypto else {"symbols": symbol, "timeframe": "1Day", "limit": limit, "feed": "iex"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code != 200: return None
        data = r.json()
        bars = data.get("bars", {}).get(symbol, [])
        return pd.DataFrame(bars) if len(bars) > 0 else None
    except: return None

def analyze_swing_symbol(symbol, regime):
    try:
        df = get_daily_bars(symbol, 100)
        if df is None or len(df) < 50: return None
        
        df['8_EMA'] = df['c'].ewm(span=8, adjust=False).mean()
        df['21_EMA'] = df['c'].ewm(span=21, adjust=False).mean()
        df['50_SMA'] = df['c'].rolling(window=50).mean()
        
        df.fillna(0.0, inplace=True)
        
        price_now = float(df["c"].iloc[-1])
        ema8 = float(df["8_EMA"].iloc[-1])
        ema21 = float(df["21_EMA"].iloc[-1])
        sma50 = float(df["50_SMA"].iloc[-1])
        
        if "t" in df:
            dates = [str(t)[:10] for t in df["t"].tail(30).tolist()]
        else:
            dates = [(datetime.now() - timedelta(days=30-i)).strftime('%Y-%m-%d') for i in range(30)]

        spark_data = {
            "dates": dates,
            "open": [float(x) for x in df["o"].tail(30).tolist()],
            "high": [float(x) for x in df["h"].tail(30).tolist()],
            "low": [float(x) for x in df["l"].tail(30).tolist()],
            "close": [float(x) for x in df["c"].tail(30).tolist()],
            "ema8": [float(x) for x in df["8_EMA"].tail(30).tolist()],
            "ema21": [float(x) for x in df["21_EMA"].tail(30).tolist()]
        }
        
        confidence = 50 
        reasons = []
        counter_reasons = []

        if price_now > sma50:
            confidence += 20; reasons.append("Price > 50 SMA")
        else:
            confidence -= 20; counter_reasons.append("Price < 50 SMA")

        if ema8 > ema21:
            confidence += 20; reasons.append("8 EMA > 21 EMA")
        else:
            confidence -= 15; counter_reasons.append("Waiting for EMA Cross")

        if price_now > ema8:
            confidence += 10; reasons.append("Momentum is Positive")
        
        if regime == "BULLISH":
            confidence += 15; reasons.append("Market Regime: Bullish")
        elif regime == "BEARISH":
            confidence -= 25; counter_reasons.append("Market Regime: Bearish")

        confidence = max(0, min(100, confidence))
        multiplier = 1.5 if confidence >= 80 else (1.0 if confidence >= 65 else 0.0)
        risk_lvl = "LOW" if price_now > ema21 else "HIGH"

        return {
            "symbol": symbol, "confidence": confidence, "reasons": reasons, 
            "counter_reasons": counter_reasons, "price": float(price_now), 
            "multiplier": multiplier, "risk": risk_lvl, "spark": spark_data
        }
    except Exception as e: 
        return None

def engine():
    global global_state
    time.sleep(5) 
    while True:
        try:
            clock_req = requests.get(f"{BASE_URL}/v2/clock", headers=HEADERS, timeout=5)
            clock_data = clock_req.json() if clock_req.status_code == 200 else {}
            is_open = clock_data.get("is_open", False)
            
            acc = get_account()
            pos = get_positions()
            cash = float(acc.get("cash") or 0)
            equity = float(acc.get("equity") or 0)
            
            spy_df = get_daily_bars("SPY", 20)
            regime = "CHOP"
            if spy_df is not None:
                spy_c = float(spy_df['c'].iloc[-1])
                spy_ema = float(spy_df['c'].ewm(span=10, adjust=False).mean().iloc[-1])
                regime = "BULLISH" if spy_c > spy_ema else "BEARISH"

            for p in pos:
                sym = p.get('symbol')
                if not sym or sym == "USD": continue
                p_df = get_daily_bars(sym, 30)
                if p_df is not None:
                    p_df['21_EMA'] = p_df['c'].ewm(span=21, adjust=False).mean()
                    orange_line = float(p_df['21_EMA'].iloc[-1])
                    curr_p = float(p.get("current_price"))
                    if curr_p < orange_line:
                        log_event(f"EXIT: {sym} closed below 21 EMA.")
                        requests.delete(f"{BASE_URL}/v2/positions/{sym}", headers=HEADERS, timeout=5)

            active_list = UNIVERSE + bot["crypto_watchlist"]
            ranked_results = [analyze_swing_symbol(s, regime) for s in active_list]
            ranked = [r for r in ranked_results if r is not None]
            ranked.sort(key=lambda x: x["confidence"], reverse=True)

            if len(ranked) > 0:
                log_event(f"📡 Radar Swept {len(ranked)} targets. Top Watch: {ranked[0]['symbol']}")

            for r in ranked[:2]:
                if r["multiplier"] > 0 and not any(p.get('symbol') == r['symbol'] for p in pos):
                    if cash >= (bot["base_trade_usd"] * r["multiplier"]):
                        val = bot["base_trade_usd"] * r["multiplier"]
                        qty = round(val / r["price"], 5)
                        payload = {"symbol": r['symbol'], "qty": qty, "side": "buy", "type": "market", "time_in_force": "gtc"}
                        requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=5)
                        log_event(f"BUY: {r['symbol']} (${val:.2f})")

            invested = sum(float(p.get("market_value") or 0) for p in pos if p.get('symbol') != "USD")
            exposure = int((invested / equity) * 100) if equity > 0 else 0

            global_state = {
                "account": acc, "positions": pos, "ranked": ranked[:8], "activity": activity_log[::-1],
                "bot_stats": {"wins": bot["wins"], "profit": bot["total_profit"], "exposure": str(exposure) + "%", "ai_state": "SWINGING", "volatility": "NORMAL"},
                "market_status": "MARKET OPEN" if is_open else "MARKET CLOSED",
                "market_regime": regime, "is_open": is_open, "next_event": clock_data.get('next_close', '')
            }
        except Exception as e:
            log_event(f"NETWORK ERROR: Intermittent connection loss...")
        
        time.sleep(60)

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
    .ai-btn { background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.3); color:var(--blue); font-size:10px; padding:3px 6px; border-radius:4px; cursor:pointer; font-weight:bold; transition: 0.2s; }
    .ai-panel { display:none; background:rgba(0,0,0,0.3); border-left: 2px solid var(--blue); padding:10px; margin-top:8px; border-radius:0 6px 6px 0; font-size:11px; }
    .sparkline { width: 60px; height: 25px; }
    @media (max-width: 1024px) { .grid { grid-template-columns: 1fr; height:auto; } .card { margin-bottom:15px; } }
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
    <div><span style="font-weight:900; font-size:18px;">TRADE<span style="color:var(--green)">BOT</span></span><span id="mkt" style="margin-left:20px; font-size:11px; color:#9ca3af; font-weight:bold;">...</span></div>
    <div class="pill" id="status" style="background:var(--orange)">CONNECTING...</div>
</div>
<div class="grid">
    <div class="card" style="overflow-y:auto;">
        <div class="muted">Net Equity</div><div class="big" id="equity">$0.00</div>
        <div class="muted" style="margin-top:15px;">Buying Power</div><div id="cash" style="font-weight:bold; font-size:18px; margin-bottom:15px;">$0.00</div>
        <hr style="border:0; border-top:1px solid var(--border); margin:20px 0;">
        <div class="muted" style="margin-bottom:10px;">Minervini Radar</div><div id="ranked">
            <div style="padding:15px; text-align:center; color:var(--orange); border: 1px dashed var(--border); border-radius: 8px;">
                📡 Waking up server...<br><span style="font-size:10px; color:#a1a1aa;">This can take 60s if the cloud was asleep.</span>
            </div>
        </div>
    </div>
    <div style="display:flex; flex-direction:column; gap:20px;">
        <div class="card" style="flex-grow:1; overflow-y:auto;"><div class="muted" style="margin-bottom:15px;">Live Swings</div><div id="pos-container">
            <div style="text-align:center; padding:20px; color:var(--orange);">Establishing connection to broker...</div>
        </div></div>
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:15px;">
                <div><div class="muted" id="chart-title">AI Strategy X-Ray</div><div style="font-size:12px; color:#a1a1aa;">Tracking Top Target</div></div>
                <div style="display:flex; gap:10px; font-size:10px; font-weight:bold;">
                    <span style="color:var(--blue);">8 EMA</span>
                    <span style="color:var(--orange);">21 EMA</span>
                </div>
            </div>
            <div style="height:180px; width:100%; position:relative;">
                <canvas id="xrayCanvas" style="position:absolute; top:0; left:0; width:100%; height:100%;"></canvas>
            </div>
        </div>
    </div>
    <div class="card" style="overflow-y:auto;"><div class="muted" style="margin-bottom:15px;">Activity</div><div id="logs" style="font-family:monospace; font-size:11px; line-height:1.6; color:#a1a1aa;"></div></div>
</div>

<script>
    const formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    function drawNativeChart(data) {
        const canvas = document.getElementById('xrayCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = rect.height;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        if (!data || !data.close || data.close.length === 0) return;
        const w = canvas.width;
        const h = canvas.height;
        const len = data.close.length;
        
        let minP = Math.min.apply(null, data.low.concat(data.ema8, data.ema21));
        let maxP = Math.max.apply(null, data.high.concat(data.ema8, data.ema21));
        const pad = (maxP - minP) * 0.1 || 1;
        minP -= pad; maxP += pad;

        const step = w / len;
        const candleW = step * 0.6;
        function getY(price) { return h - ((price - minP) / (maxP - minP)) * h; }

        ctx.strokeStyle = 'rgba(31, 41, 55, 0.5)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for(let i=1; i<4; i++) {
            let y = i * (h/4);
            ctx.moveTo(0, y); ctx.lineTo(w, y);
        }
        ctx.stroke();

        for(let i=0; i<len; i++) {
            const x = i * step + step/2;
            const o = data.open[i], c = data.close[i], hi = data.high[i], lo = data.low[i];
            const isGreen = c >= o;
            ctx.strokeStyle = isGreen ? '#22c55e' : '#ef4444';
            ctx.fillStyle = isGreen ? '#22c55e' : '#ef4444';
            ctx.beginPath();
            ctx.moveTo(x, getY(hi));
            ctx.lineTo(x, getY(lo));
            ctx.stroke();
            const bTop = getY(Math.max(o, c));
            const bBot = getY(Math.min(o, c));
            const bHeight = Math.max(1, bBot - bTop);
            ctx.fillRect(x - candleW/2, bTop, candleW, bHeight);
        }

        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for(let i=0; i<len; i++) {
            const x = i * step + step/2;
            const y = getY(data.ema21[i]);
            if(i===0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();

        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for(let i=0; i<len; i++) {
            const x = i * step + step/2;
            const y = getY(data.ema8[i]);
            if(i===0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    window.addEventListener('resize', () => {
        if(window.lastChartData) drawNativeChart(window.lastChartData);
    });

    function togglePanel(id) {
        const panel = document.getElementById(id);
        panel.style.display = (panel.style.display === 'none' || panel.style.display === '') ? 'block' : 'none';
    }

    function createSparkline(dataArray, color) {
        try {
            if(!dataArray || dataArray.length === 0) return '';
            const max = Math.max.apply(null, dataArray), min = Math.min.apply(null, dataArray), range = (max - min) || 1;
            let pts = "";
            for(let i=0; i<dataArray.length; i++) {
                pts += (i/(dataArray.length-1)*60) + ',' + (25 - ((dataArray[i]-min)/range)*25) + ' ';
            }
            return '<svg class="sparkline" style="stroke:'+color+'; fill:none; stroke-width:1.5px;"><polyline points="'+pts+'"/></svg>';
        } catch(e) { return ''; }
    }

    async function fetchData() {
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 8000);
            const response = await fetch('/api/data', { signal: controller.signal });
            clearTimeout(timeoutId);
            
            if (!response.ok) throw new Error("Server Status: " + response.status);
            const data = await response.json();
            
            document.getElementById("status").innerText = "LIVE SYNC";
            document.getElementById("status").style.background = "var(--green)";
            
            document.getElementById("g-state").innerText = data.bot_stats.ai_state;
            document.getElementById("g-exp").innerText = data.bot_stats.exposure;
            document.getElementById("g-vol").innerText = data.bot_stats.volatility;
            
            const regimeSpan = document.getElementById("regime");
            regimeSpan.innerText = "REGIME: " + data.market_regime;
            regimeSpan.style.color = data.market_regime.includes("BULLISH") ? "var(--green)" : "var(--red)";
            
            const eq = parseFloat(data.account.equity || 0);
            document.getElementById("equity").innerText = formatter.format(eq);
            document.getElementById("cash").innerText = formatter.format(data.account.cash);
            
            const posContainer = document.getElementById("pos-container");
            if (data.positions && data.positions.length > 0) {
                let html = '<table><thead><tr><th>Asset</th><th>Entry</th><th>Price</th><th>P/L</th></tr></thead><tbody>';
                for (let p of data.positions) {
                    const pl = parseFloat(p.unrealized_intraday_pl || 0);
                    const plColor = pl >= 0 ? 'var(--green)' : 'var(--red)';
                    html += '<tr><td><b>' + p.symbol + '</b></td><td>' + formatter.format(p.avg_entry_price) + '</td><td>' + formatter.format(p.current_price) + '</td><td style="color:' + plColor + '; font-weight:bold;">' + formatter.format(pl) + '</td></tr>';
                }
                html += '</tbody></table>';
                posContainer.innerHTML = html;
            } else {
                posContainer.innerHTML = '<div style="text-align:center; padding:20px; color:var(--orange);">No Swings Active. Scouting for Setups...</div>';
            }
            
            const rankedContainer = document.getElementById("ranked");
            if (data.ranked && data.ranked.length > 0) {
                
                const topTarget = data.ranked[0];
                document.getElementById("chart-title").innerText = "X-Ray: " + topTarget.symbol;
                
                if(topTarget.spark && topTarget.spark.close.length > 0) {
                    window.lastChartData = topTarget.spark;
                    drawNativeChart(topTarget.spark);
                }

                let html = '';
                for (let r of data.ranked) {
                    const color = r.confidence >= 80 ? 'var(--green)' : (r.confidence >= 65 ? 'var(--yellow)' : 'var(--red)');
                    let statusText = r.multiplier > 0 ? "🟢 ACQUIRING" : (r.confidence > 40 ? "🟡 WATCHING EMA" : "🔴 REJECTED");

                    let reasonsHtml = '';
                    if (r.reasons) {
                        for (let res of r.reasons) reasonsHtml += '<li>' + res + '</li>';
                    }
                    if (r.counter_reasons) {
                        for (let cr of r.counter_reasons) reasonsHtml += '<li style="color:var(--orange)">' + cr + '</li>';
                    }
                    
                    html += '<div style="margin-bottom:12px; border-bottom:1px solid var(--border); padding-bottom:10px;">';
                    html += '<div style="display:flex; justify-content:space-between; align-items:center;">';
                    html += '<div style="display:flex; gap:10px; align-items:center;"><b>' + r.symbol + '</b>' + createSparkline(r.spark.close, color) + '</div>';
                    html += '<div style="text-align:right;"><div style="font-weight:bold; color:' + color + ';">' + r.confidence + '% Conf</div><div style="font-size:9px; color:#a1a1aa; margin-top:3px;"><span style="color:'+color+'">' + statusText + '</span></div></div>';
                    html += '</div>';
                    html += '<div style="margin-top:8px;">';
                    // NO BACKSLASHES USED HERE
                    html += '<span class="ai-btn" onclick="togglePanel(';
                    html += "'ai-" + r.symbol + "'";
                    html += ')">Breakdown ▾</span></div>';
                    html += '<div id="ai-' + r.symbol + '" class="ai-panel"><ul>' + reasonsHtml + '</ul></div>';
                    html += '</div>';
                }
                rankedContainer.innerHTML = html;
            } else {
                rankedContainer.innerHTML = '<div style="padding:15px; text-align:center; color:var(--orange); border: 1px dashed var(--border); border-radius: 8px;">📡 Engine Sweeping Universe...<br><span style="font-size:10px; color:#a1a1aa;">Analyzing Daily Moving Averages</span></div>';
            }
            
            const logsContainer = document.getElementById("logs");
            if (data.activity && data.activity.length > 0) {
                let html = '';
                for (let a of data.activity) {
                    html += '<div style="padding:5px 0; border-bottom:1px solid #1f2937;">' + a + '</div>';
                }
                logsContainer.innerHTML = html;
            }
            
        } catch (error) { 
            console.error("Fetch Error:", error); 
            document.getElementById("status").innerText = "RECONNECTING...";
            document.getElementById("status").style.background = "var(--red)";
        }
    }

    setInterval(fetchData, 4000);
    fetchData();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
