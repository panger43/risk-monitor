"""Streamlit Risk Dashboard — company-first navigation with company search filter."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

st.set_page_config(page_title="HK Risk Radar", layout="wide")

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/").removesuffix("/rest/v1")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

TIMEFRAME_OPTIONS: dict[str, int] = {
    "Last 24 hours": 1,
    "Last 3 days": 3,
    "Last 7 days": 7,
    "Last 14 days": 14,
    "Last 30 days": 30,
    "Last 3 months": 90,
}

SOURCE_LABELS: dict[str, str] = {
    "google_news": "Google News",
    "yahoo_finance": "Yahoo Finance",
    "major_wires": "Major Wires (Reuters/Bloomberg/CNBC/etc.)",
    "pr_newswire": "PR Newswire",
    "business_wire": "Business Wire",
    "sec_8k": "SEC 8-K",
    "hkex_announcements": "HKEX Announcements",
    "reddit": "Reddit (legacy)",
}

DEFAULT_COMPANIES: list[dict[str, str]] = [
    {"name": "Apple Inc.", "ticker": "AAPL"},
    {"name": "Microsoft Corporation", "ticker": "MSFT"},
    {"name": "NVIDIA Corporation", "ticker": "NVDA"},
    {"name": "Tencent Holdings", "ticker": "0700.HK"},
    {"name": "Alibaba Group", "ticker": "9988.HK"},
    {"name": "Meituan", "ticker": "3690.HK"},
    {"name": "Xiaomi Corporation", "ticker": "1810.HK"},
    {"name": "Baidu Inc.", "ticker": "9888.HK"},
    {"name": "HSBC Holdings", "ticker": "0005.HK"},
    {"name": "AIA Group", "ticker": "1299.HK"},
    {"name": "HKEX (Hong Kong Exchanges)", "ticker": "0388.HK"},
    {"name": "Sun Hung Kai Properties", "ticker": "0016.HK"},
    {"name": "MTR Corporation", "ticker": "0066.HK"},
]


@st.cache_resource
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def seed_watched_companies(client: Client) -> None:
    payload = [
        {"ticker": c["ticker"].upper(), "company_name": c["name"], "is_active": True}
        for c in DEFAULT_COMPANIES
    ]
    client.table("watched_companies").upsert(payload, on_conflict="ticker").execute()


def fetch_watched_companies(client: Client) -> pd.DataFrame:
    try:
        response = (
            client.table("watched_companies")
            .select("ticker, company_name, is_active, created_at")
            .order("ticker")
            .execute()
        )
    except Exception as exc:
        st.error(
            "Could not load `watched_companies`. Create the table in Supabase first "
            f"(see schema.sql). Details: {exc}"
        )
        return pd.DataFrame(columns=["ticker", "company_name", "is_active", "created_at"])

    rows = response.data or []
    if not rows:
        seed_watched_companies(client)
        response = (
            client.table("watched_companies")
            .select("ticker, company_name, is_active, created_at")
            .order("ticker")
            .execute()
        )
        rows = response.data or []
    return pd.DataFrame(rows)


def add_watched_company(client: Client, company_name: str, ticker: str) -> None:
    client.table("watched_companies").upsert(
        {
            "ticker": ticker.upper().strip(),
            "company_name": company_name.strip(),
            "is_active": True,
        },
        on_conflict="ticker",
    ).execute()


def remove_watched_company(client: Client, ticker: str) -> None:
    client.table("watched_companies").delete().eq("ticker", ticker.upper().strip()).execute()


def processed_field(row: pd.Series, field: str) -> str | None:
    src = row.get("processed_sources")
    if isinstance(src, dict):
        value = src.get(field)
        return value if isinstance(value, str) and value else None
    return None


def load_events(client: Client, timeframe_days: int) -> pd.DataFrame:
    response = (
        client.table("risk_events")
        .select("*, processed_sources(url, title, source_type)")
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    data = response.data or []
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df["url"] = df.apply(
        lambda row: row["url"]
        if isinstance(row.get("url"), str) and str(row.get("url")).startswith("http")
        else processed_field(row, "url"),
        axis=1,
    )
    df["headline"] = df.apply(
        lambda row: processed_field(row, "title") or row.get("key_impact") or "Untitled event",
        axis=1,
    )
    df["source_type"] = df.apply(lambda row: processed_field(row, "source_type") or "unknown", axis=1)
    df["source_label"] = df["source_type"].map(
        lambda s: SOURCE_LABELS.get(s, s.replace("_", " ").title())
    )
    published = pd.to_datetime(df.get("published_at"), utc=True, errors="coerce")
    created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    df["event_at"] = published.fillna(created)
    cutoff = datetime.now(timezone.utc) - timedelta(days=timeframe_days)
    return df[df["event_at"] >= pd.Timestamp(cutoff)].copy()


def render_headline_card(row: pd.Series) -> None:
    severity = row["severity_score"]
    badge_color = "red" if severity >= 6 else "orange"
    article_url = row["url"] if isinstance(row["url"], str) and row["url"].startswith("http") else None
    event_at = row["event_at"]
    event_label = event_at.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(event_at) else "unknown"
    headline = str(row["headline"]).strip() or "Untitled event"
    category = str(row.get("category") or "uncategorized").strip().title()

    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"### {headline}")
            st.markdown(f"**{category}**")
        with c2:
            st.markdown(f":{badge_color}[**Severity {severity}/10**]")

        st.caption(f"Source: **{row['source_label']}** · {event_label}")
        st.write(row["key_impact"])

        if article_url:
            st.link_button("Open article", article_url)
        with st.expander("Snippet"):
            st.text(row["raw_snippet"])


def render_watchlist_editor(
    client: Client,
    active_watch: pd.DataFrame,
    watched_df: pd.DataFrame,
) -> None:
    with st.expander("Manage watchlist", expanded=False):
        st.caption("Changes apply on the next `python risk_monitor.py` run. Past headlines stay in the DB.")

        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            new_name = st.text_input(
                "Company name",
                placeholder="Company name",
                label_visibility="collapsed",
            )
        with c2:
            new_ticker = st.text_input(
                "Ticker",
                placeholder="Ticker",
                label_visibility="collapsed",
            )
        with c3:
            if st.button("Add", use_container_width=True):
                if not new_name.strip() or not new_ticker.strip():
                    st.warning("Name and ticker required.")
                else:
                    add_watched_company(client, new_name, new_ticker)
                    st.rerun()

        all_tickers = (
            watched_df["ticker"].tolist()
            if not watched_df.empty and "ticker" in watched_df.columns
            else []
        )
        if all_tickers:
            remove_ticker = st.selectbox("Remove company", options=all_tickers)
            if st.button("Remove from watchlist", use_container_width=True):
                remove_watched_company(client, remove_ticker)
                if st.session_state.selected_ticker == remove_ticker:
                    st.session_state.selected_ticker = None
                st.rerun()
        elif active_watch.empty:
            st.caption("Watchlist is empty.")


def render_company_home(
    client: Client,
    active_watch: pd.DataFrame,
    watched_df: pd.DataFrame,
    events_df: pd.DataFrame,
    min_severity: int,
) -> None:
    st.subheader("Companies")
    
    # --- SEARCH / FILTER BAR ---
    search_query = st.text_input(
        "Search companies",
        placeholder="Search by company name or ticker (e.g., Tencent, HSBC, 0700.HK)...",
        label_visibility="collapsed",
    )

    render_watchlist_editor(client, active_watch, watched_df)

    if active_watch.empty:
        st.info("No active companies. Open **Manage watchlist** above to add one.")
        return

    # Filter active companies by search query if provided
    filtered_watch = active_watch.copy()
    if search_query.strip():
        q = search_query.strip().lower()
        filtered_watch = filtered_watch[
            filtered_watch["company_name"].astype(str).str.lower().str.contains(q)
            | filtered_watch["ticker"].astype(str).str.lower().str.contains(q)
        ]

    if filtered_watch.empty:
        st.warning(f"No companies found matching '{search_query}'.")
        return

    scoped = events_df[events_df["severity_score"] >= min_severity] if not events_df.empty else events_df

    company_rows: list[dict[str, object]] = []
    for row in filtered_watch.itertuples():
        ticker = str(row.ticker)
        company_events = scoped[scoped["ticker"] == ticker] if not scoped.empty else pd.DataFrame()
        max_sev = int(company_events["severity_score"].max()) if not company_events.empty else 0
        high = int((company_events["severity_score"] >= 6).sum()) if not company_events.empty else 0
        company_rows.append(
            {
                "ticker": ticker,
                "company_name": row.company_name,
                "events": len(company_events),
                "max_severity": max_sev,
                "high_risks": high,
            }
        )

    summary = pd.DataFrame(company_rows).sort_values(
        by=["max_severity", "high_risks", "events", "ticker"],
        ascending=[False, False, False, True],
        kind="mergesort",
    )

    cols = st.columns(3)
    for idx, company in enumerate(summary.itertuples()):
        with cols[idx % 3]:
            with st.container(border=True):
                st.markdown(f"### `{company.ticker}`")
                st.caption(company.company_name)
                m1, m2, m3 = st.columns(3)
                m1.metric("Events", company.events)
                m2.metric("Max", company.max_severity if company.events else "—")
                m3.metric("High", company.high_risks)
                if st.button("Open", key=f"open_{company.ticker}", use_container_width=True):
                    st.session_state.selected_ticker = company.ticker
                    st.rerun()


def render_company_detail(
    ticker: str,
    active_watch: pd.DataFrame,
    events_df: pd.DataFrame,
    *,
    min_severity: int,
    selected_source: str,
    selected_timeframe: str,
) -> None:
    name_series = active_watch.loc[active_watch["ticker"] == ticker, "company_name"]
    company_name = str(name_series.iloc[0]) if not name_series.empty else ticker

    if st.button("← Companies"):
        st.session_state.selected_ticker = None
        st.rerun()

    st.subheader(f"{company_name}")
    st.caption(f"`{ticker}` · {selected_timeframe}")

    company_events = events_df[events_df["ticker"] == ticker].copy() if not events_df.empty else pd.DataFrame()
    if not company_events.empty:
        company_events = company_events[company_events["severity_score"] >= min_severity]
        if selected_source != "ALL":
            company_events = company_events[company_events["source_label"] == selected_source]
        company_events = company_events.sort_values(
            by=["severity_score", "event_at"],
            ascending=[False, False],
            kind="mergesort",
        )

    m1, m2, m3 = st.columns(3)
    m1.metric("Events", len(company_events))
    m2.metric("High (≥6)", int((company_events["severity_score"] >= 6).sum()) if not company_events.empty else 0)
    m3.metric("Mild (<6)", int((company_events["severity_score"] < 6).sum()) if not company_events.empty else 0)

    if company_events.empty:
        st.info("No headlines here yet. Run `python risk_monitor.py` after adding this ticker.")
        return

    for _, row in company_events.iterrows():
        render_headline_card(row)


supabase = get_supabase_client()

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None

st.title("HK Risk Radar")

# Sidebar — keep it minimal
with st.sidebar:
    st.header("Filters")
    selected_timeframe = st.selectbox(
        "Time frame",
        options=list(TIMEFRAME_OPTIONS.keys()),
        index=2,
    )
    timeframe_days = TIMEFRAME_OPTIONS[selected_timeframe]
    min_severity = st.slider("Min severity", 1, 10, 1)

    events_df = load_events(supabase, timeframe_days)
    source_options = ["ALL"]
    if not events_df.empty:
        source_options += sorted(events_df["source_label"].dropna().unique().tolist())
    selected_source = st.selectbox("Source", source_options)

    st.divider()
    if st.button("Refresh", use_container_width=True):
        st.rerun()

watched_df = fetch_watched_companies(supabase)
active_watch = (
    watched_df[watched_df["is_active"] == True]  # noqa: E712
    if not watched_df.empty and "is_active" in watched_df.columns
    else watched_df
)

selected = st.session_state.selected_ticker
if selected:
    render_company_detail(
        selected,
        active_watch,
        events_df,
        min_severity=min_severity,
        selected_source=selected_source,
        selected_timeframe=selected_timeframe,
    )
else:
    render_company_home(supabase, active_watch, watched_df, events_df, min_severity)