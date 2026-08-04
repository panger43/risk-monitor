"""Hedge fund risk monitor: Multi-source ingest -> Scrape -> LLM classify -> Supabase."""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Literal
from urllib.parse import quote_plus

import feedparser
import requests
import trafilatura
from trafilatura.settings import DEFAULT_CONFIG
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from supabase import Client, create_client

from company_catalog import APPROVED_UNIVERSE, default_watchlist_rows

# ==================== NETWORK & TIMEOUT CONFIGURATION ====================
socket.setdefaulttimeout(5.0)

TRAFILATURA_CONFIG = DEFAULT_CONFIG
TRAFILATURA_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "3")
TRAFILATURA_CONFIG.set(
    "DEFAULT",
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

MAX_ITEMS_PER_FEED = 5
MAX_AGE_DAYS = 30  # Only ingest articles published within the last month
MAX_ARTICLE_CHARS = 8_000
MAX_FEED_WORKERS = 12
MAX_ARTICLE_WORKERS = 8
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Unrestricted model in Hong Kong / Asia region
OPENROUTER_MODEL = "deepseek/deepseek-chat"

# Report everything with even mild negative sentiment (1/10 and up)
ALERT_THRESHOLD = 1

_print_lock = Lock()
_db_lock = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class RiskAssessment(BaseModel):
    is_negative_event: bool = Field(
        description="True if there is ANY negative sentiment, concern, risk, controversy, or friction (even slight)."
    )
    severity_score: int = Field(
        ge=1,
        le=10,
        description="Severity score (1-3 = Economic/market noise, 4-5 = Operational friction, 6-10 = Regulatory/political/criminal risk).",
    )
    category: str = Field(
        description="Risk category, e.g. regulatory, political, bribery-corruption, litigation, executive, operational, market-concern, economic."
    )
    key_impact: str = Field(
        description="1-2 sentences summarizing the potential downside or negative sentiment."
    )


SourceType = Literal[
    "google_news",
    "yahoo_finance",
    "major_wires",
    "pr_newswire",
    "business_wire",
    "sec_8k",
    "hkex_announcements",
]


def load_config() -> dict[str, str]:
    load_dotenv()
    required = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    config = {key: os.environ[key] for key in required}
    config["SUPABASE_URL"] = config["SUPABASE_URL"].rstrip("/").removesuffix("/rest/v1").rstrip("/")
    return config


def md5_url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def unwrap_final_url(url: str) -> str:
    """Resolves Google News redirect URLs to direct publisher links."""
    if "news.google.com" not in url:
        return url
    try:
        res = requests.head(
            url,
            allow_redirects=True,
            timeout=3.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        return res.url
    except Exception:
        return url


# ==================== RSS FEED GENERATORS ====================
def google_news_rss_url(company_name: str) -> str:
    query = quote_plus(f'"{company_name}" when:{MAX_AGE_DAYS}d')
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def sec_8k_rss_url(ticker: str) -> str:
    clean_ticker = ticker.split(".")[0]
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={quote_plus(clean_ticker)}"
        f"&type=8-K&dateb=&owner=include&count={MAX_ITEMS_PER_FEED}&output=atom"
    )


def hkex_regulatory_rss_url() -> str:
    return "https://www.hkex.com.hk/Services/RSS-Feeds/regulatory-announcements?sc_lang=en"


def pr_newswire_rss_url(company_name: str) -> str:
    query = quote_plus(company_name)
    return f"https://www.prnewswire.com/rss/news-releases-list.rss?search={query}"


def yahoo_finance_rss_url(ticker: str) -> str:
    return (
        "https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={quote_plus(ticker)}&region=US&lang=en-US"
    )


def major_wires_rss_url(company_name: str) -> str:
    """Google News restricted to major financial publishers."""
    sites = " OR ".join(
        [
            "site:reuters.com",
            "site:bloomberg.com",
            "site:cnbc.com",
            "site:marketwatch.com",
            "site:wsj.com",
            "site:ft.com",
            "site:scmp.com",
        ]
    )
    query = quote_plus(f'"{company_name}" ({sites}) when:{MAX_AGE_DAYS}d')
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def business_wire_rss_url(company_name: str) -> str:
    query = quote_plus(f'"{company_name}" site:businesswire.com when:{MAX_AGE_DAYS}d')
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def entry_published_at(entry: Any) -> datetime | None:
    """Parse RSS published/updated timestamp into an aware UTC datetime."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, key, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def is_recent_enough(published_at: datetime | None, source_type: SourceType) -> bool:
    """Keep only items published within MAX_AGE_DAYS. Undated non-filing items are dropped."""
    if published_at is None:
        # Filings feeds are short-window; undated news/social is too risky to store.
        return source_type in ("sec_8k", "hkex_announcements")
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    return published_at >= cutoff


def fetch_feed_entries(feed_url: str, source_type: SourceType) -> list[dict[str, str]]:
    try:
        resp = requests.get(
            feed_url,
            headers={"User-Agent": "HedgeFundRiskMonitor risk@yourfund.com"},
            timeout=5.0,
        )
        parsed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning("Timeout/error fetching RSS feed %s: %s", feed_url, e)
        return []

    entries: list[dict[str, str]] = []
    skipped_stale = 0
    for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
        link = getattr(entry, "link", None) or ""
        title = getattr(entry, "title", None) or ""
        if not link:
            continue
        published_at = entry_published_at(entry)
        if not is_recent_enough(published_at, source_type):
            skipped_stale += 1
            logger.info(
                "Skipping stale %s item (%s): %s",
                source_type,
                published_at.date().isoformat() if published_at else "no-date",
                title[:80] or link,
            )
            continue
        entries.append(
            {
                "url": link.strip(),
                "title": title.strip(),
                "source_type": source_type,
                "published_at": published_at.isoformat() if published_at else "",
            }
        )
    if skipped_stale:
        logger.info("Dropped %d stale %s entries (>%d days)", skipped_stale, source_type, MAX_AGE_DAYS)
    return entries


def already_processed(supabase: Client, url_hash: str) -> bool:
    response = (
        supabase.table("processed_sources")
        .select("url_hash")
        .eq("url_hash", url_hash)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def scrape_article_text(url: str) -> str | None:
    try:
        downloaded = trafilatura.fetch_url(url, config=TRAFILATURA_CONFIG)
        if not downloaded:
            return None
        text = trafilatura.extract(
            downloaded,
            fast=True,
            include_comments=False,
            include_tables=False,
            config=TRAFILATURA_CONFIG,
        )
        return text.strip() if text and text.strip() else None
    except Exception:
        return None


def assess_risk(
    client: OpenAI,
    *,
    company_name: str,
    ticker: str,
    title: str,
    article_text: str,
) -> RiskAssessment:
    truncated = article_text[:MAX_ARTICLE_CHARS]

    completion = client.beta.chat.completions.parse(
        model=OPENROUTER_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an early-warning hedge fund risk analyst evaluating risk for portfolio companies.\n\n"
                    "=== SEVERITY SCORING GUIDELINES ===\n"
                    "Evaluate events strictly on this calibrated scale:\n\n"
                    "1. HIGH SEVERITY (Score 6 - 10) -> REGULATORY, POLITICAL, LEGAL, & FINANCIAL CRIME:\n"
                    "   - Government sanctions, geopolitical bans, or political scrutiny.\n"
                    "   - SEC/SFC/DOJ/ICAC investigations, antitrust probes, enforcement actions, or bans.\n"
                    "   - Bribery, corruption, bank loan fraud, employee theft, or criminal convictions.\n"
                    "   - Material class-action lawsuits, major patent disputes, or short-seller fraud attacks.\n"
                    "   - Abrupt executive departures (CEO/CFO), auditor resignations, or board disputes.\n\n"
                    "2. MODERATE SEVERITY (Score 4 - 5) -> OPERATIONAL & STRATEGIC FRICTION:\n"
                    "   - Product launch delays, key contract cancellations, or supply chain bottlenecks.\n"
                    "   - Direct earnings/revenue misses or guidance downgrades.\n\n"
                    "3. LOW SEVERITY (Score 1 - 3) -> ECONOMIC & BROAD MARKET NOISE:\n"
                    "   - Macroeconomic data, interest rate shifts, inflation concerns, or currency fluctuations.\n"
                    "   - General market sell-offs, sector headwinds, or minor analyst target price trims.\n"
                    "   - Mild rumors or speculative commentary.\n\n"
                    "EVALUATION INSTRUCTION:\n"
                    "If the content mentions ANY negative sentiment or downside risk, set is_negative_event = True, "
                    "assign a severity_score following the guidelines above, categorize the event, and provide a 1-2 sentence impact summary."
                ),
            },
            {
                "role": "user",
                "content": f"Company: {company_name} ({ticker})\nHeadline: {title}\n\nContent:\n{truncated}",
            },
        ],
        response_format=RiskAssessment,
    )
    assessment = completion.choices[0].message.parsed
    if assessment is None:
        raise RuntimeError("OpenAI returned no parsed RiskAssessment")
    return assessment


def insert_processed_source(supabase: Client, *, url_hash: str, url: str, title: str, source_type: str) -> None:
    supabase.table("processed_sources").insert(
        {"url_hash": url_hash, "url": url, "title": title, "source_type": source_type}
    ).execute()


def insert_risk_event(
    supabase: Client,
    *,
    url_hash: str,
    url: str,
    company_name: str,
    ticker: str,
    assessment: RiskAssessment,
    raw_snippet: str,
    published_at: str | None,
) -> None:
    payload: dict[str, object] = {
        "url_hash": url_hash,
        "url": url,
        "company_name": company_name,
        "ticker": ticker,
        "is_negative_event": assessment.is_negative_event,
        "severity_score": assessment.severity_score,
        "category": assessment.category,
        "key_impact": assessment.key_impact,
        "raw_snippet": raw_snippet[:1500],
    }
    if published_at:
        payload["published_at"] = published_at
    supabase.table("risk_events").insert(payload).execute()


def print_risk_card(
    company_name: str,
    ticker: str,
    title: str,
    url: str,
    source_type: str,
    assessment: RiskAssessment,
) -> None:
    """Prints a formatted visual card to the terminal."""
    if assessment.severity_score >= 6:
        header = f"HIGH RISK DETECTED ({assessment.severity_score}/10)"
    elif assessment.is_negative_event:
        header = f"MILD RISK / CONCERN ({assessment.severity_score}/10)"
    else:
        header = f"SAFE / NEUTRAL (Score: {assessment.severity_score}/10)"

    with _print_lock:
        print("\n" + "-" * 70)
        print(f" {header}")
        print("-" * 70)
        print(f" Company : {company_name} [{ticker}]")
        print(f" Source  : {source_type.upper()}")
        print(f" Category: {assessment.category.upper()}")
        print(f" Headline: {title}")
        print(f" Summary : {assessment.key_impact}")
        print(f" Link    : {url[:75]}...")
        print("-" * 70 + "\n", flush=True)


def process_entry(
    *,
    supabase: Client,
    openai_client: OpenAI,
    company_name: str,
    ticker: str,
    entry: dict[str, str],
) -> str:
    raw_url = entry["url"]
    title = entry["title"]
    source_type = entry["source_type"]
    published_at = entry.get("published_at") or None

    url = unwrap_final_url(raw_url)
    url_hash = md5_url_hash(url)

    with _db_lock:
        if already_processed(supabase, url_hash):
            return "skipped"

    article_text = scrape_article_text(url)
    if article_text is None:
        article_text = f"Headline: {title} (Note: Body text could not be scraped)."

    assessment = assess_risk(
        openai_client,
        company_name=company_name,
        ticker=ticker,
        title=title,
        article_text=article_text,
    )

    with _db_lock:
        if already_processed(supabase, url_hash):
            return "skipped"
        insert_processed_source(supabase, url_hash=url_hash, url=url, title=title, source_type=source_type)
        insert_risk_event(
            supabase,
            url_hash=url_hash,
            url=url,
            company_name=company_name,
            ticker=ticker,
            assessment=assessment,
            raw_snippet=article_text,
            published_at=published_at,
        )

    if assessment.is_negative_event and assessment.severity_score >= ALERT_THRESHOLD:
        print_risk_card(company_name, ticker, title, url, source_type, assessment)
    return "processed"


def company_feed_jobs(company: dict[str, str]) -> list[tuple[str, SourceType]]:
    name, ticker = company["name"], company["ticker"]
    jobs: list[tuple[str, SourceType]] = [
        (google_news_rss_url(name), "google_news"),
        (yahoo_finance_rss_url(ticker), "yahoo_finance"),
        (major_wires_rss_url(name), "major_wires"),
        (pr_newswire_rss_url(name), "pr_newswire"),
        (business_wire_rss_url(name), "business_wire"),
    ]
    if ticker.endswith(".HK"):
        jobs.append((hkex_regulatory_rss_url(), "hkex_announcements"))
    else:
        jobs.append((sec_8k_rss_url(ticker), "sec_8k"))
    return jobs


def collect_all_work_items(
    companies: list[dict[str, str]],
) -> list[tuple[str, str, dict[str, str]]]:
    """Fetch all company feeds in parallel and build (company, ticker, entry) work items."""
    feed_jobs: list[tuple[str, str, str, SourceType]] = []
    for company in companies:
        for feed_url, source_type in company_feed_jobs(company):
            feed_jobs.append((company["name"], company["ticker"], feed_url, source_type))

    work_items: list[tuple[str, str, dict[str, str]]] = []
    with ThreadPoolExecutor(max_workers=MAX_FEED_WORKERS) as pool:
        futures = {
            pool.submit(fetch_feed_entries, feed_url, source_type): (name, ticker)
            for name, ticker, feed_url, source_type in feed_jobs
        }
        for future in as_completed(futures):
            name, ticker = futures[future]
            try:
                entries = future.result()
            except Exception as exc:
                logger.error("Feed fetch failed for %s (%s): %s", name, ticker, exc)
                continue
            for entry in entries:
                work_items.append((name, ticker, entry))
    return work_items


def seed_company_universe(supabase: Client) -> None:
    payload = [
        {
            "ticker": c["ticker"].upper(),
            "company_name": c["name"],
            "exchange": c.get("exchange", ""),
            "is_approved": True,
        }
        for c in APPROVED_UNIVERSE
    ]
    supabase.table("company_universe").upsert(payload, on_conflict="ticker").execute()


def seed_watched_companies(supabase: Client) -> None:
    seed_company_universe(supabase)
    payload = [
        {"ticker": c["ticker"].upper(), "company_name": c["name"], "is_active": True}
        for c in default_watchlist_rows()
    ]
    supabase.table("watched_companies").upsert(payload, on_conflict="ticker").execute()


def load_watched_companies(supabase: Client) -> list[dict[str, str]]:
    """Load active watchlist from Supabase; seed universe + defaults if empty."""
    try:
        seed_company_universe(supabase)
    except Exception as exc:
        logger.warning("Could not seed company_universe (create table if missing): %s", exc)

    response = (
        supabase.table("watched_companies")
        .select("ticker, company_name, is_active")
        .eq("is_active", True)
        .order("ticker")
        .execute()
    )
    rows = response.data or []
    if not rows:
        logger.info("Watchlist empty — seeding default watchlist")
        seed_watched_companies(supabase)
        response = (
            supabase.table("watched_companies")
            .select("ticker, company_name, is_active")
            .eq("is_active", True)
            .order("ticker")
            .execute()
        )
        rows = response.data or []

    companies = [
        {"name": str(row["company_name"]), "ticker": str(row["ticker"]).upper()}
        for row in rows
        if row.get("company_name") and row.get("ticker")
    ]
    if not companies:
        logger.warning("No active watched companies found; falling back to catalog defaults")
        return default_watchlist_rows()
    return companies


def run_scan(
    *,
    tickers: list[str] | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, object]:
    """
    Run a risk scan.

    tickers:
      - None  -> full active watchlist
      - [...] -> only those tickers (must be on the active watchlist)

    on_progress:
      Optional callback(fraction_0_to_1, status_message) for UI progress bars.
    """

    def report(fraction: float, message: str) -> None:
        if on_progress is not None:
            on_progress(max(0.0, min(1.0, fraction)), message)

    config = load_config()
    supabase = create_client(config["SUPABASE_URL"], config["SUPABASE_SERVICE_ROLE_KEY"])
    openai_client = OpenAI(api_key=config["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
    companies = load_watched_companies(supabase)

    if tickers is not None:
        wanted = {t.upper().strip() for t in tickers if t.strip()}
        companies = [c for c in companies if c["ticker"].upper() in wanted]
        if not companies:
            raise RuntimeError(
                "No matching active watchlist companies for: " + ", ".join(sorted(wanted))
            )

    scope = "FULL WATCHLIST" if tickers is None else "SELECTED COMPANIES"
    print("\n" + "=" * 70)
    print(f" STARTING PORTFOLIO RISK SCAN ({scope})")
    print(f" Target Companies: {len(companies)}")
    print(f" Watchlist: {', '.join(c['ticker'] for c in companies)}")
    print(f" Parallelism: {MAX_FEED_WORKERS} feed workers / {MAX_ARTICLE_WORKERS} article workers")
    print("=" * 70)

    report(0.02, f"Fetching feeds for {len(companies)} companies…")
    logger.info("Fetching RSS feeds in parallel for %d companies...", len(companies))
    work_items = collect_all_work_items(companies)
    logger.info("Queued %d articles for scrape + LLM classification", len(work_items))

    processed = 0
    skipped = 0
    errors = 0
    total = len(work_items)

    if total == 0:
        report(1.0, "No new articles to process")
    else:
        report(0.08, f"Queued {total} articles — classifying…")

    with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as pool:
        futures = [
            pool.submit(
                process_entry,
                supabase=supabase,
                openai_client=openai_client,
                company_name=name,
                ticker=ticker,
                entry=entry,
            )
            for name, ticker, entry in work_items
        ]
        finished = 0
        for future in as_completed(futures):
            try:
                result = future.result()
                if result == "skipped":
                    skipped += 1
                else:
                    processed += 1
            except Exception as exc:
                errors += 1
                logger.error("Article processing failed: %s", exc)

            finished += 1
            if total:
                # Reserve 8% for feed fetch; remainder for article work.
                fraction = 0.08 + 0.92 * (finished / total)
                report(
                    fraction,
                    f"Articles {finished}/{total} · processed {processed} · skipped {skipped} · errors {errors}",
                )

    report(1.0, "Scan complete")
    print("\n" + "=" * 70)
    print(" RUN COMPLETE - All findings recorded in Supabase")
    print(f" Processed: {processed} | Skipped duplicates: {skipped} | Errors: {errors}")
    print("=" * 70 + "\n")

    return {
        "scope": scope,
        "companies": [c["ticker"] for c in companies],
        "queued": len(work_items),
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
    }


def run() -> int:
    run_scan(tickers=None)
    return 0


if __name__ == "__main__":
    sys.exit(run())
