"""Quick mock test script to verify Supabase inserts."""

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 1. Fake URL Hash and Source Entry
test_hash = "test_hash_12345"
test_url = "https://example.com/test-lawsuit-article"

# Ensure clean state for test
supabase.table("processed_sources").delete().eq("url_hash", test_hash).execute()

print("1. Inserting into processed_sources...")
supabase.table("processed_sources").insert({
    "url_hash": test_hash,
    "url": test_url,
    "title": "TEST: DOJ files major antitrust lawsuit against Partner Corp",
    "source_type": "google_news"
}).execute()

print("2. Inserting mock evaluation into risk_events...")
res = supabase.table("risk_events").insert({
    "url_hash": test_hash,
    "company_name": "Partner Corp",
    "ticker": "PTR",
    "is_negative_event": True,
    "severity_score": 9,
    "category": "litigation",
    "key_impact": "TEST EVENT: DOJ seeks structural breakup of key logistics division.",
    "raw_snippet": "This is a synthetic test entry to verify Supabase database writes."
}).execute()

print("\n Success! Test row inserted:")
print(res.data)