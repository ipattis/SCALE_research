"""Evidence dashboard: site/evidence.html, the page that answers "so what?" for the SCALE repository.

NeurIPS is big enough that the map itself is the insight. SCALE has ~1.7k papers, so it can be read
more closely: every paper has an AI-extracted evidence profile (02) audited against its abstract (07).
This page aggregates those profiles with SCALE's own tags:
  - how fast the repository grows, and whether the evidence mix keeps pace
  - an evidence gap map (SCALE application x SCALE "why use AI"), all papers vs causal studies only
  - what studies measure (learning vs perceptions vs AI output quality) and what causal studies find
  - sample sizes, countries, subjects and AI tools
  - topic growth (computed in the browser for the selected scope)
  - SCALE's study-design tags compared with what the abstracts describe
  - a searchable shortlist of the causal studies on learning
A scope filter (age band, AI type) re-renders everything. All numbers are computed in the page from
the embedded per-paper records, so the figures always agree with the filters.

Also writes data/insights.md: the headline numbers for the whole repository, for reports and the README.
"""
import json
from pathlib import Path

import pandas as pd

from common import COMBINED, DATA, FACETS, IMPACT_DESIGNS, PERIODS, SITE, TIER_HELP, TIERS, age_band, evidence_tier, load_mapped

WEB = Path(__file__).parent / "web"


def records(df, enrich, ext, audit, topic_map):
    ext = ext.set_index("pid")
    flagged = set(audit.loc[audit["flag"], "pid"]) if len(audit) else set()
    tm = topic_map.set_index("pid")
    mid = sorted(int(c[1:-3]) for c in topic_map.columns if c.endswith("_id"))
    mid = mid[len(mid) // 2]  # middle layer: specific enough to be interesting, big enough to count
    out = []
    for row in df.itertuples():
        e = enrich[row.pid]
        x = ext.loc[row.pid] if row.pid in ext.index else None
        tid = int(tm.loc[row.pid, f"L{mid}_id"])
        out.append({
            "id": row.pid,
            "t": row.title,
            "a": row.authors,
            "m": row.first_month,
            "p": row.period,
            "tier": TIERS.index(evidence_tier(e)),
            "st": e.get("study_type", ""),
            "out": e.get("outcomes", []),
            "dir": e.get("direction", ""),
            "n": int(e.get("sample_size") or 0),
            "c": e.get("countries", []),
            "s": e.get("subjects", []),
            "ai": e.get("ai_tools", []),
            "g": bool(e.get("generative_ai")),
            "band": age_band(row.age),
            **{k: list(getattr(row, k)) for k in FACETS},
            "f": e.get("key_finding", ""),
            "who": e.get("participants", ""),
            "link": row.link,
            "cites": None if x is None or pd.isna(x["citations"]) else int(x["citations"]),
            "peer": None if x is None or pd.isna(x["peer_reviewed"]) else bool(x["peer_reviewed"]),
            "topic": tid if tid >= 0 else None,
            "flag": row.pid in flagged,
        })
    names = topic_map.drop_duplicates(f"L{mid}_id").set_index(f"L{mid}_id")[f"L{mid}_name"].to_dict()
    return out, {int(k): v for k, v in names.items() if k >= 0}


def headline_md(df, enrich, ext, audit):
    """A few repository-wide numbers for data/insights.md (the page computes the same ones per scope)."""
    tiers = pd.Series([evidence_tier(enrich[p]) for p in df["pid"]])
    learning = pd.Series(["Learning / achievement" in enrich[p].get("outcomes", []) for p in df["pid"]])
    k12 = df["age"].map(lambda a: bool(set(a) & {"Elementary (PK5)", "Middle School (6-8)", "High School (9-12)"}))
    causal = tiers.isin(TIERS[:2])
    scale_rct = df["design"].map(lambda d: "Impact – Randomized Controlled Trial" in d)
    llm_rct = pd.Series([enrich[p]["study_type"] == "Randomized controlled trial" for p in df["pid"]])
    peer = ext.set_index("pid").reindex(df["pid"])["peer_reviewed"]
    n = len(df)
    lines = [
        "# SCALE repository: headline numbers\n",
        f"{n:,} papers with abstracts. Evidence profiles are AI-extracted from abstracts and audited (see audit_report.md).\n",
        f"- Papers by first appearance: " + ", ".join(f"{p}: {(df['period'] == p).sum()}" for p in PERIODS),
        f"- Measure learning / achievement: {learning.mean():.0%}",
        f"- Causal designs (RCT or quasi-experiment): {causal.sum()} ({causal.mean():.0%}); "
        f"of those, measuring learning: {(tiers == TIERS[0]).sum()}",
        f"- K-12 tagged: {k12.mean():.0%}; causal evidence on learning in K-12: {((tiers == TIERS[0]) & k12).sum()}",
        f"- Peer reviewed (per Semantic Scholar): {(peer == True).mean():.0%}",
        f"- SCALE tags {scale_rct.sum()} papers as RCTs; the abstract describes random assignment in "
        f"{(scale_rct & llm_rct).sum()} of them, and {(llm_rct & ~scale_rct).sum()} untagged papers describe one.",
        "", "## Evidence tiers", "",
        *[f"- {t}: {(tiers == t).sum()} ({(tiers == t).mean():.0%})" for t in TIERS],
    ]
    return "\n".join(lines) + "\n"


def main():
    df, enrich = load_mapped()
    ext = pd.read_parquet(COMBINED / "external.parquet")
    audit_path = COMBINED / "audit.parquet"
    if audit_path.exists():
        a = pd.read_parquet(audit_path)
        text = ["summary", "problem", "method", "eli5", "key_finding", "takeaway"]
        a["flag"] = (a[text] == "unsupported").any(axis=1) | (a[["study_type", "direction", "sample_size"]] == "wrong").any(axis=1)
        audit_stats = {
            "n": len(a),
            "flagged": int(a["flag"].sum()),
            "study_type": {k: round(float(v), 3) for k, v in a["study_type"].value_counts(normalize=True).items()},
            "direction": {k: round(float(v), 3) for k, v in a["direction"].value_counts(normalize=True).items()},
        }
    else:
        a, audit_stats = pd.DataFrame({"pid": [], "flag": []}), None
    topic_map = pd.read_parquet(COMBINED / "map_topic.parquet")
    recs, topic_names = records(df, enrich, ext, a, topic_map)

    data = {
        "papers": recs,
        "topics": topic_names,
        "tiers": TIERS,
        "tierHelp": TIER_HELP,
        "periods": PERIODS,
        "facets": {k: {"label": FACETS[k][0], "options": FACETS[k][1]} for k in FACETS},
        "impactDesigns": sorted(IMPACT_DESIGNS),
        "audit": audit_stats,
        "built": pd.Timestamp.now().strftime("%Y-%m-%d"),
    }
    html = (WEB / "evidence.html").read_text()
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    out = SITE / "evidence.html"
    out.write_text(html)
    (DATA / "insights.md").write_text(headline_md(df, enrich, ext, a))
    print(f"Saved {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    print((DATA / "insights.md").read_text())


if __name__ == "__main__":
    main()
