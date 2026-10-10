"""Join citation counts and venue data from Semantic Scholar.

Most SCALE entries link to arXiv or a DOI, so those are resolved in bulk with the paper/batch
endpoint (500 ids per request). The rest fall back to per-title matching. Both are cached in
data/combined/, so re-runs only look up new papers. An API key (S2_API_KEY env var or
~/.config/semanticscholar.env) makes requests far more reliable.

Output: data/combined/external.parquet (pid, citations, influential, citation_pct, s2_url, s2_venue, peer_reviewed).
"""
import json
import os
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from common import COMBINED, load_mapped, use_os_certs

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
FIELDS = "title,citationCount,influentialCitationCount,externalIds,url,venue,publicationTypes,year"
KEY_FILE = Path.home() / ".config" / "semanticscholar.env"
ID_CACHE = COMBINED / "s2_id_cache.jsonl"
MATCH_CACHE = COMBINED / "s2_title_match_cache.jsonl"


def api_key():
    """S2_API_KEY from the environment, else from ~/.config/semanticscholar.env (never printed)."""
    if os.environ.get("S2_API_KEY"):
        return os.environ["S2_API_KEY"]
    if KEY_FILE.exists():
        for line in KEY_FILE.read_text().splitlines():
            if line.startswith("S2_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


_key_rejected = False


def s2_headers():
    key = None if _key_rejected else api_key()
    return {"x-api-key": key} if key else {}


def norm(title):
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def s2_id(link):
    """Semantic Scholar id for an arXiv or DOI link, else None."""
    link = link or ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", link)
    if m:
        return f"ARXIV:{m.group(1)}"
    m = re.search(r"(10\.\d{4,9}/[^\s?#]+)", link)
    if m:
        return "DOI:" + m.group(1).rstrip(".").removesuffix(".pdf").removesuffix("/full").removesuffix("/abstract")
    return None


def read_cache(path, key):
    cache = {}
    if path.exists():
        for line in open(path):
            rec = json.loads(line)
            cache[rec[key]] = rec["match"]
    return cache


def request(method, url, **kw):
    global _key_rejected
    for attempt in range(8):
        try:
            r = requests.request(method, url, headers=s2_headers(), timeout=120, **kw)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 403 and s2_headers():
            # An expired / revoked key is refused outright, while anonymous requests still work (slower pool).
            print("  WARNING: Semantic Scholar rejected the API key (403); continuing without it. "
                  "Check ~/.config/semanticscholar.env")
            _key_rejected = True
            continue
        return r
    return None


def lookup_ids(ids):
    cache = read_cache(ID_CACHE, "id")
    todo = sorted({i for i in ids if i not in cache})
    print(f"  id lookup: {len(todo)} new ({len(cache)} cached)")
    with open(ID_CACHE, "a") as f:
        for k in range(0, len(todo), 500):
            chunk = todo[k:k + 500]
            r = request("POST", BATCH_URL, params={"fields": FIELDS}, json={"ids": chunk})
            if r is None or r.status_code != 200:
                continue  # retry on the next run
            for i, rec in zip(chunk, r.json()):
                cache[i] = rec
                f.write(json.dumps({"id": i, "match": rec}) + "\n")
            time.sleep(1.1)
    return cache


def match_titles(titles):
    cache = read_cache(MATCH_CACHE, "title")
    todo = [t for t in dict.fromkeys(titles) if t not in cache]
    print(f"  title-matching {len(todo)} papers ({len(cache)} cached)", flush=True)
    with open(MATCH_CACHE, "a") as f:
        for title in todo:
            r = request("GET", MATCH_URL, params={"query": title, "fields": FIELDS})
            if r is None or r.status_code not in (200, 404):
                continue  # server error: leave uncached so the next run retries it
            data = r.json().get("data") if r.status_code == 200 else None  # 404 = no match for this title
            match = data[0] if data and norm(data[0]["title"]) == norm(title) else None
            cache[title] = match
            f.write(json.dumps({"title": title, "match": match}) + "\n")
            time.sleep(1.05)  # keyed limit is 1 request/s
    return cache


def main():
    use_os_certs()
    df, _ = load_mapped()
    ids = df["link"].map(s2_id)
    by_id = lookup_ids([i for i in ids if i])
    recs = [by_id.get(i) if i else None for i in ids]
    by_title = match_titles([t for t, r in zip(df["title"], recs) if r is None])
    recs = [r or by_title.get(t) for t, r in zip(df["title"], recs)]

    out = []
    for pid, year, rec in zip(df["pid"], df["year"], recs):
        if not rec:
            out.append({"pid": pid, "year": year})
            continue
        types = rec.get("publicationTypes") or []
        venue = rec.get("venue") or ""
        out.append({
            "pid": pid, "year": year,
            "citations": rec.get("citationCount"),
            "influential": rec.get("influentialCitationCount"),
            "s2_url": rec.get("url") or "",
            "s2_venue": venue,
            # Journal / conference papers count as peer reviewed; arXiv-only preprints don't.
            "peer_reviewed": bool(set(types) & {"JournalArticle", "Conference"}) and venue.lower() not in ("arxiv", "arxiv.org", ""),
        })
    ext = pd.DataFrame(out)
    # Citation percentile within each publication year, since older papers have had longer to accumulate citations.
    ext["citation_pct"] = ext.groupby("year")["citations"].rank(pct=True)
    ext = ext.drop(columns="year")
    ext.to_parquet(COMBINED / "external.parquet")
    matched = ext["citations"].notna()
    print(f"Matched {matched.sum()}/{len(ext)} papers ({matched.mean():.0%}); median citations {ext['citations'].median():.0f}")


if __name__ == "__main__":
    main()
