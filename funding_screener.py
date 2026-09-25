#!/usr/bin/env python3
"""
Perpetual Funding Rate Screener
================================
Fetches real-time perpetual funding rates from 11 exchanges and annualizes them.
Covers top 20 cryptocurrencies and top 20 RWA perps.
Outputs a self-contained HTML dashboard.

Usage:  python funding_screener.py
Output: funding_screener.html

Exchanges with live funding rates:
  ✅ Coinbase Derivatives, Hyperliquid, Kraken, GRVT, Lighter, Variational, Aster
  ❌ Binance — geo-blocked from US IPs (HTTP 451, no direct API access)

Limited data (no live funding rate):
  ⚠️ Nado       — funding rate not in public REST API (only pairs/symbols/tickers)
  ⚠️ Ondo Perps — funding API 403; rates estimated from dailyInterestRate × 365
  ✅ Extended   — perpetuals exist but do NOT use funding rates (0% maker fee model)
"""

import json
import urllib.request
import urllib.error
import datetime
import html as html_module

# ─── Token lists ───────────────────────────────────────────────

TOP_20_CRYPTO = [
    "BTC", "ETH", "BNB", "XRP", "ADA", "AVAX", "DOGE", "SHIB", "DOT",
    "LTC", "SOL", "POL", "UNI", "LINK", "ATOM", "NEAR", "TRX", "ETC",
    "APT", "SUI",
]

TOP_20_RWA = [
    "ONDO", "PAXG", "XAUT", "XAU", "XAG", "SPY", "QQQ",
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "WTI",
    "BRENT", "SPCX", "SPX", "SLV", "OUSDC",
]

ALL_TOKENS = set(TOP_20_CRYPTO + TOP_20_RWA)

# CDE (Coinbase Derivatives) uses proprietary contract_root_unit codes
# Map these to the standard token names used in ALL_TOKENS
CDE_CODE_MAP = {
    "CDEUS5": "SPX",     # US 500 Index perpetual → S&P 500 Index
    "CDETEK": "TECH",    # Technology Index perpetual
    "CDECHN": "CHINA",   # China Index perpetual
    "CDEAI": "AI",       # AI Index perpetual
    "CDEDEF": "DEFI",    # Defense Index perpetual
    "CDEOIL": "WTI",     # Crude Oil futures (not perp — filtered by empty funding_rate)
    "CDEGLD": "XAU",     # Gold futures (not perp — filtered)
    "CDESIL": "XAG",     # Silver futures (not perp — filtered)
    "CDEMC": "MAG7",     # Magnificent 7 futures (not perp)
    "CDEPT": "PLAT",     # Platinum futures (not perp)
    "CDENGS": "NG",      # Natural Gas futures (not perp)
    "CDECU": "COPPER",   # Copper futures (not perp)
}

# ─── Exchange metadata: maker rebates + incentive pools ─────────

EXCHANGE_INFO = {
    "Coinbase Derivatives": {
        "maker_fee": "0.02 USDC/contract (flat, per side)",
        "maker_rebate": "None (no rebate program)",
        "incentive_pool": "None",
        "funding_interval": "1 hour",
        "note": "US-regulated, CFTC DCO. Hourly funding.",
    },
    "Binance": {
        "maker_fee": "0.01% (1 bp)",
        "maker_rebate": "0% → -0.002% via VIP 9 (2 bp rebate)",
        "incentive_pool": "BNB auto-burn, Launchpool yield, BNB rewards",
        "funding_interval": "8 hours (3x daily)",
        "note": "Geo-blocked (HTTP 451). No direct API access from US IPs.",
    },
    "Hyperliquid": {
        "maker_fee": "0.015% (1.5 bp)",
        "maker_rebate": "-0.001% (1 bp rebate) at MM tier ($1M+ 30d vol)",
        "incentive_pool": "HYPE staking + seasonal trading rewards",
        "funding_interval": "8 h formula → paid hourly (1/8 each hour)",
        "note": "Rate is hourly decimal; annualized = rate × 8,760 × 100.",
    },
    "Kraken": {
        "maker_fee": "0.02% (2 bp)",
        "maker_rebate": "0% → -0.01% (10 bp rebate) at >$100M 30d vol",
        "incentive_pool": "None",
        "funding_interval": "1 hour",
        "note": "Hourly funding; fundingRate is percentage per hour. Annualized = rate × 8,760.",
    },
    "GRVT": {
        "maker_fee": "0.02% (2 bp)",
        "maker_rebate": "MM program: 50% fee refund on maker volume",
        "incentive_pool": "GRVT points → token allocation (trading rewards)",
        "funding_interval": "8 hours",
        "note": "Rate in percentage points per 8h; annualized = rate × 1,095.",
    },
    "Lighter": {
        "maker_fee": "0% (Standard), 0.004% (Plus)",
        "maker_rebate": "0% (Standard), -0.002% (Plus)",
        "incentive_pool": "Liquidity Partner Program: weekly LIT reward pools",
        "funding_interval": "8 hours (settled hourly at 1/8)",
        "note": "Direct Lighter L2 funding rates. Rate is decimal per 8h; annualised = rate × 1,095 × 100.",
    },
    "Variational": {
        "maker_fee": "0.02% (2 bp)",
        "maker_rebate": "0% → -0.005% at higher tiers",
        "incentive_pool": "VRT trading rewards",
        "funding_interval": "4 h (Quanto) or 8 h (Coin-margin)",
        "note": "Rate in percentage points per interval; annualized = rate × (8,760 / interval_hours).",
    },
    "Aster": {
        "maker_fee": "0.02% (2 bp)",
        "maker_rebate": "VIP tiers: 0% → -0.01% (1 bp rebate at VIP 5+)",
        "incentive_pool": "ASTER token trading rewards (veSTAR staking)",
        "funding_interval": "8 hours (3x daily)",
        "note": "Rate is decimal per 8h; annualized = rate × 1,095 × 100.",
    },
    "Nado": {
        "maker_fee": "0.01% (1 bp)",
        "maker_rebate": "0% → -0.005% via volume tiers (monthly reset)",
        "incentive_pool": "xPoints Season rewards",
        "funding_interval": "1 hour (8h formula ÷ 8 per hour)",
        "note": "Funding rate not available via public REST API — only pairs/tickers/symbols exposed.",
    },
    "Ondo Perps": {
        "maker_fee": "0.01% (varies by market)",
        "maker_rebate": "None (base tier)",
        "incentive_pool": "None",
        "funding_interval": "8 hours (estimated from dailyInterestRate × 365)",
        "note": "Funding API 403 (geo-restricted). Rates estimated from dailyInterestRate. ONDO token excluded.",
    },
    "Extended": {
        "maker_fee": "0% (0 bp)",
        "maker_rebate": "0% → -0.010% via market-share program",
        "incentive_pool": "EXT token rewards",
        "funding_interval": "N/A (no funding mechanism)",
        "note": "Perpetuals exist but do NOT use funding rates. No funding data.",
    },
}

# ─── HTTP helpers ──────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_json(url, timeout=15, method="GET", data=None, headers=None):
    """Fetch JSON from a public API endpoint."""
    if headers is None:
        headers = {}
    # Merge browser headers to avoid Cloudflare WAF blocks
    for k, v in _BROWSER_HEADERS.items():
        headers.setdefault(k, v)
    if data is not None:
        data = json.dumps(data).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:200]
        return {"_error": f"HTTP {e.code}: {msg}"}
    except Exception as e:
        return {"_error": str(e)[:200]}


# ── Coinbase Derivatives ────────────────────────────────────────

def fetch_cde():
    """Coinbase Derivatives perpetual funding rates (hourly)."""
    url = "https://api.coinbase.com/api/v3/brokerage/market/products?product_type=FUTURE"
    data = fetch_json(url, timeout=15)
    if "_error" in data:
        return {}, data["_error"]
    products = data.get("products", [])
    rates = {}
    for p in products:
        fd = p.get("future_product_details", {})
        interval = fd.get("funding_interval", "")
        raw_rate = fd.get("funding_rate") or fd.get("perpetual_details", {}).get("funding_rate", "")
        if not raw_rate or raw_rate == "":
            continue
        if "3600" in str(interval):
            # Try contract_root_unit first, then product_id
            base = fd.get("contract_root_unit", "")
            base = CDE_CODE_MAP.get(base, base)  # Map CDE-specific codes
            if not base:
                name = p.get("product_id", "")
                base = name.replace("-PERP", "").replace("-20DEC30-CDE", "").split("-")[0]
            base = base.upper()
            if base in ALL_TOKENS:
                rate = float(raw_rate)
                annual = rate * 8760 * 100
                rates[base] = {"rate": rate, "annual_pct": annual, "interval_h": 1}
    return rates, None


# ── Hyperliquid ────────────────────────────────────────────────

def fetch_hyperliquid():
    """Hyperliquid funding rates (hourly, via metaAndAssetCtxs)."""
    url = "https://api.hyperliquid.xyz/info"
    data = fetch_json(url, timeout=15, method="POST", data={"type": "metaAndAssetCtxs"})
    if "_error" in data:
        return {}, data["_error"]
    if not isinstance(data, list) or len(data) < 2:
        return {}, "Unexpected API response format"
    meta, ctxs = data
    universe = meta.get("universe", [])
    rates = {}
    for asset, ctx in zip(universe, ctxs):
        name = asset.get("name", "")
        funding = ctx.get("funding")
        if funding is None:
            continue
        base = name.upper()
        if base in ALL_TOKENS:
            rate = float(funding)
            annual = rate * 8760 * 100
            rates[base] = {"rate": rate, "annual_pct": annual, "interval_h": 1}
    return rates, None


# ── Kraken ─────────────────────────────────────────────────────

def fetch_kraken():
    """
    Kraken Futures funding rates.
    fundingRate is percentage per hour (e.g. 0.0458 = 0.0458%/h).
    Annualised = rate * 8760.
    """
    url = "https://futures.kraken.com/derivatives/api/v3/tickers"
    data = fetch_json(url, timeout=15)
    if "_error" in data:
        return {}, data["_error"]
    tickers = data.get("tickers", [])
    rates = {}
    seen = set()
    for t in tickers:
        pair = t.get("pair", "")
        tag = t.get("tag", "")
        if tag != "perpetual":
            continue
        funding = t.get("fundingRate")
        if funding is None:
            continue
        if pair in seen:
            continue
        seen.add(pair)
        base_raw = pair.split(":")[0].replace("/", "").upper()
        base_raw = base_raw.replace("XBT", "BTC").replace("XXBT", "BTC")
        if base_raw.endswith("X") and base_raw[:-1] in ALL_TOKENS:
            base = base_raw[:-1]
        else:
            base = base_raw
        if base not in ALL_TOKENS:
            continue
        rate = float(funding)
        if abs(rate) > 0.05:
            continue
        annual = rate * 8760
        rates[base] = {"rate": rate, "annual_pct": annual, "interval_h": 1}
    return rates, None


# ── GRVT ───────────────────────────────────────────────────────

def fetch_grvt():
    """GRVT funding rates (8-hour, percentage points)."""
    instruments = fetch_json(
        "https://market-data.grvt.io/full/v1/all_instruments",
        timeout=15, method="POST", data={},
    )
    if "_error" in instruments:
        return {}, instruments["_error"]
    _r = instruments.get("result", [])
    _perp = [i for i in _r if isinstance(i, dict) and i.get("kind") == "PERPETUAL"]
    perp_instruments = _perp
    rates = {}
    for inst in perp_instruments:
        symbol = inst.get("base", "").upper()
        if symbol not in ALL_TOKENS:
            continue
        instrument_id = inst.get("instrument", "")
        if not instrument_id:
            continue
        funding_data = fetch_json(
            "https://market-data.grvt.io/full/v1/funding",
            timeout=10, method="POST",
            data={"instrument": instrument_id, "limit": 1},
        )
        if "_error" in funding_data:
            continue
        result = funding_data.get("result", [])
        if not result:
            continue
        entry = result[0]
        raw_rate = entry.get("funding_rate", "0")
        interval_h = entry.get("funding_interval_hours", 8)
        rate = float(raw_rate)
        annual = rate * (8760 / int(interval_h))
        rates[symbol] = {"rate": rate, "annual_pct": annual, "interval_h": int(interval_h)}
    return rates, None


# ── Lighter (aggregator for Lighter + Binance + Bybit) ─────────

def fetch_lighter():
    """Lighter funding rates (direct from Lighter L2 perp DEX)."""
    url = "https://mainnet.zklighter.elliot.ai/api/v1/funding-rates"
    data = fetch_json(url, timeout=15)
    if "_error" in data:
        return {"Lighter": []}, data["_error"]
    all_rates = data.get("funding_rates", [])
    lighter_rates = []
    for r in all_rates:
        exchange = r.get("exchange", "").lower()
        if exchange not in ("lighter", "hyperliquid"):
            continue
        symbol = r.get("symbol", "").upper()
        rate = r.get("rate", 0)
        if symbol not in ALL_TOKENS:
            continue
        annual = rate * 1095 * 100
        entry = {"symbol": symbol, "rate": rate, "annual_pct": annual, "interval_h": 8}
        lighter_rates.append(entry)

    # Also try Binance via direct API (geo-blocked in US; will return error)
    return {"Lighter": lighter_rates}, None


# ── Binance (direct USDT-margined futures API) ──────────────────

def fetch_binance():
    """
    Binance USD-M futures funding rates.
    Direct API: https://fapi.binance.com/fapi/v1/premiumIndex?symbol=<SYMBOL>USDT
    Rate is decimal per 8 h; annualised = rate * 1,095 * 100.
    Geo-blocked (HTTP 451) from US IPs.
    """
    info = fetch_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=15)
    if "_error" in info:
        return {}, info["_error"]
    symbols = info.get("symbols", [])
    perps = [s for s in symbols if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"]
    rates = {}
    for s in perps:
        symbol = s.get("symbol", "")
        base = symbol.replace("USDT", "").upper()
        if base not in ALL_TOKENS:
            continue
        tick = fetch_json(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=10,
        )
        if "_error" in tick:
            continue
        raw = tick.get("lastFundingRate", "0")
        if not raw:
            continue
        rate = float(raw)
        annual = rate * 1095 * 100
        rates[base] = {"rate": rate, "annual_pct": annual, "interval_h": 8}
    return rates, None


# ── Variational ────────────────────────────────────────────────

def fetch_variational():
    """Variational funding rates (percentage points per variable interval)."""
    url = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"
    data = fetch_json(url, timeout=15)
    if "_error" in data:
        return {}, data["_error"]
    listings = data.get("listings", [])
    rates = {}
    for l in listings:
        ticker = l.get("ticker", "")
        if ticker not in ALL_TOKENS:
            continue
        rate = float(l.get("funding_rate", "0"))
        interval_s = l.get("funding_interval_s", 28800)
        interval_h = interval_s / 3600.0
        annual = rate * (8760 / interval_h)
        if abs(annual) > 500:
            continue
        rates[ticker] = {"rate": rate, "annual_pct": annual, "interval_h": int(interval_h)}
    return rates, None


# ── Aster DEX ──────────────────────────────────────────────────

def fetch_aster():
    """Aster DEX funding rates (8-hour, decimal, Binance-compatible API)."""
    info = fetch_json(
        "https://fapi.asterdex.com/fapi/v1/exchangeInfo",
        timeout=15,
    )
    if "_error" in info:
        return {}, info["_error"]
    symbols = info.get("symbols", [])
    perps = [s for s in symbols if s.get("contractType") in ("PERPETUAL", "PERP")]
    rates = {}
    for s in perps:
        symbol = s.get("symbol", "")
        base = symbol.replace("USDT", "").upper()
        if base not in ALL_TOKENS:
            continue
        tick = fetch_json(
            f"https://fapi.asterdex.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=10,
        )
        if "_error" in tick:
            continue
        raw_rate = tick.get("lastFundingRate", "0")
        if not raw_rate:
            continue
        rate = float(raw_rate)
        annual = rate * 1095 * 100
        rates[base] = {"rate": rate, "annual_pct": annual, "interval_h": 8}
    return rates, None


# ── Nado ───────────────────────────────────────────────────────

def fetch_nado():
    """Nado DEX: gets pairs + symbols with fee info. No funding rate via REST API."""
    pairs_data = fetch_json(
        "https://api.prod.nado.xyz/gateway/v2/pairs",
        timeout=15,
    )
    if "_error" in pairs_data:
        return [], pairs_data["_error"]
    symbols_data = fetch_json(
        "https://api.prod.nado.xyz/archive/v2/symbols",
        timeout=15,
    )
    if "_error" in symbols_data:
        return [], symbols_data["_error"]
    result = []
    pairs = pairs_data if isinstance(pairs_data, list) else pairs_data.get("pairs", [])
    symbols = symbols_data if isinstance(symbols_data, dict) else {}
    for pair in pairs:
        ticker_id = pair.get("ticker_id", "")
        base = pair.get("base", "")
        is_perp = "PERP" in ticker_id or "PERP" in base
        if not is_perp:
            continue
        base_clean = base.replace("-PERP", "").replace("_USDT0", "").upper()
        if base_clean.startswith("W") and len(base_clean) > 4:
            base_clean = base_clean[1:].rstrip("X")
        if base_clean not in ALL_TOKENS:
            continue
        sym_info = symbols.get(base_clean + "-PERP", symbols.get(ticker_id, {}))
        maker_fee_x18 = sym_info.get("maker_fee_rate_x18", "100000000000000") if sym_info else "100000000000000"
        try:
            maker_fee = float(maker_fee_x18) / 1e18
        except (ValueError, TypeError):
            maker_fee = 0.01
        result.append({
            "symbol": base_clean,
            "ticker_id": ticker_id,
            "maker_fee": f"{maker_fee:.4f}%",
            "annual_pct": "N/A",
        })
    return result, None


# ── Ondo Perps ─────────────────────────────────────────────────

def fetch_ondo_perps():
    """Ondo Perps: funding API is geo-blocked (403). Estimates from dailyInterestRate.
    ONDO token perp excluded — it shows the base rate, not a unique perp rate."""
    data = fetch_json(
        "https://api.ondoperps.xyz/v1/markets",
        timeout=15,
    )
    if "_error" in data:
        return [], data["_error"]
    perps = data.get("result", {}).get("perps", {}).get("tradingPairs", [])
    result = []
    for p in perps:
        market = p.get("market", "")
        base = market.replace("-USD.P", "").replace(".US", "").upper()
        if base.startswith("W") and len(base) > 4:
            base = base[1:].rstrip("X")
        if base not in ALL_TOKENS:
            continue
        if base == "ONDO":
            continue  # exclude ONDO token perp — user request
        maker_fee = float(p.get("makerFee", 0))
        daily_rate = p.get("dailyInterestRate", "0")
        interval_divisions = p.get("fundingIntervalDivisions", 8)
        rate_cap = p.get("fundingRateCap", 0.01)
        annual = float(daily_rate) * 365 * 100 if daily_rate else 0
        result.append({
            "symbol": base,
            "ticker_id": market,
            "maker_fee": f"{maker_fee * 100:.2f}%",
            "daily_interest_rate": daily_rate,
            "funding_interval_divisions": interval_divisions,
            "funding_rate_cap": rate_cap,
            "annual_pct": annual,
        })
    return result, None


# ── Extended ───────────────────────────────────────────────────

def fetch_extended():
    """Extended Exchange: no funding rate mechanism."""
    return [], "No funding rate mechanism on Extended"


# ─── Data aggregation ──────────────────────────────────────────

def build_dataset():
    """Fetch all data and return a structured dataset."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("Fetching Coinbase Derivatives...")
    cde_rates, cde_err = fetch_cde()
    print(f"  {len(cde_rates)} symbols found")

    print("Fetching Hyperliquid...")
    hl_rates, hl_err = fetch_hyperliquid()
    print(f"  {len(hl_rates)} symbols found")

    print("Fetching Kraken...")
    kraken_rates, kraken_err = fetch_kraken()
    print(f"  {len(kraken_rates)} symbols found")

    print("Fetching GRVT...")
    grvt_rates, grvt_err = fetch_grvt()
    print(f"  {len(grvt_rates)} symbols found")

    print("Fetching Lighter...")
    lighter_data, lighter_err = fetch_lighter()
    print(f"  Lighter: {len(lighter_data.get('Lighter', []))} symbols")

    print("Fetching Binance (direct API)...")
    binance_rates, binance_err = fetch_binance()
    print(f"  {len(binance_rates)} symbols found")

    print("Fetching Variational...")
    var_rates, var_err = fetch_variational()
    print(f"  {len(var_rates)} symbols found")

    print("Fetching Aster...")
    aster_rates, aster_err = fetch_aster()
    print(f"  {len(aster_rates)} symbols found")

    print("Fetching Nado...")
    nado_data, nado_err = fetch_nado()
    print(f"  {len(nado_data)} symbols with fee info")

    print("Fetching Ondo Perps...")
    ondo_data, ondo_err = fetch_ondo_perps()
    print(f"  {len(ondo_data)} markets (ONDO token excluded)")

    # ── Build consolidated table ──────────────────────────────
    # Structure: {token: {exchange: {annual_pct, rate, interval_h}}}
    consolidated = {}

    def add_rates(rates_dict, exchange_name):
        for token, info in rates_dict.items():
            if token not in consolidated:
                consolidated[token] = {}
            consolidated[token][exchange_name] = {
                "annual_pct": info["annual_pct"],
                "rate": info["rate"],
                "interval_h": info.get("interval_h", "?"),
            }

    add_rates(cde_rates, "Coinbase Derivatives")
    add_rates(hl_rates, "Hyperliquid")
    add_rates(kraken_rates, "Kraken")
    add_rates(grvt_rates, "GRVT")
    add_rates(var_rates, "Variational")
    add_rates(aster_rates, "Aster")
    add_rates(binance_rates, "Binance")

    # Add Ondo Perps (list format, convert to dict for add_rates)
    ondo_dict = {o["symbol"]: {"annual_pct": o["annual_pct"], "rate": o.get("rate", 0), "interval_h": o.get("interval_h", 8)} for o in ondo_data}
    add_rates(ondo_dict, "Ondo Perps")

    # Add Lighter from Lighter API
    for entry in lighter_data.get("Lighter", []):
        token = entry["symbol"]
        if token not in consolidated:
            consolidated[token] = {}
        consolidated[token]["Lighter"] = {
            "annual_pct": entry["annual_pct"],
            "rate": entry["rate"],
            "interval_h": entry["interval_h"],
        }

    # Build exchange order for display
    exchange_order = [
        "Binance", "Coinbase Derivatives", "Hyperliquid", "Kraken",
        "GRVT", "Lighter", "Variational", "Aster",
        "Nado", "Ondo Perps", "Extended",
    ]

    # Split tokens into crypto and RWA
    crypto_tokens = []
    for t in TOP_20_CRYPTO:
        if t in consolidated:
            crypto_tokens.append(t)
        elif t in [n["symbol"] for n in nado_data]:
            crypto_tokens.append(t)

    rwa_tokens = []
    for t in TOP_20_RWA:
        if t in consolidated:
            rwa_tokens.append(t)
        elif t in [n["symbol"] for n in nado_data]:
            rwa_tokens.append(t)

    return {
        "timestamp": timestamp,
        "exchange_order": exchange_order,
        "consolidated": consolidated,
        "crypto_tokens": crypto_tokens,
        "rwa_tokens": rwa_tokens,
        "nado_data": nado_data,
        "ondo_data": ondo_data,
        "errors": {
            "cde": cde_err,
            "hyperliquid": hl_err,
            "kraken": kraken_err,
            "grvt": grvt_err,
            "lighter": lighter_err,
            "binance": binance_err,
            "variational": var_err,
            "aster": aster_err,
            "nado": nado_err,
            "ondo": ondo_err,
        },
        "exchange_info": EXCHANGE_INFO,
    }


# ─── HTML helpers ─────────────────────────────────────────────

def fmt_annual(v):
    """Format annualized percentage with color."""
    if isinstance(v, str):
        return f'<span style="color:#f39c12">{v}</span>'
    if v is None:
        return "—"
    if abs(v) < 0.01:
        return "—"
    color = "#3ecc7e" if v >= 0 else "#ff6b6b"
    return f'<span style="color:{color}">{v:+.1f}%</span>'


def fmt_rate_cell(v):
    """Format a rate cell for the table."""
    if v is None:
        return '<td style="font-size:11px;text-align:center;color:#555">—</td>'
    if isinstance(v, str):
        return f'<td style="font-size:11px;text-align:center">{v}</td>'
    return f'<td style="font-size:11px;text-align:center">{fmt_annual(v)}</td>'


def build_row(token, exchanges, consolidated, nado_data, ondo_data):
    """Build a single table row with data for one token across all exchanges."""
    row = f"<tr><td style='font-weight:600'>{token}</td>"
    vals = {}
    for ex in exchanges:
        info = consolidated.get(token, {}).get(ex)
        if info:
            v = info["annual_pct"]
            vals[ex] = v
            row += f"<td style='font-size:11px;text-align:center'>{fmt_annual(v)}</td>"
        else:
            # Check Nado / Ondo Perps / Extended
            if ex == "Nado":
                match = [n for n in nado_data if n["symbol"] == token]
                v = match[0]["annual_pct"] if match else None
            elif ex == "Ondo Perps":
                match = [o for o in ondo_data if o["symbol"] == token]
                v = match[0]["annual_pct"] if match else None
            else:
                v = None

            if v is not None:
                row += f"<td style='font-size:11px;text-align:center'>{fmt_annual(v)}</td>"
                vals[ex] = v if isinstance(v, (int, float)) else None
            else:
                row += "<td style='font-size:11px;text-align:center;color:#555'>—</td>"
                vals[ex] = None

    longs = {k: vv for k, vv in vals.items() if vv is not None and vv > 0.01}
    if longs:
        best_long = max(longs.items(), key=lambda x: x[1])
        row += f"<td style='font-size:10px;color:green'>{best_long[0]}<br>{best_long[1]:+.1f}%</td>"
    else:
        row += "<td style='font-size:10px;color:#555'>—</td>"

    shorts = {k: vv for k, vv in vals.items() if vv is not None and vv < -0.01}
    if shorts:
        best_short = min(shorts.items(), key=lambda x: x[1])
        row += f"<td style='font-size:10px;color:red'>{best_short[0]}<br>{best_short[1]:+.1f}%</td>"
    else:
        row += "<td style='font-size:10px;color:#555'>—</td>"

    row += "</tr>"
    return row


def generate_html(dataset):
    ts = dataset["timestamp"]
    exchanges = dataset["exchange_order"]
    consolidated = dataset["consolidated"]
    crypto_tokens = dataset["crypto_tokens"]
    rwa_tokens = dataset["rwa_tokens"]
    nado_data = dataset["nado_data"]
    ondo_data = dataset["ondo_data"]
    exchange_info = dataset["exchange_info"]
    errors = dataset["errors"]

    # Build headers
    headers_html = "<th>Token</th>"
    for ex in exchanges:
        headers_html += f"<th style='font-size:10px'>{ex}</th>"
    headers_html += "<th>Best Long<br>(earn)</th><th>Best Short<br>(pay)</th>"

    # Build rows
    rows_html = ""
    for token in crypto_tokens:
        rows_html += build_row(token, exchanges, consolidated, nado_data, ondo_data) + "\n"

    rows_html_rwa = ""
    for token in rwa_tokens:
        rows_html_rwa += build_row(token, exchanges, consolidated, nado_data, ondo_data) + "\n"

    # Build exchange info table
    info_rows = ""
    for ex_name in exchanges:
        info = exchange_info.get(ex_name, {})
        note = info.get("note", "")
        note_html = f"<br><em style='font-size:9px;color:#888'>{html_module.escape(note)}</em>" if note else ""
        info_rows += f"""<tr>
          <td style='font-weight:600'>{ex_name}</td>
          <td style='font-size:11px'>{html_module.escape(info.get('maker_fee', '—'))}</td>
          <td style='font-size:11px'>{html_module.escape(info.get('maker_rebate', '—'))}</td>
          <td style='font-size:11px'>{html_module.escape(info.get('incentive_pool', '—'))}</td>
          <td style='font-size:11px'>{html_module.escape(info.get('funding_interval', '—'))}{note_html}</td>
        </tr>"""

    # Build status messages
    status_msgs = ""
    for ex_name, err in errors.items():
        status_ex = {
            "cde": "Coinbase Derivatives",
            "hyperliquid": "Hyperliquid",
            "kraken": "Kraken",
            "grvt": "GRVT",
            "lighter": "Lighter",
            "binance": "Binance",
            "variational": "Variational",
            "aster": "Aster",
            "nado": "Nado",
            "ondo": "Ondo Perps",
            "extended": "Extended",
        }.get(ex_name, ex_name)
        if err:
            status_msgs += f"<span style='color:#a00'>{status_ex}: {err}</span><br>"
        else:
            status_msgs += f"<span style='color:#0a0'>✓ {status_ex}</span><br>"

    # Build Nado sub-table rows
    nado_info_rows = ""
    for n in nado_data:
        nado_info_rows += f"<tr><td style='font-size:11px'>{n['symbol']}</td><td style='font-size:11px'>{n['ticker_id']}</td><td style='font-size:11px'>{n['maker_fee']}</td></tr>"

    # Build Ondo Perps sub-table rows
    ondo_info_rows = ""
    for o in ondo_data:
        apr = o.get("annual_pct", 0)
        ondo_info_rows += f"<tr><td style='font-size:11px'>{o['symbol']}</td><td style='font-size:11px'>{o['ticker_id']}</td><td style='font-size:11px'>{o['maker_fee']}</td><td style='font-size:11px'>{apr:+.1f}%</td></tr>"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Perpetual Funding Rate Screener</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f0f18; color: #e0e0e0; padding: 24px; font-size: 13px; }}
  h1 {{ font-size: 22px; color: #fff; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; color: #c0c0ff; margin: 24px 0 10px; border-bottom: 1px solid #2a2a4a; padding-bottom: 4px; }}
  .subtitle {{ color: #888; font-size: 12px; margin-bottom: 16px; line-height: 1.5; }}
  .updated {{ color: #aaa; font-size: 11px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; font-size: 11px; }}
  th {{ background: #1a1a2e; color: #c0c0ff; padding: 6px 4px; text-align: center; border: 1px solid #2a2a4a; font-weight: 600; white-space: nowrap; }}
  td {{ padding: 5px 4px; border: 1px solid #2a2a4a; }}
  tr:nth-child(even) {{ background: #161625; }}
  tr:nth-child(odd) {{ background: #1a1a2e; }}
  tr:hover {{ background: #22223a; }}
  th:first-child, td:first-child {{ text-align: left; background: #141428; position: sticky; left: 0; min-width: 70px; }}
  .positive {{ color: #3ecc7e; }}
  .negative {{ color: #ff6b6b; }}
  .neutral {{ color: #888; }}
  .status-box {{ background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; font-size: 11px; line-height: 1.6; }}
  .note {{ background: #1a1a2e; border-left: 3px solid #f39c12; padding: 8px 12px; margin: 8px 0; font-size: 11px; border-radius: 0 4px 4px 0; }}
  .legend {{ display: flex; gap: 16px; margin: 8px 0 16px; font-size: 11px; color: #888; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 4px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  a {{ color: #64b5f0; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{ margin-top: 20px; font-size: 10px; color: #555; text-align: center; }}
  .sub-table {{ margin-top: 8px; font-size: 10px; }}
  .sub-table th {{ background: #111; }}
  .sub-table td:first-child {{ min-width: 50px; }}
</style>
</head>
<body>
<h1>Perpetual Funding Rate Screener</h1>
<div class="subtitle">Real-time annualized funding rates across 11 exchanges for top 20 cryptocurrencies and top 20 RWA perps. Rates are color-coded: green = positive (longs pay shorts), red = negative (shorts pay longs).</div>
<div class="updated">Updated: {ts}</div>

<div class="legend">
  <span><span class="dot" style="background:#3ecc7e"></span> Positive (longs pay shorts)</span>
  <span><span class="dot" style="background:#ff6b6b"></span> Negative (shorts pay longs)</span>
  <span><span class="dot" style="background:#888"></span> No data / N/A</span>
</div>

<div class="status-box">
<b>API Status:</b><br>{status_msgs}
</div>

<div class="note">
<b>Binance:</b> Geo-blocked from US IPs (HTTP 451). No funding rate data available from direct API access.</div>
</div>

<h2>Top 20 Cryptocurrencies</h2>
<table>
<thead>
  <tr style="position:sticky;top:0;z-index:2">
    {headers_html}
  </tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<h2>Top 20 RWA Perpetuals</h2>
<table>
<thead>
  <tr style="position:sticky;top:0;z-index:2">
    {headers_html}
  </tr>
</thead>
<tbody>
{rows_html_rwa}
</tbody>
</table>

<h2>Exchange Fees &amp; Incentives</h2>
<table>
<thead>
  <tr>
    <th>Exchange</th>
    <th>Maker Fee</th>
    <th>Maker Rebate</th>
    <th>Incentive Pool</th>
    <th>Funding Interval</th>
  </tr>
</thead>
<tbody>
{info_rows}
</tbody>
</table>

<div class="note">
<b>Nado:</b> Funding rate not available via public REST API — only pairs/tickers/symbols exposed. Below are available perp pairs with fee info.
</div>
<table class="sub-table">
<thead>
  <tr><th>Token</th><th>Pair</th><th>Maker Fee</th></tr>
</thead>
<tbody>
{nado_info_rows}
</tbody>
</table>

<div class="note">
<b>Ondo Perps:</b> Funding API 403 (geo-restricted). Rates estimated from dailyInterestRate × 365. Actual funding may include premium component. ONDO token excluded.
</div>
<table class="sub-table">
<thead>
  <tr><th>Token</th><th>Pair</th><th>Maker Fee</th><th>Est. Annual</th></tr>
</thead>
<tbody>
{ondo_info_rows}
</tbody>
</table>

<div class="footer">
Perpetual Funding Rate Screener • Data via public exchange APIs • No API keys required • Auto-refresh: refresh the page after re-running the script
</div>
</body>
</html>'''

    return html


# =====================================================================
# Arbitrage Screener
# =====================================================================

# Fee model: maker on short leg, taker on long leg, volume incentives as rebates
# Values in percentage points (0.01 = 0.01%)
ARB_FEES = {
    "Binance":            {"maker": 0.00, "taker": 0.04, "rebate": 0.00},
    "Coinbase Derivatives": {"maker": 0.01, "taker": 0.03, "rebate": 0.00},
    "Hyperliquid":        {"maker": 0.02, "taker": 0.05, "rebate": 0.01},
    "Kraken":             {"maker": 0.02, "taker": 0.05, "rebate": 0.00},
    "GRVT":               {"maker": 0.00, "taker": 0.02, "rebate": 0.01},
    "Lighter":            {"maker": 0.00, "taker": 0.02, "rebate": 0.01},
    "Variational":        {"maker": 0.00, "taker": 0.02, "rebate": 0.01},
    "Aster":              {"maker": 0.01, "taker": 0.03, "rebate": 0.01},
    "Nado":               {"maker": 0.01, "taker": 0.03, "rebate": 0.00},
    "Ondo Perps":         {"maker": 0.01, "taker": 0.03, "rebate": 0.00},
    "Extended":           {"maker": 0.00, "taker": 0.02, "rebate": 0.01},
}


def generate_arb_html(dataset):
    """Generate the arbitrage screener HTML.

    For each token, finds the best short (highest funding rate) and best long
    (lowest funding rate) across all exchanges. Computes:
      gross  = max_rate - min_rate  (always ≥ 0)
      fees   = maker_fee(short) + taker_fee(long)
      incent = rebate(short) + rebate(long)   [volume-based maker rebates]
      net    = gross - fees + incent
    """
    consolidated = dataset["consolidated"]
    all_tokens = dataset["crypto_tokens"] + dataset["rwa_tokens"]
    ts = dataset["timestamp"]

    rows = []
    for token in all_tokens:
        if token not in consolidated:
            continue
        # Collect real rates across exchanges
        rates = []
        for ex in dataset["exchange_order"]:
            v = consolidated[token].get(ex)
            if not v:
                continue
            r = v.get("annual_pct")
            if not isinstance(r, (int, float)):
                continue
            if abs(r) < 0.01:
                continue
            fees = ARB_FEES.get(ex, {"maker": 0.01, "taker": 0.02, "rebate": 0.00})
            rates.append((ex, r, fees))
        if len(rates) < 2:
            continue
        # Best short = highest rate (you receive the most by shorting)
        # Best long  = lowest rate  (you receive the most by longing, since longs receive when rate is negative)
        best_short = max(rates, key=lambda x: x[1])
        best_long = min(rates, key=lambda x: x[1])
        rates_exclude_pair = [r for r in rates if r[0] != best_short[0]]
        if not rates_exclude_pair:
            # Only one exchange had data — can't arbitrage
            continue
        # Ensure long is on a different exchange
        best_long = min(rates_exclude_pair, key=lambda x: x[1])
        gross = best_short[1] - best_long[1]  # always ≥ 0
        # Maker on short leg, taker on long leg
        est_fees = best_short[2]["maker"] + best_long[2]["taker"]
        # Volume incentive: maker rebate on short + rebate on long
        est_incentive = best_short[2]["rebate"] + best_long[2]["rebate"]
        net = gross - est_fees + est_incentive
        if abs(net) < 0.1:
            continue
        rows.append({
            "token": token,
            "short_ex": best_short[0],
            "short_rate": best_short[1],
            "long_ex": best_long[0],
            "long_rate": best_long[1],
            "gross": gross,
            "fees": est_fees,
            "incentive": est_incentive,
            "net": net,
        })
    # Sort by net APY (positive first, then by magnitude)
    rows.sort(key=lambda x: x["net"], reverse=True)

    def fmt_pct(v):
        if v > 0:
            return f'<span style="color:#3ecc7e">+{v:.2f}%</span>'
        elif v < 0:
            return f'<span style="color:#ff6b6b">{v:.2f}%</span>'
        else:
            return '<span style="color:#555">—</span>'

    rows_html = ""
    for r in rows:
        rows_html += (
            f"<tr>"
            f'<td style="font-weight:600;font-size:12px">{r["token"]}</td>'
            f'<td style="font-size:11px">{r["short_ex"]}</td>'
            f'<td style="font-size:11px;text-align:right">{fmt_pct(r["short_rate"])}</td>'
            f'<td style="font-size:11px">{r["long_ex"]}</td>'
            f'<td style="font-size:11px;text-align:right">{fmt_pct(r["long_rate"])}</td>'
            f'<td style="font-size:11px;text-align:right">{fmt_pct(r["gross"])}</td>'
            f'<td style="font-size:11px;text-align:right"><span style="color:#aaa">{r["fees"]:.2f}%</span></td>'
            f'<td style="font-size:11px;text-align:right"><span style="color:#aaa">+{r["incentive"]:.2f}%</span></td>'
            f'<td style="font-size:11px;text-align:right;font-weight:600">{fmt_pct(r["net"])}</td>'
            f"</tr>\n"
        )

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Funding Rate Arbitrage Screener</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0; margin: 0; padding: 20px; }}
  h1 {{ font-size: 22px; color: #e0e0e0; margin: 0 0 4px 0; }}
  .subtitle {{ font-size: 12px; color: #888; margin: 0 0 16px 0; }}
  .updated {{ font-size: 11px; color: #666; margin: 0 0 16px 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a2a3e; color: #888; font-weight: 600; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #1a1a2e; }}
  tr:hover td {{ background: #151525; }}
  .legend {{ display: flex; gap: 16px; margin: 12px 0; font-size: 11px; color: #888; }}
  .legend span {{ color: #3ecc7e; }}
  .footer {{ margin-top: 24px; font-size: 11px; color: #555; }}
  .sub-table {{ margin: 8px 0 20px; font-size: 11px; }}
  .sub-table th {{ color: #aaa; font-weight: 600; }}
  .note {{ background: #151525; padding: 8px 12px; border-radius: 4px; margin: 8px 0; font-size: 11px; color: #aaa; }}
</style>
</head>
<body>
<h1>Funding Rate Arbitrage Screener</h1>
<div class="subtitle">Cross-exchange arbitrage opportunities (maker on short leg, taker on long leg, volume incentives included)</div>
<div class="updated">Updated: {ts}</div>

<div class="legend">
<span>Green: positive carry (you earn)</span>
<span>Red: negative carry (you pay)</span>
<span>Gray: neutral</span>
</div>

<table>
<thead>
<tr>
  <th>Token</th>
  <th>Short<br><span style="color:#888;font-weight:400">(highest rate)</span></th>
  <th>Rate</th>
  <th>Long<br><span style="color:#888;font-weight:400">(lowest rate)</span></th>
  <th>Rate</th>
  <th>Gross<br><span style="color:#888;font-weight:400">rate − rate</span></th>
  <th>Fees<br><span style="color:#888;font-weight:400">maker+short + maker+taker</span></th>
  <th>Incentive<br><span style="color:#888;font-weight:400">vol. rebates</span></th>
  <th>Net APY<br><span style="color:#888;font-weight:400">gross − fees + incentive</span></th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<div class="note">
<b>Model:</b> Net APY = (max funding rate − min funding rate) − (maker fee on short + taker fee on long) + (volume-based maker rebates on both legs).
Assumes ~$1M+ monthly volume per exchange for standard rebate tiers. Rates are annualized (hourly × 8,760).
Only shows opportunities with |net| ≥ 0.1% to filter noise.
</div>

<div class="footer">
Funding Rate Arbitrage Screener • Data via public exchange APIs • No API keys required • Auto-refresh: re-run the script
</div>
</body>
</html>'''

    return html


if __name__ == "__main__":
    print("=" * 60)
    print("Perpetual Funding Rate Screener")
    print("=" * 60)
    print()

    dataset = build_dataset()

    print()
    print("=" * 60)
    print(f"Timestamp: {dataset['timestamp']}")
    print(f"Crypto tokens with data: {len(dataset['crypto_tokens'])}")
    print(f"RWA tokens with data: {len(dataset['rwa_tokens'])}")
    print(f"Exchanges queried: {', '.join(dataset['exchange_order'])}")
    print("=" * 60)

    html_output = generate_html(dataset)
    output_path = "funding_screener.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_output)
    print(f"\nDashboard written to: {output_path}")

    arb_output = generate_arb_html(dataset)
    arb_path = "arb_screener.html"
    with open(arb_path, "w", encoding="utf-8") as f:
        f.write(arb_output)
    print(f"Arbitrage screener written to: {arb_path}")