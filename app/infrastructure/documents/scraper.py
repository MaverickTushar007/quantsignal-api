"""
Financial document scraper.
Sources: RBI, SEBI, NSE, Economic Times markets.
Runs on a schedule — fetches latest announcements automatically.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; QuantSignal/1.0; research bot)"
}

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")


def _store_document(source: str, doc_type: str, title: str,
                    url: str, content: str, published_at=None) -> bool:
    """Chunk, embed, and store a document in Supabase."""
    if not content or len(content) < 100:
        return False
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("[scraper] Supabase not configured")
        return False
    try:
        from supabase import create_client
        from app.infrastructure.documents.embedder import chunk_text, embed_text
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        # Check if URL already indexed
        existing = sb.table("financial_documents") \
            .select("id").eq("url", url).execute()
        if existing.data:
            log.info(f"[scraper] already indexed: {url}")
            return False

        chunks = chunk_text(content, chunk_size=400, overlap=50)
        log.info(f"[scraper] indexing {len(chunks)} chunks from: {title[:60]}")

        for i, chunk in enumerate(chunks):
            embedding = embed_text(chunk)
            sb.table("financial_documents").insert({
                "source":       source,
                "doc_type":     doc_type,
                "title":        title,
                "url":          url,
                "content":      chunk,
                "chunk_index":  i,
                "embedding":    embedding,
                "published_at": published_at or datetime.now(timezone.utc).isoformat(),
            }).execute()

        log.info(f"[scraper] stored {len(chunks)} chunks: {title[:60]}")
        return True
    except Exception as e:
        log.error(f"[scraper] store failed: {e}")
        return False


def scrape_rbi_pressreleases(limit: int = 10) -> int:
    """Scrape latest RBI press releases."""
    stored = 0
    try:
        url = "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        # Find press release links
        links = soup.find_all("a", href=True)
        pr_links = [
            l for l in links
            if "PressRelease" in str(l.get("href", ""))
        ][:limit]

        for link in pr_links:
            try:
                href = link.get("href", "")
                full_url = f"https://rbi.org.in{href}" if href.startswith("/") else href
                title = link.get_text(strip=True)
                if not title:
                    continue

                # Fetch the actual press release
                pr_resp = httpx.get(full_url, headers=HEADERS, timeout=15, follow_redirects=True)
                pr_soup = BeautifulSoup(pr_resp.text, "lxml")

                # Extract main content
                content_div = pr_soup.find("div", {"id": "content"}) or \
                              pr_soup.find("div", class_="pressrelease") or \
                              pr_soup.find("td", class_="tabletext")
                content = content_div.get_text(separator=" ", strip=True) if content_div else ""

                if len(content) > 200:
                    ok = _store_document("RBI", "monetary_policy", title, full_url, content)
                    if ok:
                        stored += 1
            except Exception as e:
                log.warning(f"[scraper] RBI link failed: {e}")
                continue

    except Exception as e:
        log.error(f"[scraper] RBI scrape failed: {e}")

    log.info(f"[scraper] RBI: stored {stored} documents")
    return stored


def scrape_sebi_circulars(limit: int = 10) -> int:
    """Scrape latest SEBI circulars."""
    stored = 0
    try:
        url = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=yes&type=1&subType=0"
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        links = soup.find_all("a", href=True)
        circ_links = [
            l for l in links
            if "/legal/circulars/" in str(l.get("href", ""))
               or "circular" in l.get_text(strip=True).lower()
        ][:limit]

        for link in circ_links:
            try:
                href = link.get("href", "")
                full_url = f"https://www.sebi.gov.in{href}" if href.startswith("/") else href
                title = link.get_text(strip=True)
                if not title or len(title) < 10:
                    continue

                circ_resp = httpx.get(full_url, headers=HEADERS, timeout=15, follow_redirects=True)
                circ_soup = BeautifulSoup(circ_resp.text, "lxml")
                content_div = circ_soup.find("div", class_="content") or \
                              circ_soup.find("div", {"id": "content"}) or \
                              circ_soup.find("main")
                content = content_div.get_text(separator=" ", strip=True) if content_div else ""

                if len(content) > 200:
                    ok = _store_document("SEBI", "circular", title, full_url, content)
                    if ok:
                        stored += 1
            except Exception as e:
                log.warning(f"[scraper] SEBI link failed: {e}")
                continue

    except Exception as e:
        log.error(f"[scraper] SEBI scrape failed: {e}")

    log.info(f"[scraper] SEBI: stored {stored} documents")
    return stored


def scrape_nse_announcements(limit: int = 20) -> int:
    """Scrape NSE corporate announcements via their API."""
    stored = 0
    try:
        url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
        session = httpx.Client(headers={
            **HEADERS,
            "Referer": "https://www.nseindia.com",
        }, follow_redirects=True)

        # NSE needs a session cookie first
        session.get("https://www.nseindia.com", timeout=10)
        resp = session.get(url, timeout=15)
        data = resp.json()

        announcements = data if isinstance(data, list) else data.get("data", [])

        for ann in announcements[:limit]:
            try:
                symbol  = ann.get("symbol", "")
                subject = ann.get("subject", "") or ann.get("desc", "")
                body    = ann.get("attchmntText", "") or ann.get("body", "")
                ann_url = ann.get("attchmntFile", "") or f"https://nseindia.com/ann/{symbol}"

                content = f"{subject}\n\n{body}".strip()
                if len(content) > 100:
                    ok = _store_document(
                        "NSE", "corporate_announcement",
                        f"{symbol}: {subject[:100]}",
                        ann_url, content
                    )
                    if ok:
                        stored += 1
            except Exception:
                continue

    except Exception as e:
        log.error(f"[scraper] NSE scrape failed: {e}")

    log.info(f"[scraper] NSE: stored {stored} documents")
    return stored


def run_full_scrape() -> dict:
    """Run all scrapers. Called by Perseus watcher every hour."""
    log.info("[scraper] Starting full financial document scrape...")
    results = {
        "rbi":  scrape_rbi_pressreleases(limit=5),
        "sebi": scrape_sebi_circulars(limit=5),
        "nse":  scrape_nse_announcements(limit=20),
    }
    total = sum(results.values())
    log.info(f"[scraper] Full scrape complete: {total} documents stored")
    return results
