"""Admin — approve companies into the universe (PIN gated)."""

from __future__ import annotations

import os
import re

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

st.set_page_config(page_title="Admin · Company Universe", layout="centered")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/").removesuffix("/rest/v1")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ADMIN_PIN = os.getenv("ADMIN_PIN", "").strip()

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")


def get_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def require_admin() -> bool:
    if not ADMIN_PIN:
        st.error(
            "Admin is disabled. Set `ADMIN_PIN` in your `.env` file to enable this page."
        )
        st.stop()
        return False

    if st.session_state.get("admin_ok"):
        return True

    st.title("Admin login")
    if st.button("← Back to Companies"):
        st.switch_page("dashboard.py")
    st.caption("Only for approving names into the company universe.")
    pin = st.text_input("Admin PIN", type="password")
    if st.button("Unlock", type="primary", use_container_width=True):
        if pin == ADMIN_PIN:
            st.session_state.admin_ok = True
            st.rerun()
        st.error("Wrong PIN.")
    st.stop()
    return False


def upsert_universe_company(*, name: str, ticker: str, exchange: str) -> None:
    client = get_client()
    client.table("company_universe").upsert(
        {
            "ticker": ticker,
            "company_name": name,
            "exchange": exchange,
            "is_approved": True,
        },
        on_conflict="ticker",
    ).execute()


require_admin()

st.title("Admin · Company universe")
if st.button("← Back to Companies"):
    st.switch_page("dashboard.py")
st.caption(
    "Adds an **approved** company so it can be selected in Manage watchlist. "
    "Does **not** auto-start research — add it to the watchlist afterward, then scan."
)

if st.button("Lock admin"):
    st.session_state.admin_ok = False
    st.rerun()

st.divider()

with st.form("add_universe_form", clear_on_submit=True):
    name = st.text_input("Company name", placeholder="BYD Company Limited")
    ticker = st.text_input("Ticker", placeholder="1211.HK or COST")
    exchange = st.selectbox("Exchange", options=["HK", "US", "OTHER"])
    submitted = st.form_submit_button("Approve into universe", type="primary")

if submitted:
    clean_name = name.strip()
    clean_ticker = ticker.strip().upper()
    if not clean_name or not clean_ticker:
        st.warning("Name and ticker are required.")
    elif not TICKER_RE.match(clean_ticker):
        st.warning("Ticker looks invalid. Use something like `AAPL` or `0700.HK`.")
    else:
        try:
            upsert_universe_company(
                name=clean_name,
                ticker=clean_ticker,
                exchange=exchange,
            )
            st.success(
                f"Approved `{clean_ticker}` — {clean_name}. "
                "Go to the main page → Manage watchlist → add it, then scan."
            )
        except Exception as exc:
            st.error(f"Failed to save: {exc}")

st.divider()
st.subheader("Currently approved (sample)")
try:
    rows = (
        get_client()
        .table("company_universe")
        .select("ticker, company_name, exchange, is_approved")
        .eq("is_approved", True)
        .order("ticker")
        .limit(50)
        .execute()
        .data
        or []
    )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("Universe is empty — seed from the main dashboard or add above.")
except Exception as exc:
    st.error(f"Could not load universe: {exc}")
