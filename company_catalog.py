"""Approved company universe — only these tickers can be watched."""

from __future__ import annotations

# Seed / catalog of approved names. Grow this list (or load from CSV later);
# the dashboard must never invent free-text tickers.
APPROVED_UNIVERSE: list[dict[str, str]] = [
    # US
    {"name": "Apple Inc.", "ticker": "AAPL", "exchange": "US"},
    {"name": "Microsoft Corporation", "ticker": "MSFT", "exchange": "US"},
    {"name": "NVIDIA Corporation", "ticker": "NVDA", "exchange": "US"},
    {"name": "Amazon.com Inc.", "ticker": "AMZN", "exchange": "US"},
    {"name": "Alphabet Inc.", "ticker": "GOOGL", "exchange": "US"},
    {"name": "Meta Platforms Inc.", "ticker": "META", "exchange": "US"},
    {"name": "Tesla Inc.", "ticker": "TSLA", "exchange": "US"},
    {"name": "JPMorgan Chase & Co.", "ticker": "JPM", "exchange": "US"},
    {"name": "Alibaba Group (US)", "ticker": "BABA", "exchange": "US"},
    {"name": "JD.com Inc.", "ticker": "JD", "exchange": "US"},
    {"name": "PDD Holdings Inc.", "ticker": "PDD", "exchange": "US"},
    # Hong Kong Tech & Internet
    {"name": "Tencent Holdings", "ticker": "0700.HK", "exchange": "HK"},
    {"name": "Alibaba Group (HK)", "ticker": "9988.HK", "exchange": "HK"},
    {"name": "Meituan", "ticker": "3690.HK", "exchange": "HK"},
    {"name": "Xiaomi Corporation", "ticker": "1810.HK", "exchange": "HK"},
    {"name": "Baidu Inc.", "ticker": "9888.HK", "exchange": "HK"},
    {"name": "JD.com Inc. (HK)", "ticker": "9618.HK", "exchange": "HK"},
    {"name": "NetEase Inc.", "ticker": "9999.HK", "exchange": "HK"},
    {"name": "Kuaishou Technology", "ticker": "1024.HK", "exchange": "HK"},
    # Hong Kong Finance & Exchanges
    {"name": "HSBC Holdings", "ticker": "0005.HK", "exchange": "HK"},
    {"name": "AIA Group", "ticker": "1299.HK", "exchange": "HK"},
    {"name": "HKEX (Hong Kong Exchanges)", "ticker": "0388.HK", "exchange": "HK"},
    {"name": "China Construction Bank", "ticker": "0939.HK", "exchange": "HK"},
    {"name": "ICBC", "ticker": "1398.HK", "exchange": "HK"},
    # Hong Kong Real Estate & Conglomerates
    {"name": "Sun Hung Kai Properties", "ticker": "0016.HK", "exchange": "HK"},
    {"name": "MTR Corporation", "ticker": "0066.HK", "exchange": "HK"},
    {"name": "CK Hutchison Holdings", "ticker": "0001.HK", "exchange": "HK"},
    {"name": "CK Asset Holdings", "ticker": "1113.HK", "exchange": "HK"},
]

# Default active watchlist seed (subset of APPROVED_UNIVERSE)
DEFAULT_WATCHLIST_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "0700.HK",
    "9988.HK",
    "3690.HK",
    "1810.HK",
    "9888.HK",
    "0005.HK",
    "1299.HK",
    "0388.HK",
    "0016.HK",
    "0066.HK",
]


def universe_by_ticker() -> dict[str, dict[str, str]]:
    return {c["ticker"].upper(): c for c in APPROVED_UNIVERSE}


def default_watchlist_rows() -> list[dict[str, str]]:
    catalog = universe_by_ticker()
    rows: list[dict[str, str]] = []
    for ticker in DEFAULT_WATCHLIST_TICKERS:
        company = catalog.get(ticker.upper())
        if company:
            rows.append({"name": company["name"], "ticker": company["ticker"]})
    return rows
