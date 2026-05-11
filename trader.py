"""
AUTO TRADER BOT v5.1 — Bull + Bear + Crypto 24/7
• Trailing stop-loss — moves up as price rises to lock in profits
• Crypto trading (BTC, ETH, SOL, DOGE etc) — trades 24/7 on weekends
• Auto-switches between stocks and crypto based on market hours
• 5 bull + 3 bear swing strategies
• Market regime detector (bull/bear/neutral)
• Persistent 30-day login
• Paper trading — no real money
"""

# ─────────────────────────────────────────────────────────
#  🔑  API KEYS
# ─────────────────────────────────────────────────────────
API_KEY    = "PKNTVAEYUN4FR2IHE2PGV4P242"
API_SECRET = "3V8CPotzwLSU8QHyhyU6XrdbGfxt1FemYM5GUwfpyTWA"
BASE_URL   = "https://paper-api.alpaca.markets"

# ─────────────────────────────────────────────────────────
#  🔒  LOGIN
# ─────────────────────────────────────────────────────────
DASH_USERNAME = "admin"
DASH_PASSWORD = "trader123"

# ─────────────────────────────────────────────────────────
#  ⚙️  SETTINGS
# ─────────────────────────────────────────────────────────
import os
DASHBOARD_PORT = int(os.environ.get("PORT", 7777))

# Stock watchlists
BULL_SWING = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","JPM","V","MA","UNH",
    "SPY","QQQ","IWM","XLK","XLF","XLE","GLD","TLT",
    "SHOP","CRWD","SNOW","PANW","NET","DDOG","SQ","COIN",
]
BEAR_ETF = ["SQQQ","SPXS","SOXS","TZA","UVXY","GLD","TLT"]
SHORT_CANDIDATES = [
    "TSLA","NVDA","AMD","META","AMZN","GOOGL","AAPL","MSFT",
    "COIN","PLTR","RIVN","LCID","NIO","BABA","GME","AMC",
]
DAY_WATCHLIST = [
    "TSLA","NVDA","AMD","TQQQ","SQQQ","SPY","QQQ",
    "AAPL","MSFT","META","AMZN","PLTR","RIVN","GME",
    "MARA","RIOT","COIN","SPXS","SOXS","UVXY",
]

# ── CRYPTO watchlist — trades 24/7 including weekends ──
# Alpaca uses symbol/USD format for crypto
CRYPTO_WATCHLIST = [
    "BTC/USD",   # Bitcoin
    "ETH/USD",   # Ethereum
    "SOL/USD",   # Solana
    "DOGE/USD",  # Dogecoin
    "AVAX/USD",  # Avalanche
    "LINK/USD",  # Chainlink
    "MATIC/USD", # Polygon
    "UNI/USD",   # Uniswap
]

MAX_POSITIONS   = 4
MAX_SHORT_SLOTS = 2
MAX_CRYPTO_SLOTS= 3   # max crypto positions at once

# Trailing stop settings
# Once price moves this % in our favour, stop starts trailing
TRAIL_ACTIVATE_PCT = 1.5   # activate trailing after 1.5% gain
TRAIL_DISTANCE_PCT  = 1.0  # trail 1% below highest price seen

DAY_CFG = {
    "timeframe":"1Min","bars":60,"interval_sec":60,
    "min_score":3,"atr_sl_mult":1.5,"atr_tp_mult":2.5,"label":"Day Trading",
}
SWING_CFG = {
    "timeframe":"15Min","bars":80,"interval_sec":300,
    "min_score":2,"atr_sl_mult":2.0,"atr_tp_mult":3.5,"label":"Swing Trading",
}
CRYPTO_CFG = {
    "timeframe":"15Min","bars":60,"interval_sec":300,
    "min_score":2,"atr_sl_mult":2.5,"atr_tp_mult":4.0,"label":"Crypto",
}
MIN_CONFIDENCE = 0.55

# ─────────────────────────────────────────────────────────
#  AUTO-INSTALL
# ─────────────────────────────────────────────────────────
import subprocess, sys
for pkg in ["requests","pandas","flask","flask_cors"]:
    try: __import__(pkg)
    except ImportError:
        subprocess.check_call([sys.executable,"-m","pip","install",pkg.replace("_","-"),"-q"])

import time, logging, datetime, threading, secrets
import requests, pandas as pd
from flask import Flask, jsonify, request as freq, redirect, session
from flask_cors import CORS

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
#  STRATEGY PERFORMANCE
# ─────────────────────────────────────────────────────────
STRATEGIES = [
    "trend_follow","dip_buy","breakout","mean_revert","vwap_bounce",
    "bear_short","bear_inverse_etf","bear_momentum",
    "crypto_momentum","crypto_dip",
]

def blank_perf():
    return {s:{"wins":0,"losses":0,"total_pl":0.0,"trades":0} for s in STRATEGIES}

def strategy_winrate(perf, s):
    p=perf.get(s,{}); t=p.get("trades",0)
    return p.get("wins",0)/t if t>=3 else 0.5

def record_strategy_result(strategy, pl):
    with LOCK:
        p=STATE["strategy_perf"]
        if strategy not in p: p[strategy]={"wins":0,"losses":0,"total_pl":0.0,"trades":0}
        p[strategy]["trades"]+=1
        p[strategy]["total_pl"]=round(p[strategy]["total_pl"]+pl,2)
        if pl>0: p[strategy]["wins"]+=1
        else:    p[strategy]["losses"]+=1

# ─────────────────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────────────────
STATE = {
    "mode":"swing","equity":0,"cash":0,"buying_power":0,
    "starting_equity":None,"market_open":False,"crypto_mode":False,
    "positions":[],"watchlist":[],"recent_actions":[],
    "last_update":None,"cycle_count":0,"next_check_sec":0,
    "stop_losses":{},"take_profits":{},"entry_prices":{},
    "highest_prices":{},   # tracks highest price seen for trailing stop
    "entry_strategy":{},"entry_side":{},"entry_asset_class":{},  # "stock"|"crypto"
    "status_log":[],"last_error":None,"api_ok":None,
    "scoring_progress":"",
    "market_regime":"unknown","spy_momentum":0.0,
    "strategy_perf":blank_perf(),
    "trailing_stops":{},   # {symbol: current_trailing_stop_price}
    # ── User controls (settable from /controls page) ──
    "trade_stocks":True,    # enable/disable stock trading
    "trade_crypto":True,    # enable/disable crypto trading
    "trade_day":True,       # enable day trading mode
    "trade_swing":True,     # enable swing trading mode
    "bot_paused":False,     # pause all trading entirely
    "custom_watchlist":[],  # user-added stocks from control panel
    "custom_crypto":[],     # user-added crypto from control panel
}
LOCK = threading.Lock()

def push(msg, level="info"):
    e={"ts":datetime.datetime.now().strftime("%H:%M:%S"),"msg":msg,"level":level}
    log.info(f"[{level.upper()}] {msg}")
    with LOCK:
        STATE["status_log"].append(e)
        if len(STATE["status_log"])>100: STATE["status_log"]=STATE["status_log"][-100:]
        if level=="error": STATE["last_error"]=msg

# ─────────────────────────────────────────────────────────
#  ALPACA
# ─────────────────────────────────────────────────────────
HDR={"APCA-API-KEY-ID":API_KEY,"APCA-API-SECRET-KEY":API_SECRET}

def aget(path,params=None):
    r=requests.get(BASE_URL+path,headers=HDR,params=params,timeout=10)
    r.raise_for_status(); return r.json()

def apost(path,data):
    r=requests.post(BASE_URL+path,headers=HDR,json=data,timeout=10)
    r.raise_for_status(); return r.json()

def get_account():   return aget("/v2/account")
def get_positions(): return aget("/v2/positions")

def get_clock():
    try:    c=aget("/v2/clock"); return c.get("is_open",False),c
    except: return False,{}

def get_bars(symbol, timeframe="15Min", limit=80, crypto=False):
    """Fetch bars for stocks OR crypto — same function, different endpoint."""
    if crypto:
        # Crypto uses a different data endpoint
        sym_encoded=symbol.replace("/","%2F")
        url=f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params={"symbols":symbol,"timeframe":timeframe,"limit":limit}
    else:
        url=f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        params={"timeframe":timeframe,"limit":limit,"adjustment":"raw"}
    try:
        r=requests.get(url,headers=HDR,params=params,timeout=8)
        if r.status_code not in(200,422): return None
        data=r.json()
        if crypto:
            bars=data.get("bars",{}).get(symbol,[])
        else:
            bars=data.get("bars",[])
        if not bars: return None
        df=pd.DataFrame(bars)
        df["t"]=pd.to_datetime(df["t"])
        # Crypto uses vw for vwap, ensure consistent column names
        if "vw" in df.columns and "v" not in df.columns:
            df["v"]=df.get("v",1)
        return df.set_index("t").sort_index()
    except: return None

def place_order(symbol, qty, side, short=False, crypto=False):
    tif="gtc" if (short or crypto) else "day"
    try:
        order={"symbol":symbol,"qty":str(qty if crypto else int(qty)),
               "side":side,"type":"market","time_in_force":tif}
        apost("/v2/orders",order)
        icon="🔶" if crypto else ("📉" if side=="sell" and not short else "🔻" if short else "📈")
        push(f"{icon} {side.upper()} {qty}x {symbol} {'[SHORT]' if short else '[CRYPTO]' if crypto else ''}","success")
        return True
    except Exception as e:
        push(f"Order failed {symbol}: {e}","error"); return False

def get_current_price(symbol, crypto=False):
    """Get latest price for a symbol."""
    try:
        if crypto:
            r=requests.get("https://data.alpaca.markets/v1beta3/crypto/us/latest/bars",
                headers=HDR,params={"symbols":symbol},timeout=5)
            if r.status_code==200:
                return float(r.json()["bars"][symbol]["c"])
        else:
            r=requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars/latest",
                headers=HDR,timeout=5)
            if r.status_code==200:
                return float(r.json()["bar"]["c"])
    except: pass
    return None

# ─────────────────────────────────────────────────────────
#  TRAILING STOP UPDATER
#  Runs in background every 30 seconds
#  Moves stop-loss UP as price rises to lock in profits
# ─────────────────────────────────────────────────────────
def update_trailing_stops():
    """
    Trailing stop logic:
    - Once a position is up TRAIL_ACTIVATE_PCT% from entry, we start trailing
    - The stop trails TRAIL_DISTANCE_PCT% below the highest price ever seen
    - If price rises more, stop rises with it — locking in more profit
    - Stop NEVER moves down — only up
    Example: Buy AAPL at $100
      Price rises to $102 (2% gain) → trailing activates
      Stop set at $102 * (1 - 0.01) = $100.98
      Price rises to $105 → stop moves to $105 * 0.99 = $103.95
      Price drops to $103 → stop triggers at $103.95 → SELL with ~$3.95 profit locked in
    """
    while True:
        try:
            with LOCK:
                positions=list(STATE["positions"])
                entry_prices=dict(STATE["entry_prices"])
                highest=dict(STATE["highest_prices"])
                sl_map=dict(STATE["stop_losses"])
                entry_side=dict(STATE["entry_side"])
                asset_class=dict(STATE["entry_asset_class"])

            for pos in positions:
                sym=pos["symbol"]
                entry=entry_prices.get(sym)
                if not entry: continue
                is_short=entry_side.get(sym,"long")=="short"
                is_crypto=asset_class.get(sym,"stock")=="crypto"
                cur=pos["current"]
                if not cur or cur<=0: continue

                if is_short:
                    # For shorts: trail DOWN as price falls
                    # Track LOWEST price seen
                    lowest_seen=highest.get(sym, cur)
                    if cur < lowest_seen:
                        lowest_seen=cur
                        with LOCK: STATE["highest_prices"][sym]=lowest_seen
                    gain_pct=(entry-cur)/entry*100
                    if gain_pct >= TRAIL_ACTIVATE_PCT:
                        new_trail=round(lowest_seen*(1+TRAIL_DISTANCE_PCT/100),2)
                        old_sl=sl_map.get(sym,999999)
                        if new_trail < old_sl:  # only move stop DOWN for shorts
                            with LOCK:
                                STATE["stop_losses"][sym]=new_trail
                                STATE["trailing_stops"][sym]=new_trail
                            push(f"📍 Trail SL {sym} [short] → ${new_trail} (locked gain)","info")
                else:
                    # For longs: trail UP as price rises
                    highest_seen=highest.get(sym, cur)
                    if cur > highest_seen:
                        highest_seen=cur
                        with LOCK: STATE["highest_prices"][sym]=highest_seen
                    gain_pct=(highest_seen-entry)/entry*100
                    if gain_pct >= TRAIL_ACTIVATE_PCT:
                        new_trail=round(highest_seen*(1-TRAIL_DISTANCE_PCT/100),2)
                        old_sl=sl_map.get(sym,0)
                        if new_trail > old_sl:  # only move stop UP for longs
                            with LOCK:
                                STATE["stop_losses"][sym]=new_trail
                                STATE["trailing_stops"][sym]=new_trail
                            push(f"📍 Trail SL {sym} → ${new_trail} (locked gain)","info")
        except Exception as e:
            push(f"Trail stop error: {e}","warn")
        time.sleep(30)

# ─────────────────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────────────────
def calc_rsi(c,p=14):
    d=c.diff(); g=d.clip(lower=0).rolling(p).mean()
    l=(-d.clip(upper=0)).rolling(p).mean()
    return 100-(100/(1+g/l.replace(0,1e-10)))

def calc_atr(df,p=14):
    h,l,c=df["h"],df["l"],df["c"]
    tr=pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    v=tr.rolling(p).mean().iloc[-1]
    return float(v) if pd.notna(v) else None

def calc_vwap(df):
    tp=(df["h"]+df["l"]+df["c"])/3
    vol=df["v"] if "v" in df.columns else pd.Series([1]*len(df),index=df.index)
    return (tp*vol).cumsum()/vol.cumsum()

def calc_macd(c):
    e12=c.ewm(span=12,adjust=False).mean()
    e26=c.ewm(span=26,adjust=False).mean()
    macd=e12-e26; sig=macd.ewm(span=9,adjust=False).mean()
    return macd,sig

def calc_bb(c,p=20):
    ma=c.rolling(p).mean(); std=c.rolling(p).std()
    return ma+2*std,ma,ma-2*std

def calc_sr(df,lb=20):
    return float(df["h"].rolling(lb).max().iloc[-1]),float(df["l"].rolling(lb).min().iloc[-1])

# ─────────────────────────────────────────────────────────
#  MARKET REGIME
# ─────────────────────────────────────────────────────────
def detect_market_regime():
    df=get_bars("SPY","15Min",60)
    if df is None or len(df)<10: return "unknown",0.0
    c=df["c"]
    ma10=c.rolling(min(10,len(c))).mean()
    ma20=c.rolling(min(20,len(c))).mean()
    rsi=calc_rsi(c); macd,sig=calc_macd(c)
    price=float(c.iloc[-1])
    mom=(price-float(c.iloc[-5]))/float(c.iloc[-5])*100
    chk=[price>float(ma10.iloc[-1]),float(ma10.iloc[-1])>float(ma20.iloc[-1]),
         float(rsi.iloc[-1])>50,float(macd.iloc[-1])>float(sig.iloc[-1]),mom>0]
    score=sum(chk)/len(chk)
    regime="bull" if score>=0.6 else "bear" if score<=0.35 else "neutral"
    push(f"Market: {regime.upper()} score={score:.0%} SPY mom={mom:.2f}%",
         "success" if regime=="bull" else "warn" if regime=="bear" else "info")
    return regime,round(mom,2)

# ─────────────────────────────────────────────────────────
#  BULL STRATEGIES
# ─────────────────────────────────────────────────────────
def strat_trend_follow(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    ma20=c.rolling(20).mean(); ma50=c.rolling(min(50,len(c))).mean(); ma5=c.rolling(5).mean()
    rsi=calc_rsi(c); macd,sig=calc_macd(c)
    vol=v.iloc[-3:].mean()/(v.mean()+1e-10)
    chk=[price>float(ma20.iloc[-1]),price>float(ma50.iloc[-1]),
         float(ma5.iloc[-1])>float(ma20.iloc[-1]),float(macd.iloc[-1])>float(sig.iloc[-1]),
         40<float(rsi.iloc[-1])<70,float(vol)>1.0]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"trend_follow")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"above_ma20":chk[0],
        "golden_cross":chk[2],"macd_bull":chk[3],"vol_surge":round(float(vol),2)}

def strat_dip_buy(df,price,atr_val,perf):
    if len(df)<50: return 0.0,{"error":"need 50 bars"}
    c=df["c"]; ma20=c.rolling(20).mean(); ma50=c.rolling(50).mean()
    rsi=calc_rsi(c); _,_,bb_low=calc_bb(c)
    slope=(float(ma50.iloc[-1])-float(ma50.iloc[-5]))/float(ma50.iloc[-5])*100
    chk=[float(rsi.iloc[-1])<45,price<float(ma20.iloc[-1]),price>float(ma50.iloc[-1]),
         slope>0,price<=float(bb_low.iloc[-1])*1.02,
         float(df["c"].iloc[-1])>float(df["o"].iloc[-1])]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"dip_buy")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"below_20ma":chk[1],
        "above_50ma":chk[2],"trend_intact":chk[3],"at_bb_low":chk[4]}

def strat_breakout(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    resist,_=calc_sr(df)
    recent_range=df["h"].iloc[-10:].max()-df["l"].iloc[-10:].min()
    avg_range=(df["h"]-df["l"]).mean(); tight=recent_range<avg_range*0.8
    vol=v.iloc[-3:].mean()/(v.mean()+1e-10); rsi=calc_rsi(c); macd,sig=calc_macd(c)
    chk=[price>=resist*0.995,float(vol)>1.5,tight,
         float(rsi.iloc[-1])>50,float(macd.iloc[-1])>float(sig.iloc[-1])]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"breakout")*0.3
    return round(conf,3),{"resistance":round(float(resist),2),"at_breakout":chk[0],
        "vol_surge":round(float(vol),2),"tight":bool(tight),"rsi":round(float(rsi.iloc[-1]),1)}

def strat_mean_revert(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    _,_,bb_low=calc_bb(c); rsi=calc_rsi(c)
    ma50=c.rolling(min(50,len(c))).mean()
    dev=(price-float(bb_low.iloc[-1]))/float(bb_low.iloc[-1])*100
    slope=(float(ma50.iloc[-1])-float(ma50.iloc[-10]))/float(ma50.iloc[-10])*100
    chk=[dev<-1.0,float(rsi.iloc[-1])<35,slope>-3.0,
         float(df["c"].iloc[-1])>float(df["o"].iloc[-1]),
         float(v.iloc[-1])>float(v.mean())*1.3]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"mean_revert")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"bb_dev":round(float(dev),2),
        "not_crashing":chk[2],"reversal_candle":chk[3]}

def strat_vwap_bounce(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    vwap=calc_vwap(df); rsi=calc_rsi(c); ma20=c.rolling(20).mean()
    vn=float(vwap.iloc[-1]); near=abs(price-vn)/vn<0.01
    was_above=float(c.iloc[-5])>vn*1.005; vol_inc=float(v.iloc[-1])>float(v.iloc[-3:].mean())
    chk=[near,was_above,price>float(ma20.iloc[-1]),vol_inc,40<float(rsi.iloc[-1])<65]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"vwap_bounce")*0.3
    return round(conf,3),{"vwap":round(float(vn),2),"near_vwap":chk[0],
        "was_above":chk[1],"vol_inc":chk[3],"rsi":round(float(rsi.iloc[-1]),1)}

# ─────────────────────────────────────────────────────────
#  BEAR STRATEGIES
# ─────────────────────────────────────────────────────────
def strat_bear_short(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    ma20=c.rolling(20).mean(); ma50=c.rolling(min(50,len(c))).mean(); ma5=c.rolling(5).mean()
    rsi=calc_rsi(c); macd,sig=calc_macd(c)
    vol=float(v.iloc[-3:].mean()/(v.mean()+1e-10))
    chk=[price<float(ma20.iloc[-1]),price<float(ma50.iloc[-1]),
         float(ma5.iloc[-1])<float(ma20.iloc[-1]),
         float(macd.iloc[-1])<float(sig.iloc[-1]),
         float(rsi.iloc[-1])<50,vol>1.0]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"bear_short")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"below_ma20":chk[0],
        "death_cross":chk[2],"macd_bear":chk[3]}

def strat_bear_inverse_etf(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    ma10=c.rolling(min(10,len(c))).mean(); ma5=c.rolling(5).mean()
    rsi=calc_rsi(c); mom=(float(c.iloc[-1])-float(c.iloc[-10]))/float(c.iloc[-10])*100
    vol=float(v.iloc[-3:].mean()/(v.mean()+1e-10))
    chk=[price>float(ma10.iloc[-1]),float(ma5.iloc[-1])>float(ma10.iloc[-1]),
         mom>0,float(rsi.iloc[-1])<70,vol>1.2]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"bear_inverse_etf")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"trending_up":chk[0],
        "momentum_pct":round(float(mom),2)}

def strat_bear_momentum(df,price,atr_val,perf):
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    rsi=calc_rsi(c); _,_,bb_low=calc_bb(c); macd,sig=calc_macd(c)
    mom5=(float(c.iloc[-1])-float(c.iloc[-5]))/float(c.iloc[-5])*100
    mom10=(float(c.iloc[-1])-float(c.iloc[-10]))/float(c.iloc[-10])*100
    chk=[float(rsi.iloc[-1])<45,price<float(bb_low.iloc[-1]),
         float(macd.iloc[-1])<float(sig.iloc[-1]),mom5<mom10,
         float(v.iloc[-3:].mean())>float(v.mean())*1.3,
         float(df["c"].iloc[-1])<float(df["o"].iloc[-1])]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"bear_momentum")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"below_bb":chk[1],
        "macd_bear":chk[2],"accelerating":chk[3]}

# ─────────────────────────────────────────────────────────
#  CRYPTO STRATEGIES
#  Crypto is more volatile so we use slightly looser signals
#  and wider ATR multipliers (set in CRYPTO_CFG)
# ─────────────────────────────────────────────────────────
def strat_crypto_momentum(df,price,atr_val,perf):
    """
    CRYPTO MOMENTUM — crypto trends hard and fast.
    Catches coins already in a strong uptrend with volume.
    Uses shorter MAs because crypto moves faster than stocks.
    """
    c=df["c"]
    ma10=c.rolling(min(10,len(c))).mean()
    ma20=c.rolling(min(20,len(c))).mean()
    ma5=c.rolling(5).mean()
    rsi=calc_rsi(c); macd,sig=calc_macd(c)
    mom=(float(c.iloc[-1])-float(c.iloc[-6]))/float(c.iloc[-6])*100
    chk=[price>float(ma10.iloc[-1]),
         price>float(ma20.iloc[-1]),
         float(ma5.iloc[-1])>float(ma10.iloc[-1]),
         float(macd.iloc[-1])>float(sig.iloc[-1]),
         30<float(rsi.iloc[-1])<75,
         mom>0]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"crypto_momentum")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"above_ma10":chk[0],
        "golden_cross":chk[2],"macd_bull":chk[3],"momentum_pct":round(float(mom),2)}

def strat_crypto_dip(df,price,atr_val,perf):
    """
    CRYPTO DIP BUY — crypto dips hard and bounces hard.
    Buys oversold crypto that still has a healthy long-term trend.
    RSI threshold is looser than stocks because crypto is more volatile.
    """
    if len(df)<20: return 0.0,{}
    c=df["c"]
    ma20=c.rolling(min(20,len(c))).mean()
    ma10=c.rolling(min(10,len(c))).mean()
    rsi=calc_rsi(c); _,_,bb_low=calc_bb(c,p=min(20,len(c)))
    slope=(float(ma10.iloc[-1])-float(ma10.iloc[-5]))/float(ma10.iloc[-5])*100
    chk=[float(rsi.iloc[-1])<40,
         price<float(ma20.iloc[-1]),
         slope>-5.0,
         price<=float(bb_low.iloc[-1])*1.03,
         float(df["c"].iloc[-1])>float(df["o"].iloc[-1])]
    conf=sum(chk)/len(chk)*0.7+strategy_winrate(perf,"crypto_dip")*0.3
    return round(conf,3),{"rsi":round(float(rsi.iloc[-1]),1),"below_ma20":chk[1],
        "trend_ok":chk[2],"at_bb_low":chk[3],"reversal_candle":chk[4]}

def analyse_crypto(symbol, perf):
    """Score a crypto symbol using both crypto strategies, pick best."""
    df=get_bars(symbol, CRYPTO_CFG["timeframe"], CRYPTO_CFG["bars"], crypto=True)
    if df is None or len(df)<15: return None,0,None,None,{}
    price=round(float(df["c"].iloc[-1]),4)
    atr_val=calc_atr(df)
    results={}
    for name,fn in [("crypto_momentum",strat_crypto_momentum),("crypto_dip",strat_crypto_dip)]:
        try: results[name]=fn(df,price,atr_val,perf)
        except: results[name]=(0.0,{})
    best=max(results.items(),key=lambda x:x[1][0])
    return best[0],best[1][0],price,atr_val,results

# ─────────────────────────────────────────────────────────
#  STOCK ANALYSERS
# ─────────────────────────────────────────────────────────
def analyse_bull(symbol,cfg,perf):
    df=get_bars(symbol,cfg["timeframe"],cfg["bars"])
    if df is None or len(df)<20: return None,0,None,None,{}
    price=round(float(df["c"].iloc[-1]),2); atr_val=calc_atr(df)
    fns={"trend_follow":strat_trend_follow,"dip_buy":strat_dip_buy,
         "breakout":strat_breakout,"mean_revert":strat_mean_revert,"vwap_bounce":strat_vwap_bounce}
    results={}
    for name,fn in fns.items():
        try: results[name]=fn(df,price,atr_val,perf)
        except: results[name]=(0.0,{})
    best=max(results.items(),key=lambda x:x[1][0])
    return best[0],best[1][0],price,atr_val,results

def analyse_bear_etf(symbol,cfg,perf):
    df=get_bars(symbol,cfg["timeframe"],cfg["bars"])
    if df is None or len(df)<20: return None,0,None,None,{}
    price=round(float(df["c"].iloc[-1]),2); atr_val=calc_atr(df)
    try: conf,sigs=strat_bear_inverse_etf(df,price,atr_val,perf)
    except: conf,sigs=0.0,{}
    return "bear_inverse_etf",conf,price,atr_val,{"bear_inverse_etf":(conf,sigs)}

def analyse_short(symbol,cfg,perf):
    df=get_bars(symbol,cfg["timeframe"],cfg["bars"])
    if df is None or len(df)<20: return None,0,None,None,{}
    price=round(float(df["c"].iloc[-1]),2); atr_val=calc_atr(df)
    results={}
    for name,fn in [("bear_short",strat_bear_short),("bear_momentum",strat_bear_momentum)]:
        try: results[name]=fn(df,price,atr_val,perf)
        except: results[name]=(0.0,{})
    best=max(results.items(),key=lambda x:x[1][0])
    return best[0],best[1][0],price,atr_val,results

def analyse_day(symbol,perf):
    df=get_bars(symbol,DAY_CFG["timeframe"],DAY_CFG["bars"])
    if df is None or len(df)<20: return None,0,None,None,{}
    price=round(float(df["c"].iloc[-1]),2); atr_val=calc_atr(df)
    c=df["c"]; v=df.get("v",pd.Series([1]*len(df)))
    ma20=c.rolling(20).mean(); ma5=c.rolling(5).mean()
    rsi=calc_rsi(c); macd,sig=calc_macd(c)
    mom=(float(c.iloc[-1])-float(c.iloc[-10]))/float(c.iloc[-10])*100
    vol=float(v.iloc[-5:].mean()/(v.mean()+1e-10))
    chk=[price>float(ma20.iloc[-1]),float(ma5.iloc[-1])>float(ma20.iloc[-1]),
         mom>0,float(rsi.iloc[-1])<70,float(macd.iloc[-1])>float(sig.iloc[-1]),vol>1.3]
    conf=min(sum(chk),4)/6.0
    sigs={"rsi":round(float(rsi.iloc[-1]),1),"momentum_pct":round(float(mom),2),"vol":round(float(vol),2)}
    return "day_momentum",round(conf,3),price,atr_val,{"day_momentum":(round(conf,3),sigs)}

# ─────────────────────────────────────────────────────────
#  RECORDS + P&L
# ─────────────────────────────────────────────────────────
def record(side,sym,qty,price,pl=None,reason="signal",strategy=None,direction="long"):
    now=datetime.datetime.now()
    with LOCK:
        STATE["recent_actions"].append({
            "ts":now.isoformat(),"side":side,"symbol":sym,"qty":qty,
            "price":price,"pl":pl,"reason":reason,"strategy":strategy or "",
            "direction":direction,
            "label":f"{now.strftime('%H:%M:%S')} {side} {qty}x{sym} @ ${price} [{reason}]"
        })
        if len(STATE["recent_actions"])>500:
            STATE["recent_actions"]=STATE["recent_actions"][-500:]

def pl_windows():
    now=datetime.datetime.now()
    sells=[a for a in STATE["recent_actions"]
           if a["side"] in("SELL","BUY_TO_COVER") and a.get("pl") is not None]
    def sp(lst):
        g=sum(a["pl"] for a in lst if a["pl"]>0); lo=sum(a["pl"] for a in lst if a["pl"]<0)
        return{"gain":round(g,2),"loss":round(lo,2),"net":round(g+lo,2),
               "win_trades":sum(1 for a in lst if a["pl"]>0),
               "loss_trades":sum(1 for a in lst if a["pl"]<0)}
    def af(dt): return[a for a in sells if datetime.datetime.fromisoformat(a["ts"])>=dt]
    td=now.replace(hour=0,minute=0,second=0,microsecond=0)
    return{"hour":sp(af(now-datetime.timedelta(hours=1))),"today":sp(af(td)),
           "week":sp(af(td-datetime.timedelta(days=now.weekday()))),
           "year":sp(af(now.replace(month=1,day=1,hour=0,minute=0,second=0,microsecond=0))),
           "all":sp(sells)}

def sl_tp(entry,atr_val,cfg,short=False):
    if not atr_val or atr_val<=0:
        sl=round(entry*1.02,2) if short else round(entry*.98,2)
        tp=round(entry*.94,2) if short else round(entry*1.04,2)
        return sl,tp
    sl=(round(entry+cfg["atr_sl_mult"]*atr_val,2) if short
        else round(entry-cfg["atr_sl_mult"]*atr_val,2))
    tp=(round(entry-cfg["atr_tp_mult"]*atr_val,2) if short
        else round(entry+cfg["atr_tp_mult"]*atr_val,2))
    return sl,tp

def close_position(sym,pos,is_short,su,price,reason):
    qty=float(pos["qty"]); avg=float(pos["avg_entry_price"])
    pl_val=round(((avg-price) if is_short else (price-avg))*qty,2)
    close_side="buy" if is_short else "sell"
    is_crypto=STATE["entry_asset_class"].get(sym,"stock")=="crypto"
    if place_order(sym,qty if is_crypto else int(qty),close_side,short=is_short,crypto=is_crypto):
        record("BUY_TO_COVER" if is_short else "SELL",sym,qty,price,pl_val,reason,su,
               "short" if is_short else "long")
        record_strategy_result(su,pl_val)
        with LOCK:
            for d in [STATE["stop_losses"],STATE["take_profits"],STATE["entry_strategy"],
                      STATE["entry_side"],STATE["entry_prices"],STATE["highest_prices"],
                      STATE["trailing_stops"],STATE["entry_asset_class"]]:
                d.pop(sym,None)
        return True
    return False

# ─────────────────────────────────────────────────────────
#  MAIN TRADE CYCLE
# ─────────────────────────────────────────────────────────
def run_cycle():
    with LOCK: cfg_name=STATE["mode"]
    cfg=DAY_CFG if cfg_name=="day" else SWING_CFG

    # Check user controls
    with LOCK:
        if STATE["bot_paused"]: push("Bot paused — skipping cycle","warn"); return
        trade_stocks=STATE["trade_stocks"]; trade_crypto=STATE["trade_crypto"]
        trade_day=STATE["trade_day"]; trade_swing=STATE["trade_swing"]
        cust_stocks=list(STATE["custom_watchlist"]); cust_crypto=list(STATE["custom_crypto"])
    # Add custom stocks to watchlists
    for s in cust_stocks:
        if s not in BULL_SWING: BULL_SWING.append(s)
        if s not in DAY_WATCHLIST: DAY_WATCHLIST.append(s)
    for c in cust_crypto:
        if c not in CRYPTO_WATCHLIST: CRYPTO_WATCHLIST.append(c)
    try:
        account=get_account()
        with LOCK: STATE["api_ok"]=True
    except Exception as e:
        push(f"API error: {e}","error")
        with LOCK: STATE["api_ok"]=False; return

    open_,clock=get_clock()

    try:
        positions=get_positions()
        eq=float(account["equity"]); cash=float(account["cash"]); bp=float(account["buying_power"])
        with LOCK:
            if STATE["starting_equity"] is None: STATE["starting_equity"]=eq
            STATE.update({"equity":eq,"cash":cash,"buying_power":bp,
                          "market_open":open_,"last_update":datetime.datetime.now().isoformat()})
            STATE["cycle_count"]+=1
            perf=dict(STATE["strategy_perf"])
    except Exception as e:
        push(f"Account error: {e}","error"); return

    # Decide what to trade
    crypto_only=not open_
    with LOCK: STATE["crypto_mode"]=crypto_only

    if crypto_only:
        push("📅 Stock market closed — running CRYPTO cycle 24/7","info")
        _run_crypto_cycle(positions,bp,perf)
    else:
        push(f"── Stock Cycle [{cfg['label']}] ──")
        regime,spy_mom=detect_market_regime()
        with LOCK: STATE["market_regime"]=regime; STATE["spy_momentum"]=spy_mom
        _run_stock_cycle(positions,bp,perf,regime,clock,cfg,cfg_name)

    positions=get_positions()
    # Update positions display with latest prices and trailing info
    _sync_positions(positions)
    push(f"Cycle done — {len(positions)} position(s)","success")

def _run_crypto_cycle(positions, bp, perf):
    """Trade crypto 24/7."""
    push(f"Analysing {len(CRYPTO_WATCHLIST)} crypto pairs…")
    held={p["symbol"] for p in positions}
    crypto_held=sum(1 for s in held if STATE["entry_asset_class"].get(s,"stock")=="crypto")
    crypto_slots=MAX_CRYPTO_SLOTS-crypto_held

    scored=[]
    for i,sym in enumerate(CRYPTO_WATCHLIST):
        with LOCK: STATE["scoring_progress"]=f"Crypto {sym} ({i+1}/{len(CRYPTO_WATCHLIST)})…"
        try:
            strat,conf,price,atr_val,results=analyse_crypto(sym,perf)
            scored.append((sym,strat,conf,price,atr_val,results))
            if price: push(f"  {sym}: {strat} {conf:.0%} ${price}")
            else: push(f"  {sym}: no data","warn")
        except Exception as e: push(f"  {sym}: {e}","warn")
        time.sleep(0.3)
    scored.sort(key=lambda x:-x[2])
    with LOCK: STATE["scoring_progress"]=""

    # Check SL/TP on existing crypto positions
    for pos in positions:
        sym=pos["symbol"]
        if STATE["entry_asset_class"].get(sym,"stock")!="crypto": continue
        cur=float(pos["current_price"]); su=STATE["entry_strategy"].get(sym,"unknown")
        sl_map=STATE["stop_losses"]; tp_map=STATE["take_profits"]
        is_short=False  # crypto long only for simplicity
        if sym in sl_map and cur<=sl_map[sym]:
            push(f"🛑 Crypto SL {sym} @ ${cur}","warn")
            close_position(sym,pos,is_short,su,cur,"stop-loss")
        elif sym in tp_map and cur>=tp_map[sym]:
            push(f"🎯 Crypto TP {sym} @ ${cur}","success")
            close_position(sym,pos,is_short,su,cur,"take-profit")

    # Update watchlist with crypto scores
    with LOCK:
        crypto_watch=[
            {"symbol":sym,"strategy":strat or "—","confidence":conf,"conf_pct":f"{conf:.0%}",
             "price":price,"atr":round(atr_val,2) if atr_val else None,
             "held":sym in held,"direction":"crypto"}
            for sym,strat,conf,price,atr_val,_ in scored]
        # Merge with existing stock watchlist entries
        existing=[w for w in STATE["watchlist"] if w.get("direction")!="crypto"]
        STATE["watchlist"]=crypto_watch+existing

    # Buy top crypto
    if crypto_slots>0 and bp>10:
        candidates=[(sym,strat,conf,price,atr_val)
                    for sym,strat,conf,price,atr_val,_ in scored
                    if conf>=MIN_CONFIDENCE and sym not in held and price and price>0][:crypto_slots]
        for sym,strat,conf,price,atr_val in candidates:
            alloc=bp/max(crypto_slots,1)
            # Crypto can be fractional — buy in USD value
            qty=round(alloc/price,6)
            if qty<=0: continue
            sl,tp=sl_tp(price,atr_val,CRYPTO_CFG)
            push(f"₿ BUY {sym} {qty} @ ${price} [{strat} {conf:.0%}] SL=${sl} TP=${tp}","success")
            if place_order(sym,qty,"buy",crypto=True):
                record("BUY",sym,qty,price,reason=strat,strategy=strat,direction="crypto")
                with LOCK:
                    STATE["stop_losses"][sym]=sl; STATE["take_profits"][sym]=tp
                    STATE["entry_prices"][sym]=price; STATE["entry_strategy"][sym]=strat
                    STATE["entry_side"][sym]="long"; STATE["entry_asset_class"][sym]="crypto"
                    STATE["highest_prices"][sym]=price

def _run_stock_cycle(positions,bp,perf,regime,clock,cfg,cfg_name):
    held={p["symbol"]:p for p in positions}
    with LOCK: sl_map=dict(STATE["stop_losses"]); tp_map=dict(STATE["take_profits"]); entry_strat=dict(STATE["entry_strategy"]); entry_side=dict(STATE["entry_side"])

    # Build stock watchlist
    if cfg_name=="day":
        wlist=[(sym,"long") for sym in DAY_WATCHLIST]
    elif regime=="bear":
        push("🐻 Bear market — shorts + inverse ETFs","warn")
        wlist=([(sym,"inverse") for sym in BEAR_ETF]+[(sym,"short") for sym in SHORT_CANDIDATES])
    elif regime=="bull":
        push("🐂 Bull market — long positions","success")
        wlist=[(sym,"long") for sym in BULL_SWING]
    else:
        wlist=([(sym,"long") for sym in BULL_SWING[:12]]+[(sym,"inverse") for sym in BEAR_ETF[:4]])

    push(f"Analysing {len(wlist)} stocks…")
    scored=[]
    for i,(sym,direction) in enumerate(wlist):
        with LOCK: STATE["scoring_progress"]=f"Analysing {sym} ({i+1}/{len(wlist)})…"
        try:
            if cfg_name=="day":
                strat,conf,price,atr_val,results=analyse_day(sym,perf); direction="long"
            elif direction=="long":
                strat,conf,price,atr_val,results=analyse_bull(sym,cfg,perf)
            elif direction=="inverse":
                strat,conf,price,atr_val,results=analyse_bear_etf(sym,cfg,perf)
            else:
                strat,conf,price,atr_val,results=analyse_short(sym,cfg,perf)
            scored.append((sym,strat,conf,price,atr_val,results,direction))
            if price: push(f"  {sym}[{direction}]: {strat} {conf:.0%} ${price}")
            else: push(f"  {sym}: no data","warn")
        except Exception as e: push(f"  {sym}: {e}","warn")
        time.sleep(0.25)
    scored.sort(key=lambda x:-x[2])
    with LOCK: STATE["scoring_progress"]=""

    # Update watchlist
    held_syms=set(held.keys())
    with LOCK:
        stock_watch=[
            {"symbol":sym,"strategy":strat or "—","confidence":conf,"conf_pct":f"{conf:.0%}",
             "price":price,"atr":round(atr_val,2) if atr_val else None,
             "held":sym in held_syms,"direction":direction}
            for sym,strat,conf,price,atr_val,_,direction in scored]
        crypto_watch=[w for w in STATE["watchlist"] if w.get("direction")=="crypto"]
        STATE["watchlist"]=stock_watch+crypto_watch

    # SL/TP checks on stock positions
    for sym,pos in list(held.items()):
        if STATE["entry_asset_class"].get(sym,"stock")=="crypto": continue
        try:
            cur=float(pos["current_price"]); su=entry_strat.get(sym,"unknown")
            is_short=entry_side.get(sym,"long")=="short"
            hit_sl=sym in sl_map and (cur>=sl_map[sym] if is_short else cur<=sl_map[sym])
            hit_tp=sym in tp_map and (cur<=tp_map[sym] if is_short else cur>=tp_map[sym])
            if hit_sl:
                push(f"🛑 SL {sym} @ ${cur}","warn")
                close_position(sym,pos,is_short,su,cur,"stop-loss")
            elif hit_tp:
                push(f"🎯 TP {sym} @ ${cur}","success")
                close_position(sym,pos,is_short,su,cur,"take-profit")
        except: pass

    positions=get_positions(); held={p["symbol"]:p for p in positions}

    # Signal exits
    for sym,pos in list(held.items()):
        if STATE["entry_asset_class"].get(sym,"stock")=="crypto": continue
        m=next((s for s in scored if s[0]==sym),None)
        if not m: continue
        _,strat,conf,price,atr_val,_,direction=m
        is_short=entry_side.get(sym,"long")=="short"
        if conf<MIN_CONFIDENCE*0.7:
            push(f"Signal exit {sym} conf={conf:.0%}","warn")
            close_position(sym,pos,is_short,entry_strat.get(sym,"unknown"),price or 0,"signal")

    # EOD (day mode)
    if cfg_name=="day":
        try:
            nc=clock.get("next_close","")
            if nc:
                ct=datetime.datetime.fromisoformat(nc.replace("Z","+00:00")).astimezone()
                mins=(ct-datetime.datetime.now().astimezone()).seconds//60
                if mins<=15:
                    push(f"⏰ {mins}min to close — EOD sell all stocks","warn")
                    for sym,pos in list(held.items()):
                        if STATE["entry_asset_class"].get(sym,"stock")=="crypto": continue
                        cur=float(pos["current_price"]); is_short=entry_side.get(sym,"long")=="short"
                        close_position(sym,pos,is_short,entry_strat.get(sym,"unknown"),cur,"end-of-day")
        except: pass

    positions=get_positions(); held={p["symbol"]:p for p in positions}

    # Count slots
    current_longs =sum(1 for s in held if entry_side.get(s,"long")=="long" and STATE["entry_asset_class"].get(s,"stock")=="stock")
    current_shorts=sum(1 for s in held if entry_side.get(s,"long")=="short")
    long_slots=MAX_POSITIONS-current_longs; short_slots=MAX_SHORT_SLOTS-current_shorts

    candidates=[(sym,strat,conf,price,atr_val,direction)
                for sym,strat,conf,price,atr_val,_,direction in scored
                if conf>=MIN_CONFIDENCE and sym not in held and price and price>0]

    for sym,strat,conf,price,atr_val,direction in candidates:
        is_short=direction=="short"
        if is_short and short_slots<=0: continue
        if not is_short and long_slots<=0: continue
        if bp<price: continue
        slots=short_slots if is_short else long_slots
        shares=int((bp/max(slots,1))/price)
        if shares<1: continue
        sl,tp=sl_tp(price,atr_val,cfg,short=is_short)
        push(f"{'SHORT' if is_short else 'BUY'} {sym} {shares}sh @ ${price} [{strat} {conf:.0%}]","success")
        order_side="sell" if is_short else "buy"
        if place_order(sym,shares,order_side,short=is_short):
            record("SHORT" if is_short else "BUY",sym,shares,price,reason=strat,strategy=strat,direction=direction)
            with LOCK:
                STATE["stop_losses"][sym]=sl; STATE["take_profits"][sym]=tp
                STATE["entry_prices"][sym]=price; STATE["entry_strategy"][sym]=strat
                STATE["entry_side"][sym]=direction; STATE["entry_asset_class"][sym]="stock"
                STATE["highest_prices"][sym]=price
            if is_short: short_slots-=1
            else: long_slots-=1

def _sync_positions(positions):
    with LOCK:
        sl_map=dict(STATE["stop_losses"]); tp_map=dict(STATE["take_profits"])
        trail=dict(STATE["trailing_stops"]); es=dict(STATE["entry_side"])
    pos_out=[]
    for p in positions:
        try:
            sym=p["symbol"]; is_short=es.get(sym,"long")=="short"
            cur=float(p["current_price"]); avg=float(p["avg_entry_price"]); qty=float(p["qty"])
            pl=round(((avg-cur) if is_short else (cur-avg))*qty,2)
            pos_out.append({"symbol":sym,"qty":qty,"avg_cost":avg,"current":cur,"pl":pl,
                "pl_pct":pl/max(avg*qty,1)*100,
                "stop_loss":sl_map.get(sym),"take_profit":tp_map.get(sym),
                "trailing":sym in trail,
                "strategy":STATE["entry_strategy"].get(sym,"—"),
                "direction":STATE["entry_asset_class"].get(sym,"stock")=="crypto" and "crypto" or es.get(sym,"long")})
        except: pass
    held={p["symbol"] for p in positions}
    with LOCK:
        STATE["positions"]=pos_out
        for w in STATE["watchlist"]: w["held"]=w["symbol"] in held

# ─────────────────────────────────────────────────────────
#  TRADING LOOP
# ─────────────────────────────────────────────────────────
def trading_loop():
    last_run=0
    while True:
        with LOCK: cfg_name=STATE["mode"]; paused=STATE["bot_paused"]
        if paused:
            with LOCK: STATE["next_check_sec"]=0
            time.sleep(5); continue
        interval=(DAY_CFG if cfg_name=="day" else SWING_CFG)["interval_sec"]
        elapsed=time.time()-last_run
        with LOCK: STATE["next_check_sec"]=int(max(0,interval-elapsed))
        if elapsed>=interval:
            last_run=time.time()
            try: run_cycle()
            except Exception as e: push(f"Loop error: {e}","error")
        time.sleep(1)

# ─────────────────────────────────────────────────────────
#  FLASK + LOGIN
# ─────────────────────────────────────────────────────────
app=Flask(__name__)
app.secret_key=secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"]=datetime.timedelta(days=30)
CORS(app)

def check_auth(): return session.get("logged_in") is True

LOGIN_PAGE="""<!DOCTYPE html><html><head><title>AutoTrader</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#07090f;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:'Segoe UI',sans-serif;}
.box{background:#0d1422;border:1px solid #1b2740;border-radius:14px;padding:36px;width:100%;max-width:360px;margin:16px;}
.logo{font-size:1.5rem;font-weight:900;color:#3b82f6;margin-bottom:4px;letter-spacing:-1px;}
.logo span{color:#f1f5f9;}.sub{color:#4e6280;font-size:.78rem;margin-bottom:6px;}
.paper{background:#f59e0b18;border:1px solid #f59e0b44;border-radius:6px;padding:7px 11px;color:#f59e0b;font-size:.68rem;margin-bottom:22px;}
label{display:block;color:#4e6280;font-size:.7rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;}
input[type=text],input[type=password]{width:100%;background:#131c2e;border:1px solid #1b2740;border-radius:8px;padding:11px 13px;color:#f1f5f9;font-size:.88rem;margin-bottom:14px;outline:none;}
input:focus{border-color:#3b82f6;}
.remember{display:flex;align-items:center;gap:8px;margin-bottom:16px;color:#4e6280;font-size:.75rem;}
button{width:100%;background:#3b82f6;border:none;border-radius:8px;padding:12px;color:#fff;font-size:.88rem;font-weight:700;cursor:pointer;}
.err{color:#f43f5e;font-size:.75rem;margin-bottom:12px;}
</style></head><body><div class="box">
<div class="logo">auto<span>trader</span></div>
<div class="sub">Bull + Bear + Crypto 24/7</div>
<div class="paper">📄 Paper Trading — No real money</div>
{err}
<form method="POST">
<label>Username</label><input name="username" type="text" autocomplete="username"/>
<label>Password</label><input name="password" type="password" autocomplete="current-password"/>
<div class="remember"><input type="checkbox" name="remember" value="1" checked/> Keep me logged in for 30 days</div>
<button type="submit">Sign In</button>
</form></div></body></html>"""

@app.route("/login",methods=["GET","POST"])
def login():
    if freq.method=="POST":
        u=freq.form.get("username",""); p=freq.form.get("password","")
        if u==DASH_USERNAME and p==DASH_PASSWORD:
            session.permanent=freq.form.get("remember","0")=="1"
            session["logged_in"]=True; return redirect("/")
        return LOGIN_PAGE.replace("{err}",'<div class="err">⚠ Wrong username or password</div>')
    return LOGIN_PAGE.replace("{err}","")

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/api/state")
def api_state():
    if not check_auth(): return jsonify({"error":"unauthorized"}),401
    with LOCK: d={k:v for k,v in STATE.items()}
    d["pl_windows"]=pl_windows()
    d["total_unrealized_pl"]=round(sum(p["pl"] for p in d["positions"]),2)
    d["equity_change"]=round((d["equity"]-d["starting_equity"]) if d["starting_equity"] else 0,2)
    d["mode_label"]=DAY_CFG["label"] if d["mode"]=="day" else SWING_CFG["label"]
    d["watchlist_size"]=len(d["watchlist"])
    return jsonify(d)

@app.route("/api/mode",methods=["POST"])
def api_mode():
    if not check_auth(): return jsonify({"error":"unauthorized"}),401
    m=freq.get_json().get("mode","swing")
    if m not in("day","swing"): return jsonify({"error":"invalid"}),400
    with LOCK: STATE["mode"]=m
    push(f"Mode → {m}","success"); return jsonify({"ok":True,"mode":m})

@app.route("/")
def index():
    if not check_auth(): return redirect("/login")
    return DASHBOARD_HTML

def run_flask():
    import logging as lg; lg.getLogger("werkzeug").setLevel(lg.ERROR)
    app.run(host="0.0.0.0",port=DASHBOARD_PORT,debug=False,use_reloader=False)

# ── NEW: manual sell endpoint ──
@app.route("/api/sell",methods=["POST"])
def api_sell():
    if not check_auth(): return jsonify({"error":"unauthorized"}),401
    b=freq.get_json(); sym=b.get("symbol","").upper()
    if not sym: return jsonify({"error":"no symbol"}),400
    try:
        positions=get_positions()
        pos=next((p for p in positions if p["symbol"]==sym),None)
        if not pos: return jsonify({"error":f"{sym} not held"}),404
        is_short=STATE["entry_side"].get(sym,"long")=="short"
        is_crypto=STATE["entry_asset_class"].get(sym,"stock")=="crypto"
        sell_qty=float(pos["qty"]); side="buy" if is_short else "sell"
        ok=place_order(sym,sell_qty,side,short=is_short,crypto=is_crypto)
        if ok:
            cur=float(pos["current_price"]); avg=float(pos["avg_entry_price"])
            pl=round(((avg-cur) if is_short else (cur-avg))*sell_qty,2)
            record("SELL",sym,sell_qty,cur,pl,"manual",STATE["entry_strategy"].get(sym,"manual"))
            with LOCK:
                for d in [STATE["stop_losses"],STATE["take_profits"],STATE["entry_strategy"],
                          STATE["entry_side"],STATE["entry_prices"],STATE["highest_prices"],
                          STATE["trailing_stops"],STATE["entry_asset_class"]]:
                    d.pop(sym,None)
            push(f"Manual sell {sym}","success")
            return jsonify({"ok":True,"pl":pl})
        return jsonify({"error":"order failed"}),500
    except Exception as e: return jsonify({"error":str(e)}),500

# ── NEW: controls API ──
@app.route("/api/controls",methods=["GET","POST"])
def api_controls():
    if not check_auth(): return jsonify({"error":"unauthorized"}),401
    if freq.method=="POST":
        b=freq.get_json()
        with LOCK:
            for key in ["trade_stocks","trade_crypto","trade_day","trade_swing","bot_paused"]:
                if key in b: STATE[key]=bool(b[key])
            if "add_stock" in b:
                sym=b["add_stock"].strip().upper()
                if sym and sym not in STATE["custom_watchlist"] and sym not in BULL_SWING:
                    STATE["custom_watchlist"].append(sym)
                    push(f"Added {sym} to watchlist","success")
            if "remove_stock" in b:
                sym=b["remove_stock"].strip().upper()
                STATE["custom_watchlist"]=[s for s in STATE["custom_watchlist"] if s!=sym]
                push(f"Removed {sym}","info")
            if "add_crypto" in b:
                sym=b["add_crypto"].strip().upper()
                if "/" not in sym: sym+="/USD"
                if sym not in STATE["custom_crypto"] and sym not in CRYPTO_WATCHLIST:
                    STATE["custom_crypto"].append(sym)
                    push(f"Added crypto {sym}","success")
            if "remove_crypto" in b:
                sym=b["remove_crypto"].strip().upper()
                if "/" not in sym: sym+="/USD"
                STATE["custom_crypto"]=[s for s in STATE["custom_crypto"] if s!=sym]
        return jsonify({"ok":True})
    with LOCK:
        return jsonify({
            "trade_stocks":STATE["trade_stocks"],"trade_crypto":STATE["trade_crypto"],
            "trade_day":STATE["trade_day"],"trade_swing":STATE["trade_swing"],
            "bot_paused":STATE["bot_paused"],
            "custom_watchlist":list(STATE["custom_watchlist"]),
            "custom_crypto":list(STATE["custom_crypto"]),
            "bull_swing":BULL_SWING,"day_watchlist":DAY_WATCHLIST,
            "crypto_watchlist":CRYPTO_WATCHLIST,"positions":list(STATE["positions"]),
        })

@app.route("/controls")
def controls_page():
    if not check_auth(): return redirect("/login")
    return CONTROLS_HTML


# ─────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────
DASHBOARD_HTML="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<title>AutoTrader</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Cabinet+Grotesk:wght@400;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{--bg:#07090f;--bg2:#0d1422;--bg3:#131c2e;--bd:#1b2740;--ac:#3b82f6;--cy:#22d3ee;--gr:#10b981;--re:#f43f5e;--ye:#f59e0b;--pu:#a855f7;--or:#f97316;--tx:#f1f5f9;--mu:#4e6280;--mono:'DM Mono',monospace;--ui:'Cabinet Grotesk',sans-serif;}
*{margin:0;padding:0;box-sizing:border-box;}html,body{height:100%;background:var(--bg);color:var(--tx);font-family:var(--ui);}body{display:flex;flex-direction:column;}
.bar{display:flex;align-items:center;justify-content:space-between;padding:9px 16px;border-bottom:1px solid var(--bd);background:var(--bg2);position:sticky;top:0;z-index:20;gap:7px;flex-wrap:wrap;}
.logo{font-size:1rem;font-weight:900;letter-spacing:-1px;}.logo em{color:var(--ac);font-style:normal;}
.bc{display:flex;align-items:center;gap:7px;flex:1;justify-content:center;flex-wrap:wrap;}
.ptag{background:#f59e0b18;border:1px solid #f59e0b44;border-radius:4px;padding:2px 8px;font-family:var(--mono);font-size:.6rem;color:var(--ye);}
.ctag{background:#a855f722;border:1px solid #a855f744;border-radius:4px;padding:2px 8px;font-family:var(--mono);font-size:.6rem;color:var(--pu);}
.regime{border-radius:4px;padding:2px 8px;font-family:var(--mono);font-size:.6rem;font-weight:700;}
.regime.bull{background:#10b98122;color:var(--gr);border:1px solid #10b98144;}
.regime.bear{background:#f43f5e22;color:var(--re);border:1px solid #f43f5e44;}
.regime.neutral{background:#f59e0b22;color:var(--ye);border:1px solid #f59e0b44;}
.regime.unknown{background:var(--bg3);color:var(--mu);border:1px solid var(--bd);}
.ms{display:flex;background:var(--bg3);border:1px solid var(--bd);border-radius:6px;overflow:hidden;}
.mb{padding:4px 11px;font-family:var(--mono);font-size:.62rem;text-transform:uppercase;letter-spacing:.7px;background:none;border:none;color:var(--mu);cursor:pointer;transition:.15s;}
.mb:hover{color:var(--tx);}.mb.active.day{background:var(--or);color:#fff;}.mb.active.swing{background:var(--ac);color:#fff;}
.cd{font-family:var(--mono);font-size:.6rem;color:var(--mu);}.cd em{color:var(--cy);font-style:normal;}
.br{display:flex;align-items:center;gap:6px;}
.pill{display:flex;align-items:center;gap:5px;background:var(--bg3);border:1px solid var(--bd);border-radius:99px;padding:3px 9px;font-family:var(--mono);font-size:.62rem;}
.dot{width:5px;height:5px;border-radius:50%;background:var(--mu);}.dot.open{background:var(--gr);box-shadow:0 0 5px #10b98177;}.dot.closed{background:var(--re);}
.upd{font-family:var(--mono);font-size:.58rem;color:var(--mu);}
.logout{font-family:var(--mono);font-size:.58rem;color:var(--mu);text-decoration:none;border:1px solid var(--bd);border-radius:4px;padding:2px 7px;}
.logout:hover{color:var(--re);}
main{flex:1;padding:11px 16px;display:flex;flex-direction:column;gap:9px;overflow-y:auto;}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;}
.hc{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:11px 13px;position:relative;overflow:hidden;}
.hc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--c,transparent);}
.hc.b{--c:var(--ac);}.hc.g{--c:var(--gr);}.hc.r{--c:var(--re);}.hc.c{--c:var(--cy);}
.hl{font-family:var(--mono);font-size:.55rem;color:var(--mu);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:5px;}
.hv{font-size:1.3rem;font-weight:900;letter-spacing:-1.5px;line-height:1;}
.hv.b{color:var(--ac);}.hv.g{color:var(--gr);}.hv.r{color:var(--re);}.hv.c{color:var(--cy);}
.hs{font-family:var(--mono);font-size:.55rem;color:var(--mu);margin-top:3px;}
.tabs{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;overflow:hidden;}
.th{display:flex;border-bottom:1px solid var(--bd);overflow-x:auto;}.th::-webkit-scrollbar{display:none;}
.tb{flex:1;min-width:55px;padding:8px 4px;font-family:var(--mono);font-size:.59rem;text-transform:uppercase;letter-spacing:.6px;background:none;border:none;color:var(--mu);cursor:pointer;border-bottom:2px solid transparent;transition:.15s;}
.tb:hover{color:var(--tx);}.tb.active{color:var(--ac);border-bottom-color:var(--ac);}
.tp{display:none;padding:11px 13px;}.tp.active{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;}
.ts{text-align:center;}.tl{font-family:var(--mono);font-size:.53rem;color:var(--mu);text-transform:uppercase;letter-spacing:1px;margin-bottom:3px;}
.tv{font-size:1rem;font-weight:900;letter-spacing:-1px;}.tv.pos{color:var(--gr);}.tv.neg{color:var(--re);}.tv.neu{color:var(--tx);}
.tsb{font-family:var(--mono);font-size:.53rem;color:var(--mu);margin-top:2px;}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:9px;}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:11px 13px;}
.ct{font-family:var(--mono);font-size:.55rem;color:var(--mu);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px;}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.61rem;}
th{color:var(--mu);font-size:.53rem;letter-spacing:1px;text-transform:uppercase;padding:0 4px 5px 0;border-bottom:1px solid var(--bd);font-weight:400;}
td{padding:5px 4px 5px 0;border-bottom:1px solid var(--bd);vertical-align:middle;}
tr:last-child td{border-bottom:none;}
.sy{font-weight:700;color:var(--cy);}.pos{color:var(--gr);}.neg{color:var(--re);}
.slb{font-size:.5rem;background:#f43f5e22;border:1px solid #f43f5e55;color:var(--re);border-radius:3px;padding:1px 3px;}
.tpb{font-size:.5rem;background:#10b98122;border:1px solid #10b98155;color:var(--gr);border-radius:3px;padding:1px 3px;}
.trail-tag{font-size:.5rem;background:#f59e0b22;border:1px solid #f59e0b55;color:var(--ye);border-radius:3px;padding:1px 3px;}
.stag{font-size:.5rem;border-radius:3px;padding:1px 4px;font-weight:700;white-space:nowrap;}
.stag.trend_follow{background:#3b82f622;color:#3b82f6;border:1px solid #3b82f644;}
.stag.dip_buy{background:#10b98122;color:#10b981;border:1px solid #10b98144;}
.stag.breakout{background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44;}
.stag.mean_revert{background:#a855f722;color:#a855f7;border:1px solid #a855f744;}
.stag.vwap_bounce{background:#22d3ee22;color:#22d3ee;border:1px solid #22d3ee44;}
.stag.bear_short,.stag.bear_momentum{background:#f43f5e22;color:#f43f5e;border:1px solid #f43f5e44;}
.stag.bear_inverse_etf{background:#f9731622;color:#f97316;border:1px solid #f9731644;}
.stag.crypto_momentum,.stag.crypto_dip{background:#a855f722;color:#a855f7;border:1px solid #a855f744;}
.stag.day_momentum{background:#f9731622;color:#f97316;border:1px solid #f9731644;}
.dir-long{font-size:.5rem;background:#10b98122;color:var(--gr);border:1px solid #10b98144;border-radius:3px;padding:1px 4px;}
.dir-short{font-size:.5rem;background:#f43f5e22;color:var(--re);border:1px solid #f43f5e44;border-radius:3px;padding:1px 4px;}
.dir-inverse{font-size:.5rem;background:#f9731622;color:var(--or);border:1px solid #f9731644;border-radius:3px;padding:1px 4px;}
.dir-crypto{font-size:.5rem;background:#a855f722;color:var(--pu);border:1px solid #a855f744;border-radius:3px;padding:1px 4px;}
.wr2{display:flex;align-items:center;gap:4px;padding:5px 0;border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:.61rem;}
.wr2:last-child{border-bottom:none;}
.ws{font-weight:700;width:62px;flex-shrink:0;font-size:.59rem;}
.ht{font-size:.5rem;background:var(--ac);color:#fff;border-radius:3px;padding:1px 3px;font-weight:700;flex-shrink:0;}
.sp2{width:24px;flex-shrink:0;}.wp{color:var(--mu);margin-left:auto;font-size:.59rem;}
.wconf{font-size:.57rem;color:var(--cy);width:26px;text-align:right;}
.sp-row{display:flex;align-items:center;gap:5px;padding:4px 0;border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:.6rem;}
.sp-row:last-child{border-bottom:none;}.sp-name{width:82px;flex-shrink:0;}
.sp-bar{flex:1;height:4px;background:var(--bg3);border-radius:2px;overflow:hidden;}
.sp-fill{height:100%;background:var(--gr);border-radius:2px;transition:.4s;}
.sp-stat{color:var(--mu);font-size:.56rem;width:48px;text-align:right;flex-shrink:0;}
.lw{max-height:150px;overflow-y:auto;}.lw::-webkit-scrollbar{width:3px;}.lw::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px;}
.ll{padding:3px 0;border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:.59rem;color:var(--mu);line-height:1.4;}
.ll:last-child{border-bottom:none;}.ll .BUY{color:var(--gr);font-weight:700;}.ll .SELL{color:var(--re);font-weight:700;}.ll .SHORT{color:var(--or);font-weight:700;}.ll .BUY_TO_COVER{color:var(--cy);font-weight:700;}.ll.em{font-style:italic;}
.slg{max-height:135px;overflow-y:auto;}.slg::-webkit-scrollbar{width:3px;}.slg::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px;}
.sln{padding:3px 0;border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:.57rem;display:flex;gap:6px;}
.sln:last-child{border-bottom:none;}.sln .st{color:var(--mu);flex-shrink:0;}
.sln.info .sm{color:var(--mu);}.sln.warn .sm{color:var(--ye);}.sln.error .sm{color:var(--re);}.sln.success .sm{color:var(--gr);}
.ar{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.61rem;padding:5px 0;border-bottom:1px solid var(--bd);}
.ar:last-child{border-bottom:none;}.ak{color:var(--mu);}
.empty{text-align:center;padding:14px;color:var(--mu);font-family:var(--mono);font-size:.64rem;line-height:1.7;}
.tk{background:var(--bg2);border-top:1px solid var(--bd);padding:4px 0;overflow:hidden;white-space:nowrap;flex-shrink:0;}
.ti{display:inline-flex;gap:22px;animation:tk 40s linear infinite;}
@keyframes tk{from{transform:translateX(0);}to{transform:translateX(-50%);}}
.ti2{display:inline-flex;gap:5px;font-family:var(--mono);font-size:.59rem;align-items:center;}
.tsy{color:var(--cy);font-weight:700;}.tpr{color:var(--mu);}
@media(max-width:600px){.hero{grid-template-columns:repeat(2,1fr);}.g2{grid-template-columns:1fr;}}
</style></head><body>
<div class="bar">
  <div class="logo">auto<em>trader</em></div>
  <div class="bc">
    <div class="ptag">📄 PAPER</div>
    <div class="ctag" id="ctag" style="display:none">₿ CRYPTO MODE</div>
    <div class="regime unknown" id="regime">— market</div>
    <div class="ms">
      <button class="mb swing active" id="bsw" onclick="setMode('swing')">⚖ Swing</button>
      <button class="mb day" id="bdy" onclick="setMode('day')">⚡ Day</button>
    </div>
    <div class="cd">Next <em id="cd">—</em></div>
  </div>
  <div class="br">
    <div class="pill"><div class="dot" id="mdot"></div><span id="mst">…</span></div>
    <div class="upd" id="upd">—</div>
    <a class="logout" href="/logout">Logout</a>
  </div>
</div>
<main>
  <div class="hero">
    <div class="hc b"><div class="hl">Portfolio Value</div><div class="hv b" id="eq">$—</div><div class="hs" id="eqch">—</div></div>
    <div class="hc g"><div class="hl">Total Gains</div><div class="hv g" id="tg">$—</div><div class="hs" id="gct">—</div></div>
    <div class="hc r"><div class="hl">Total Losses</div><div class="hv r" id="tl">$—</div><div class="hs" id="lct">—</div></div>
    <div class="hc c"><div class="hl">Unrealized P&amp;L</div><div class="hv c" id="unr">$—</div><div class="hs" id="pct">—</div></div>
  </div>
  <div class="tabs">
    <div class="th">
      <button class="tb active" onclick="tab('hour')">Hour</button>
      <button class="tb" onclick="tab('today')">Today</button>
      <button class="tb" onclick="tab('week')">Week</button>
      <button class="tb" onclick="tab('year')">Year</button>
      <button class="tb" onclick="tab('all')">All Time</button>
    </div>
    <div class="tp active" id="t-hour"><div class="ts"><div class="tl">Made</div><div class="tv pos" id="h-g">$0.00</div><div class="tsb" id="h-wt">0 wins</div></div><div class="ts"><div class="tl">Lost</div><div class="tv neg" id="h-l">$0.00</div><div class="tsb" id="h-lt">0 losses</div></div><div class="ts"><div class="tl">Net</div><div class="tv neu" id="h-n">$0.00</div></div></div>
    <div class="tp" id="t-today"><div class="ts"><div class="tl">Made</div><div class="tv pos" id="d-g">$0.00</div><div class="tsb" id="d-wt">0 wins</div></div><div class="ts"><div class="tl">Lost</div><div class="tv neg" id="d-l">$0.00</div><div class="tsb" id="d-lt">0 losses</div></div><div class="ts"><div class="tl">Net</div><div class="tv neu" id="d-n">$0.00</div></div></div>
    <div class="tp" id="t-week"><div class="ts"><div class="tl">Made</div><div class="tv pos" id="w-g">$0.00</div><div class="tsb" id="w-wt">0 wins</div></div><div class="ts"><div class="tl">Lost</div><div class="tv neg" id="w-l">$0.00</div><div class="tsb" id="w-lt">0 losses</div></div><div class="ts"><div class="tl">Net</div><div class="tv neu" id="w-n">$0.00</div></div></div>
    <div class="tp" id="t-year"><div class="ts"><div class="tl">Made</div><div class="tv pos" id="y-g">$0.00</div><div class="tsb" id="y-wt">0 wins</div></div><div class="ts"><div class="tl">Lost</div><div class="tv neg" id="y-l">$0.00</div><div class="tsb" id="y-lt">0 losses</div></div><div class="ts"><div class="tl">Net</div><div class="tv neu" id="y-n">$0.00</div></div></div>
    <div class="tp" id="t-all"><div class="ts"><div class="tl">Made</div><div class="tv pos" id="a-g">$0.00</div><div class="tsb" id="a-wt">0 wins</div></div><div class="ts"><div class="tl">Lost</div><div class="tv neg" id="a-l">$0.00</div><div class="tsb" id="a-lt">0 losses</div></div><div class="ts"><div class="tl">Net</div><div class="tv neu" id="a-n">$0.00</div></div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="ct">Holdings <span style="color:var(--mu);font-size:.52rem;">(📍= trailing stop active)</span></div><div id="pw"><div class="empty">Waiting…</div></div></div>
    <div class="card"><div class="ct">Strategy Win Rates</div><div id="spw"><div class="empty">No trades yet</div></div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="ct">Watchlist <span id="wprog" style="color:var(--ye);font-size:.54rem;"></span></div><div id="ww"><div class="empty">Analysing…</div></div></div>
    <div class="card"><div class="ct">Trade Activity</div><div class="lw" id="lw"><div class="ll em">No trades yet</div></div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="ct">Account</div><div id="aw"></div></div>
    <div class="card"><div class="ct">Bot Status</div><div class="slg" id="slg"></div></div>
  </div>
</main>
<div class="tk"><div class="ti" id="tk">—</div></div>
<script>
const API="";let cmode="swing",cdv=0;
function $$(i){return document.getElementById(i);}
function set(i,v){const e=$$(i);if(e)e.textContent=v;}
function fD(n){return"$"+Math.abs(+n||0).toFixed(2);}
function fS(n){const v=+n||0;return(v>=0?"+":"-")+"$"+Math.abs(v).toFixed(2);}
function tab(t){
  ["hour","today","week","year","all"].forEach((k,i)=>{
    document.querySelectorAll(".tb")[i].classList.toggle("active",k===t);
    const p=$$("t-"+k);if(p)p.classList.toggle("active",k===t);
  });
}
async function setMode(m){
  cmode=m;
  await fetch(API+"/api/mode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:m})});
  $$("bdy").className="mb day"+(m==="day"?" active":"");
  $$("bsw").className="mb swing"+(m==="swing"?" active":"");
}
function stag(s){return s&&s!=="—"?`<span class="stag ${s}">${s.replace(/_/g," ")}</span>`:"—";}
function dtag(d){return d?`<span class="dir-${d}">${d}</span>`:"";}
function rPos(pos){
  set("pct",pos.length+" position"+(pos.length!==1?"s":""));
  const w=$$("pw");
  if(!pos.length){w.innerHTML='<div class="empty">No positions — bot buys when confidence ≥55%</div>';return;}
  let h='<table><thead><tr><th>Sym</th><th>Dir</th><th>Strategy</th><th>P&L</th><th>Stop</th><th>Target</th></tr></thead><tbody>';
  for(const p of pos){
    const c=p.pl>=0?"pos":"neg";
    const sl=p.stop_loss?`<span class="${p.trailing?'trail-tag':'slb'}">${p.trailing?"📍":""} ${fD(p.stop_loss)}</span>`:"—";
    const tp=p.take_profit?`<span class="tpb">${fD(p.take_profit)}</span>`:"—";
    h+=`<tr><td class="sy">${p.symbol}</td><td>${dtag(p.direction)}</td><td>${stag(p.strategy)}</td><td class="${c}">${fS(p.pl)}</td><td>${sl}</td><td>${tp}</td></tr>`;
  }
  w.innerHTML=h+"</tbody></table>";
}
function rStratPerf(perf){
  const w=$$("spw");
  const entries=Object.entries(perf||{}).filter(([,p])=>p.trades>0);
  if(!entries.length){w.innerHTML='<div class="empty">Win rates appear after first trade</div>';return;}
  entries.sort((a,b)=>(b[1].wins/Math.max(b[1].trades,1))-(a[1].wins/Math.max(a[1].trades,1)));
  w.innerHTML=entries.map(([s,p])=>{
    const wr=p.trades>0?Math.round(p.wins/p.trades*100):0;
    return`<div class="sp-row"><span class="sp-name">${stag(s)}</span><div class="sp-bar"><div class="sp-fill" style="width:${wr}%"></div></div><span class="sp-stat">${wr}% (${p.trades}t)</span><span class="${p.total_pl>=0?'pos':'neg'}" style="font-size:.55rem;width:44px;text-align:right">${fS(p.total_pl)}</span></div>`;
  }).join("");
}
function rWatch(list,prog){
  set("wprog",prog||"");
  const w=$$("ww");
  if(!list||!list.length){w.innerHTML=`<div class="empty">${prog||"Analysing…"}</div>`;return;}
  w.innerHTML=list.slice(0,24).map(x=>{
    const he=x.held?'<span class="ht">HELD</span>':'<span class="sp2"></span>';
    return`<div class="wr2"><span class="ws">${x.symbol}</span>${he}${dtag(x.direction)}${stag(x.strategy)}<span class="wconf">${Math.round((x.confidence||0)*100)}%</span><span class="wp">${x.price?"$"+x.price:"—"}</span></div>`;
  }).join("");
}
function rLog(actions){
  const w=$$("lw");
  if(!actions||!actions.length){w.innerHTML='<div class="ll em">No trades yet</div>';return;}
  w.innerHTML=[...actions].reverse().slice(0,40).map(a=>{
    const lb=a.label.replace(/(BUY_TO_COVER|SHORT|BUY|SELL)/g,m=>`<span class="${m}">${m}</span>`);
    const pl=a.pl!=null?` <span class="${a.pl>=0?'pos':'neg'}">(${fS(a.pl)})</span>`:"";
    return`<div class="ll">${lb}${a.strategy?` ${stag(a.strategy)}`:""}${pl}</div>`;
  }).join("");
}
function rAcct(d){
  const ch=d.equity_change||0;
  const regime=d.market_regime||"unknown";
  const re=$$("regime");
  if(re){re.className=`regime ${regime}`;re.textContent=(regime==="bull"?"🐂 bull":regime==="bear"?"🐻 bear":regime==="neutral"?"⚖️ neutral":"— market");}
  const ct=$$("ctag");if(ct)ct.style.display=d.crypto_mode?"flex":"none";
  $$("aw").innerHTML=[
    ["Cash",fD(d.cash||0),""],["Buying Power",fD(d.buying_power||0),""],
    ["Start Equity",d.starting_equity?fD(d.starting_equity):"—",""],
    ["Change",fS(ch),ch>=0?"pos":"neg"],
    ["Market",d.market_regime||"—",""],["SPY Mom",(d.spy_momentum||0).toFixed(2)+"%",""],
    ["Mode",d.crypto_mode?"₿ Crypto 24/7":d.mode_label||"—",""],
    ["Watching",(d.watchlist_size||0)+" assets",""],
    ["Cycles",d.cycle_count||0,""],
    ["Trail Stop","activates at +"+""+(1.5)+"%",""],
    ["API",d.api_ok===true?"✅ OK":"❌ Error",""]
  ].map(([k,v,c])=>`<div class="ar"><span class="ak">${k}</span><span class="${c}">${v}</span></div>`).join("");
}
function rStatus(lines){
  const w=$$("slg");
  if(!lines||!lines.length)return;
  w.innerHTML=[...lines].reverse().slice(0,35).map(l=>`<div class="sln ${l.level||'info'}"><span class="st">${l.ts}</span><span class="sm">${l.msg}</span></div>`).join("");
}
function sPL(p,w){
  if(!w)return;
  set(p+"-g",w.gain>0?"$"+w.gain.toFixed(2):"$0.00");
  set(p+"-l",Math.abs(w.loss||0)>0?"$"+Math.abs(w.loss).toFixed(2):"$0.00");
  const ne=$$(p+"-n");
  if(ne){const n=w.net||0;ne.textContent=(n>=0?"+$":"-$")+Math.abs(n).toFixed(2);ne.className="tv "+(n>0?"pos":n<0?"neg":"neu");}
  set(p+"-wt",(w.win_trades||0)+" win"+((w.win_trades||0)!==1?"s":""));
  set(p+"-lt",(w.loss_trades||0)+" loss"+((w.loss_trades||0)!==1?"es":""));
}
function rTicker(w){
  const t=$$("tk");if(!w||!w.length){t.textContent="—";return;}
  t.innerHTML=[...w,...w].map(x=>`<span class="ti2"><span class="tsy">${x.symbol}</span><span class="tpr">${x.price?"$"+x.price:"—"}</span>${dtag(x.direction)}${stag(x.strategy)}<span style="color:var(--cy);font-size:.56rem">${x.conf_pct||""}</span></span>`).join("");
}
setInterval(()=>{if(cdv>0)cdv--;set("cd",cdv>60?Math.floor(cdv/60)+"m "+cdv%60+"s":cdv+"s");},1000);
async function refresh(){
  try{
    const r=await fetch(API+"/api/state?t="+Date.now());
    if(r.status===401){window.location="/login";return;}
    if(!r.ok)throw 0;
    const d=await r.json();
    const dot=$$("mdot");if(dot)dot.className="dot "+(d.market_open?"open":"closed");
    set("mst",d.market_open?"Stock Market Open":"Stock Market Closed");
    set("upd",d.last_update?"Updated "+new Date(d.last_update).toLocaleTimeString():"Waiting…");
    cdv=d.next_check_sec||0;
    if(d.mode&&d.mode!==cmode){cmode=d.mode;$$("bdy").className="mb day"+(d.mode==="day"?" active":"");$$("bsw").className="mb swing"+(d.mode==="swing"?" active":"");}
    set("eq","$"+parseFloat(d.equity||0).toFixed(2));
    const ch=d.equity_change||0;const ec=$$("eqch");
    if(ec){ec.textContent=(ch>=0?"+":"-")+"$"+Math.abs(ch).toFixed(2)+" since start";ec.style.color=ch>=0?"var(--gr)":"var(--re)";}
    const pl=d.pl_windows||{},aw=pl.all||{};
    set("tg","$"+(aw.gain||0).toFixed(2));set("tl","$"+Math.abs(aw.loss||0).toFixed(2));
    set("gct",(aw.win_trades||0)+" winning trades");set("lct",(aw.loss_trades||0)+" losing trades");
    const u=d.total_unrealized_pl||0;const ue=$$("unr");
    if(ue){ue.textContent=(u>=0?"+":"-")+"$"+Math.abs(u).toFixed(2);ue.className="hv "+(u>0?"g":u<0?"r":"c");}
    ["h","d","w","y","a"].forEach((p,i)=>sPL(p,[pl.hour,pl.today,pl.week,pl.year,pl.all][i]));
    rPos(d.positions||[]);rStratPerf(d.strategy_perf);
    rWatch(d.watchlist||[],d.scoring_progress);
    rLog(d.recent_actions||[]);rAcct(d);rStatus(d.status_log||[]);rTicker(d.watchlist||[]);
  }catch(e){set("upd","⚠ Connecting…");}
}
refresh();setInterval(refresh,10000);
</script></body></html>"""


CONTROLS_HTML="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<title>AutoTrader Controls</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Cabinet+Grotesk:wght@400;700;800;900&display=swap" rel="stylesheet"/>
<style>
:root{--bg:#07090f;--bg2:#0d1422;--bg3:#131c2e;--bd:#1b2740;--ac:#3b82f6;--cy:#22d3ee;--gr:#10b981;--re:#f43f5e;--ye:#f59e0b;--pu:#a855f7;--or:#f97316;--tx:#f1f5f9;--mu:#4e6280;--mono:"DM Mono",monospace;--ui:"Cabinet Grotesk",sans-serif;}
*{margin:0;padding:0;box-sizing:border-box;}html,body{min-height:100%;background:var(--bg);color:var(--tx);font-family:var(--ui);}
.bar{display:flex;align-items:center;justify-content:space-between;padding:10px 18px;border-bottom:1px solid var(--bd);background:var(--bg2);position:sticky;top:0;z-index:20;gap:8px;}
.logo{font-size:1rem;font-weight:900;letter-spacing:-1px;}.logo em{color:var(--ac);font-style:normal;}
.nav{display:flex;gap:8px;align-items:center;}
.navbtn{font-family:var(--mono);font-size:.65rem;color:var(--mu);text-decoration:none;border:1px solid var(--bd);border-radius:5px;padding:4px 10px;}
.navbtn:hover{color:var(--tx);}.navbtn.active{color:var(--ac);border-color:var(--ac);}
.logout{font-family:var(--mono);font-size:.6rem;color:var(--mu);text-decoration:none;border:1px solid var(--bd);border-radius:4px;padding:3px 8px;}
.logout:hover{color:var(--re);}
main{padding:16px 18px;display:flex;flex-direction:column;gap:14px;max-width:900px;margin:0 auto;}
.section{background:var(--bg2);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;}
.stitle{font-weight:800;font-size:1rem;margin-bottom:4px;}
.sdesc{font-family:var(--mono);font-size:.65rem;color:var(--mu);margin-bottom:14px;}
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bd);}
.toggle-row:last-child{border-bottom:none;}
.tinfo{flex:1;}.tlabel{font-weight:700;font-size:.88rem;}
.tdesc{font-family:var(--mono);font-size:.62rem;color:var(--mu);margin-top:2px;}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0;}
.toggle input{opacity:0;width:0;height:0;}
.slider{position:absolute;inset:0;background:var(--bg3);border:1px solid var(--bd);border-radius:12px;cursor:pointer;transition:.2s;}
.slider:before{content:"";position:absolute;width:18px;height:18px;left:2px;top:2px;background:var(--mu);border-radius:50%;transition:.2s;}
input:checked+.slider{background:var(--ac);border-color:var(--ac);}
input:checked+.slider:before{transform:translateX(20px);background:white;}
.pause-row{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-radius:8px;border:1px solid var(--bd);margin-bottom:14px;background:var(--bg3);}
.pause-row.paused{border-color:#f43f5e55;background:#f43f5e08;}
.pbtn{padding:8px 18px;border-radius:8px;border:none;font-family:var(--mono);font-size:.72rem;font-weight:700;cursor:pointer;}
.pbtn.pause{background:var(--re);color:white;}.pbtn.resume{background:var(--gr);color:white;}
.pos-row{display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid var(--bd);font-family:var(--mono);font-size:.72rem;flex-wrap:wrap;}
.pos-row:last-child{border-bottom:none;}
.pos-sym{font-weight:700;color:var(--cy);width:65px;flex-shrink:0;}
.pos-dir{font-size:.58rem;border-radius:3px;padding:1px 5px;flex-shrink:0;}
.pos-dir.long{background:#10b98122;color:var(--gr);border:1px solid #10b98144;}
.pos-dir.short{background:#f43f5e22;color:var(--re);border:1px solid #f43f5e44;}
.pos-dir.crypto{background:#a855f722;color:var(--pu);border:1px solid #a855f744;}
.pos-dir.inverse{background:#f9731622;color:var(--or);border:1px solid #f9731644;}
.pos-pl{flex:1;min-width:120px;}
.sell-btn{padding:5px 12px;background:var(--re);color:white;border:none;border-radius:6px;font-family:var(--mono);font-size:.65rem;font-weight:700;cursor:pointer;}
.sell-btn:hover{opacity:.8;}
.wl-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;min-height:30px;}
.chip{display:inline-flex;align-items:center;gap:5px;background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:4px 8px;font-family:var(--mono);font-size:.67rem;}
.chip.custom{border-color:#3b82f644;background:#3b82f611;color:var(--ac);}
.chip-x{cursor:pointer;color:var(--mu);font-size:.85rem;line-height:1;margin-left:2px;}
.chip-x:hover{color:var(--re);}
.add-row{display:flex;gap:8px;margin-top:8px;}
.add-inp{flex:1;background:var(--bg3);border:1px solid var(--bd);border-radius:7px;padding:8px 11px;color:var(--tx);font-family:var(--mono);font-size:.75rem;outline:none;}
.add-inp:focus{border-color:var(--ac);}
.add-inp::placeholder{color:var(--mu);}
.add-btn{padding:8px 14px;background:var(--ac);color:white;border:none;border-radius:7px;font-family:var(--mono);font-size:.72rem;font-weight:700;cursor:pointer;}
.empty{color:var(--mu);font-family:var(--mono);font-size:.7rem;padding:10px 0;}
.toast{position:fixed;bottom:20px;right:20px;background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:10px 16px;font-family:var(--mono);font-size:.72rem;opacity:0;transition:.3s;z-index:99;}
.toast.show{opacity:1;}.toast.ok{border-color:#10b98155;color:var(--gr);}.toast.err{border-color:#f43f5e55;color:var(--re);}
</style></head><body>
<div class="bar">
  <div class="logo">auto<em>trader</em></div>
  <div class="nav">
    <a class="navbtn" href="/">📊 Dashboard</a>
    <a class="navbtn active" href="/controls">⚙️ Controls</a>
    <a class="logout" href="/logout">Logout</a>
  </div>
</div>
<main>
  <div class="section">
    <div class="stitle">Bot Control</div>
    <div class="sdesc">Pause all trading instantly or toggle individual modes on/off.</div>
    <div class="pause-row" id="pause-row">
      <div><div style="font-weight:700" id="pause-label">Bot is Running</div>
      <div style="font-family:var(--mono);font-size:.62rem;color:var(--mu)" id="pause-desc">Trading normally</div></div>
      <button class="pbtn pause" id="pause-btn" onclick="togglePause()">⏸ Pause Bot</button>
    </div>
    <div class="toggle-row">
      <div class="tinfo"><div class="tlabel">Stock Trading</div><div class="tdesc">Trade stocks Mon–Fri 9:30am–4pm ET</div></div>
      <label class="toggle"><input type="checkbox" id="tog-stocks" onchange="setSetting('trade_stocks',this.checked)"/><span class="slider"></span></label>
    </div>
    <div class="toggle-row">
      <div class="tinfo"><div class="tlabel">Crypto Trading</div><div class="tdesc">BTC, ETH, SOL etc — 24/7 including weekends</div></div>
      <label class="toggle"><input type="checkbox" id="tog-crypto" onchange="setSetting('trade_crypto',this.checked)"/><span class="slider"></span></label>
    </div>
    <div class="toggle-row">
      <div class="tinfo"><div class="tlabel">Swing Trading</div><div class="tdesc">15-min candles, holds hours to days, 5 strategies</div></div>
      <label class="toggle"><input type="checkbox" id="tog-swing" onchange="setSetting('trade_swing',this.checked)"/><span class="slider"></span></label>
    </div>
    <div class="toggle-row">
      <div class="tinfo"><div class="tlabel">Day Trading</div><div class="tdesc">1-min candles, closes all positions before market close</div></div>
      <label class="toggle"><input type="checkbox" id="tog-day" onchange="setSetting('trade_day',this.checked)"/><span class="slider"></span></label>
    </div>
  </div>
  <div class="section">
    <div class="stitle">Manual Sell</div>
    <div class="sdesc">Sell any open position immediately — the bot won't interfere.</div>
    <div id="pos-list"><div class="empty">No open positions right now</div></div>
  </div>
  <div class="section">
    <div class="stitle">Stock Watchlist</div>
    <div class="sdesc">Stocks the bot watches. Blue = you added it. Default ones can't be removed here.</div>
    <div class="wl-chips" id="stock-chips"></div>
    <div class="add-row">
      <input class="add-inp" id="add-stock-inp" placeholder="Add ticker e.g. TSLA" maxlength="10" onkeydown="if(event.key==='Enter')addStock()"/>
      <button class="add-btn" onclick="addStock()">+ Add Stock</button>
    </div>
  </div>
  <div class="section">
    <div class="stitle">Crypto Watchlist</div>
    <div class="sdesc">Crypto the bot watches 24/7. Just type BTC and /USD is added automatically.</div>
    <div class="wl-chips" id="crypto-chips"></div>
    <div class="add-row">
      <input class="add-inp" id="add-crypto-inp" placeholder="Add coin e.g. BTC or BTC/USD" maxlength="12" onkeydown="if(event.key==='Enter')addCrypto()"/>
      <button class="add-btn" onclick="addCrypto()">+ Add Crypto</button>
    </div>
  </div>
</main>
<div class="toast" id="toast"></div>
<script>
const API="";let paused=false;
function toast(msg,ok=true){const t=document.getElementById("toast");t.textContent=msg;t.className="toast show "+(ok?"ok":"err");setTimeout(()=>t.className="toast",2500);}
function fD(n){return"$"+Math.abs(+n||0).toFixed(2);}
function fS(n){const v=+n||0;return(v>=0?"+":"-")+"$"+Math.abs(v).toFixed(2);}
async function load(){
  const r=await fetch(API+"/api/controls");if(r.status===401){window.location="/login";return;}
  const d=await r.json();paused=d.bot_paused;
  document.getElementById("tog-stocks").checked=d.trade_stocks;
  document.getElementById("tog-crypto").checked=d.trade_crypto;
  document.getElementById("tog-swing").checked=d.trade_swing;
  document.getElementById("tog-day").checked=d.trade_day;
  updatePauseUI();renderPositions(d.positions||[]);
  renderStockChips(d.bull_swing||[],d.day_watchlist||[],d.custom_watchlist||[]);
  renderCryptoChips(d.crypto_watchlist||[],d.custom_crypto||[]);
}
function updatePauseUI(){
  const row=document.getElementById("pause-row"),lbl=document.getElementById("pause-label"),desc=document.getElementById("pause-desc"),btn=document.getElementById("pause-btn");
  if(paused){row.classList.add("paused");lbl.textContent="⏸ Bot Paused";desc.textContent="No trades will be placed";btn.textContent="▶ Resume Bot";btn.className="pbtn resume";}
  else{row.classList.remove("paused");lbl.textContent="▶ Bot Running";desc.textContent="Trading normally";btn.textContent="⏸ Pause Bot";btn.className="pbtn pause";}
}
async function togglePause(){paused=!paused;await post({bot_paused:paused});updatePauseUI();toast(paused?"Bot paused":"Bot resumed");}
async function setSetting(key,val){await post({[key]:val});toast(key.replace(/_/g," ")+" "+(val?"ON":"OFF"));}
async function post(data){return fetch(API+"/api/controls",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});}
function renderPositions(pos){
  const w=document.getElementById("pos-list");
  if(!pos.length){w.innerHTML='<div class="empty">No open positions</div>';return;}
  w.innerHTML=pos.map(p=>{
    const c=p.pl>=0?"pos":"neg";const dir=p.direction||"long";
    return`<div class="pos-row"><span class="pos-sym">${p.symbol}</span><span class="pos-dir ${dir}">${dir}</span><span class="pos-pl ${c}" style="flex:1">${fD(p.avg_cost)} → ${fD(p.current)} <span class="${c}">(${fS(p.pl)})</span></span><button class="sell-btn" onclick="sellPos('${p.symbol}')">Sell Now</button></div>`;
  }).join("");
}
async function sellPos(sym){
  if(!confirm("Sell "+sym+" now?"))return;
  const r=await fetch(API+"/api/sell",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol:sym})});
  const d=await r.json();
  if(d.ok){toast("Sold "+sym+" P&L: "+fS(d.pl||0));setTimeout(load,1000);}else toast("Error: "+d.error,false);
}
function renderStockChips(def,day,custom){
  const all=[...new Set([...def,...day])];
  document.getElementById("stock-chips").innerHTML=all.map(s=>`<div class="chip">${s}</div>`).join("")+custom.map(s=>`<div class="chip custom">${s} <span class="chip-x" onclick="removeStock('${s}')">×</span></div>`).join("");
}
function renderCryptoChips(def,custom){
  document.getElementById("crypto-chips").innerHTML=def.map(s=>`<div class="chip">${s}</div>`).join("")+custom.map(s=>`<div class="chip custom">${s} <span class="chip-x" onclick="removeCrypto('${s}')">×</span></div>`).join("");
}
async function addStock(){const inp=document.getElementById("add-stock-inp");const sym=inp.value.trim().toUpperCase();if(!sym)return;await post({add_stock:sym});toast("Added "+sym);inp.value="";setTimeout(load,500);}
async function removeStock(sym){await post({remove_stock:sym});toast("Removed "+sym);setTimeout(load,500);}
async function addCrypto(){const inp=document.getElementById("add-crypto-inp");const sym=inp.value.trim().toUpperCase();if(!sym)return;await post({add_crypto:sym});toast("Added "+sym);inp.value="";setTimeout(load,500);}
async function removeCrypto(sym){await post({remove_crypto:sym});toast("Removed "+sym);setTimeout(load,500);}
load();setInterval(load,15000);
</script></body></html>"""


# ─────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────
if __name__=="__main__":
    print("\n"+"="*54)
    print("  🤖  AutoTrader v5.1 — Bull + Bear + Crypto")
    print(f"  📊  Port: {DASHBOARD_PORT}")
    print(f"  🔒  Login: {DASH_USERNAME} / {DASH_PASSWORD}")
    print(f"  🐂  Bull: {len(BULL_SWING)} stocks, 5 strategies")
    print(f"  🐻  Bear: {len(BEAR_ETF)} inverse ETFs + {len(SHORT_CANDIDATES)} shorts")
    print(f"  ₿   Crypto: {len(CRYPTO_WATCHLIST)} pairs — trades 24/7")
    print(f"  📍  Trailing stop: activates at +{TRAIL_ACTIVATE_PCT}% gain")
    print(f"  🧠  Learns which strategies win over time")
    print(f"  🔐  Login persists 30 days on mobile")
    print(f"  📄  Paper trading — no real money")
    print("  ❌  Ctrl+C to stop")
    print("="*54+"\n")
    # Start trailing stop updater in background
    threading.Thread(target=update_trailing_stops,daemon=True).start()
    # Start trading loop in background
    threading.Thread(target=trading_loop,daemon=True).start()
    # Run Flask (main thread)
    run_flask()
