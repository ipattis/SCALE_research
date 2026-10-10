"""Fetch the Stanford SCALE AI Hub research repository: listing pages + one detail page per paper.

The listing cards (/ai/repository?page=N) give title, authors, date, publisher and SCALE's five
human-curated facets. Each paper's page adds the abstract and the external link. Pages are cached
in data/pages_cache.jsonl, so a re-run (e.g. next month, when the repository grows) only fetches
papers it hasn't seen. /ai/repository is allowed by the site's robots.txt; requests are throttled.

Output: data/papers.parquet (one row per paper; facet columns are lists of SCALE labels).
Usage: python 01_fetch.py [--refresh-listing-only]
"""
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from common import BASE_URL, DATA, FACETS, LIST_URL, PAPERS, use_os_certs

CACHE = DATA / "pages_cache.jsonl"
HEADERS = {"User-Agent": "Mozilla/5.0 (research map; contact via scale.stanford.edu repository feedback)"}
WORKERS = 4
LABEL_TO_FACET = {label: key for key, (label, _) in FACETS.items()}


def get(url, tries=4):
    for k in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 ** k)
    raise RuntimeError(f"failed to fetch {url}")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_card(card):
    m = re.search(r'<h5><a href="/ai/repository/([^"]+)"[^>]*>(.*?)</a></h5>', card, re.S)
    if not m:
        return None
    rec = {"pid": m.group(1), "title": text(m.group(2))}
    byline = re.search(r"</h5>\s*<p>(.*?)</p>", card, re.S)
    b = text(byline.group(1)) if byline else ""
    d = re.search(r"\((\d{2})/(\d{4})\)", b)
    rec["authors"] = b[: d.start()].strip(" .") if d else b
    rec["month"] = f"{d.group(2)}-{d.group(1)}" if d else ""
    pub = re.search(r"<em>(.*?)</em>", byline.group(1), re.S) if byline else None
    rec["publication"] = text(pub.group(1)) if pub else ""
    for key in FACETS:
        rec[key] = []
    for label, body in re.findall(r"<strong>(.*?)</strong>:?(.*?)(?:<br>|</p>)", card, re.S):
        key = LABEL_TO_FACET.get(text(label).rstrip(":"))
        if key:
            rec[key] = [text(a) for a in re.findall(r"<a [^>]*>(.*?)</a>", body, re.S)]
    return rec


def fetch_listing():
    papers, page = {}, 0
    while True:
        s = get(LIST_URL.format(page=page))
        cards = [c for c in (parse_card(c) for c in s.split('<div class="card">')[1:]) if c]
        new = [c for c in cards if c["pid"] not in papers]
        if not new:
            break
        for c in new:
            c["list_order"] = len(papers)
            papers[c["pid"]] = c
        print(f"  listing page {page}: {len(cards)} cards ({len(papers)} total)", flush=True)
        page += 1
        time.sleep(0.5)
    return papers


def field(s, name):
    """Inner HTML of the first `field--name-<name>` block (field bodies don't nest divs)."""
    m = re.search(rf'field--name-{name}\b[^>]*>(.*?)</div>\s*(?:</div>|<div)', s, re.S)
    return m.group(1) if m else ""


def parse_detail(pid, s):
    # The page template also uses field--name-body for sidebar blocks (logo, feedback text), so read
    # only the paper's own node, which starts at its publication fields.
    start = s.find("field--name-field-pub-authors")
    node = s[start:] if start >= 0 else s
    m = re.search(r'field--name-body\b[^>]*>(.*?)</div>', node, re.S)
    link_html = re.search(r'field--name-field-pub-link\b.*?field__item">(.*?)</div>', node, re.S)
    link_html = link_html.group(1) if link_html else ""
    href = re.search(r'href="([^"]+)"', link_html)
    # Some entries hold the link as plain text rather than an anchor.
    link = href.group(1) if href else text(link_html)
    return {"pid": pid, "abstract": text(m.group(1)) if m else "", "link": html.unescape(link)}


def load_cache():
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            rec = json.loads(line)
            cache[rec["pid"]] = rec
    return cache


def main():
    use_os_certs()
    print("Fetching listing pages…")
    papers = fetch_listing()
    cache = load_cache()
    todo = [p for p in papers if p not in cache]
    print(f"{len(papers)} papers listed, {len(cache)} detail pages cached, {len(todo)} to fetch")

    def one(pid):
        s = get(f"{BASE_URL}/ai/repository/{pid}")
        return parse_detail(pid, s) if s else {"pid": pid, "abstract": "", "link": "", "missing": True}

    with open(CACHE, "a") as f, ThreadPoolExecutor(WORKERS) as pool:
        for k, rec in enumerate(pool.map(one, todo), 1):
            f.write(json.dumps(rec) + "\n")
            cache[rec["pid"]] = rec
            if k % 100 == 0:
                print(f"  {k}/{len(todo)} detail pages", flush=True)

    df = pd.DataFrame([{**p, **{k: cache[p["pid"]].get(k, "") for k in ("abstract", "link")}} for p in papers.values()])
    df["url"] = BASE_URL + "/ai/repository/" + df["pid"]
    # SCALE's date is often the latest arXiv *version* (e.g. 2507.16013v2 is dated 08/2026). For trends,
    # use when the paper first appeared: the arXiv id's YYMM where there is one, else SCALE's date.
    yymm = df["link"].str.extract(r"arxiv\.org/(?:abs|pdf)/(\d{2})(\d{2})\.\d{4,5}")
    first = ("20" + yymm[0] + "-" + yymm[1]).where(yymm[0].notna(), df["month"])
    df["first_month"] = first.where(first.str.match(r"20[12]\d-\d\d"), "")  # drops placeholder dates like 1970-01
    df["year"] = df["first_month"].str[:4]
    df = df.sort_values("list_order").reset_index(drop=True)
    n_abs = (df["abstract"].str.len() > 100).sum()
    print(f"Saved {len(df)} papers ({n_abs} with abstracts) to {PAPERS.name}")
    print("Papers per year first posted:", df["year"].value_counts().sort_index().to_dict())
    for key in FACETS:
        print(f"  {key}: {(df[key].str.len() == 0).sum()} untagged; top", df[key].explode().value_counts().head(3).to_dict())
    df.to_parquet(PAPERS)


if __name__ == "__main__":
    main()
