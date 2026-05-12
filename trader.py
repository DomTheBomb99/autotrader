@app.route("/")
def dash():

    acc = requests.get(BASE_URL + "/v2/account", headers=HEADERS).json()
    pos = requests.get(BASE_URL + "/v2/positions", headers=HEADERS).json()

    equity = acc.get("equity", "0")
    cash = acc.get("cash", "0")
    buying_power = acc.get("buying_power", "0")

    # ---------------- PORTFOLIO TABLE ----------------
    portfolio_rows = ""

    total_pl = 0

    for p in pos:

        pl = float(p["unrealized_pl"])
        total_pl += pl

        portfolio_rows += f"""
        <tr>
            <td>{p['symbol']}</td>
            <td>{p['qty']}</td>
            <td>${float(p['market_value']):.2f}</td>
            <td>${float(p['avg_entry_price']):.2f}</td>
            <td style="color:{'lime' if pl >= 0 else 'red'}">
                ${pl:.2f}
            </td>
        </tr>
        """

    # ---------------- WATCHLIST ----------------
    watch_html = ""

    for s in bot["watchlist"]:

        df = bars(s, "1Min", 60)

        if df is None:
            continue

        price = df["c"].iloc[-1]

        watch_html += f"""
        <div class="card">
            <b>{s}</b><br>
            Price: ${price:.2f}<br>
            Signal: {"ACTIVE" if model_ready else "WARMING UP"}
        </div>
        """

    # ---------------- MAIN DASHBOARD ----------------
    return f"""
    <html>
    <head>
    <meta http-equiv="refresh" content="10">

    <style>

    body {{
        background:#0b0f1a;
        color:white;
        font-family:Arial;
        margin:0;
    }}

    .topbar {{
        display:flex;
        justify-content:space-between;
        padding:15px;
        background:#111827;
        border-bottom:1px solid #1f2937;
    }}

    .grid {{
        display:grid;
        grid-template-columns:1fr 1fr 1fr;
        gap:15px;
        padding:15px;
    }}

    .panel {{
        background:rgba(255,255,255,0.05);
        border:1px solid rgba(255,255,255,0.08);
        border-radius:12px;
        padding:15px;
    }}

    table {{
        width:100%;
        border-collapse:collapse;
    }}

    td,th {{
        padding:8px;
        border-bottom:1px solid #1f2937;
        text-align:left;
    }}

    .card {{
        background:rgba(255,255,255,0.06);
        padding:10px;
        margin:5px;
        border-radius:10px;
    }}

    button {{
        padding:8px 12px;
        border:none;
        border-radius:8px;
        background:#2563eb;
        color:white;
        cursor:pointer;
    }}

    .green {{ color:lime; }}
    .red {{ color:red; }}

    </style>
    </head>

    <body>

    <!-- TOP BAR -->
    <div class="topbar">
        <div>
            <b>AI Trading Dashboard</b>
        </div>

        <div>
            <a href="/toggle"><button>{"STOP" if bot['running'] else "START"}</button></a>
        </div>
    </div>

    <!-- STATS -->
    <div class="grid">

        <div class="panel">
            <h3>Account</h3>
            Equity: ${equity}<br>
            Cash: ${cash}<br>
            Buying Power: ${buying_power}<br>
        </div>

        <div class="panel">
            <h3>Bot Status</h3>
            Running: {bot['running']}<br>
            ML Ready: {model_ready}<br>
            Samples: {len(X_data)}<br>
            Last: {bot['last']}<br>
        </div>

        <div class="panel">
            <h3>P&L</h3>
            Total Positions P/L: <span class="{'green' if total_pl >= 0 else 'red'}">${total_pl:.2f}</span>
        </div>

    </div>

    <!-- MAIN CONTENT -->
    <div class="grid">

        <!-- PORTFOLIO -->
        <div class="panel">
            <h3>Portfolio</h3>

            <table>
                <tr>
                    <th>Symbol</th>
                    <th>Qty</th>
                    <th>Value</th>
                    <th>Entry</th>
                    <th>P/L</th>
                </tr>
                {portfolio_rows}
            </table>
        </div>

        <!-- WATCHLIST -->
        <div class="panel">
            <h3>Watchlist</h3>
            {watch_html}
        </div>

        <!-- CONTROLS -->
        <div class="panel">
            <h3>Controls</h3>

            <a href="/buy/AAPL"><button>Buy AAPL</button></a><br><br>
            <a href="/buy/TSLA"><button>Buy TSLA</button></a><br><br>
            <a href="/buy/NVDA"><button>Buy NVDA</button></a><br><br>

            <a href="/toggle"><button>Start / Stop Bot</button></a>
        </div>

    </div>

    </body>
    </html>
    """
