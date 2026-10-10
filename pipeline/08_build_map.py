"""Render the three interactive maps (topic / problem / AI use) with datamapplot.

    python 08_build_map.py            # self-contained HTML files in site/ (open directly)
    python 08_build_map.py --hosted   # site/ + split data files, for GitHub Pages / any static host

Each page shares the same toolbar: switch view (and to the evidence dashboard from 09_insights.py),
filter by period, filter by SCALE's facets and the evidence tier, "where does my paper fit?", and a
reading list (saved in the browser, exportable as CSV / RIS) that works across all pages.
"""
import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

import datamapplot
import numpy as np
import pandas as pd
from datamapplot.selection_handlers import SelectionHandlerBase

from common import (
    COMBINED, FACETS, PERIODS, SEARCH_DIM, SEARCH_MODEL_ONNX, SITE, TIER_HELP, TIERS, UNDATED, VIEWS, age_band,
    evidence_tier, load_mapped,
)

WEB = Path(__file__).parent / "web"
PAGES = {
    "topic": ("index.html", "By topic", "Papers placed by what they are about"),
    "problem": ("problem.html", "By need", "Papers placed by the educational problem they tackle"),
    "method": ("method.html", "By AI use", "Papers placed by how AI is used or studied"),
}
TITLES = {
    "topic": "AI in education research: the map",
    "problem": "AI in education research: the needs map",
    "method": "AI in education research: how AI is used",
}
N_TOP = 12

HOVER_TEMPLATE = """
<div style="max-width:440px">
  <div style="font-size:15px;font-weight:600;line-height:1.25;margin-bottom:4px">{hover_text}</div>
  <div style="font-size:11px;opacity:.7;margin-bottom:6px">{period} &middot; {tier} &middot; {cluster}</div>
  <div style="font-size:12.5px;line-height:1.4;margin-bottom:6px"><b>In plain terms:</b> {eli5}</div>
  <div style="font-size:12px;line-height:1.4;opacity:.85"><b>Finding:</b> {key_finding}</div>
  <div style="font-size:10.5px;opacity:.55;margin-top:6px">Click for details</div>
</div>
"""


def github_repo():
    """'owner/name' of this repo's GitHub origin (for the toolbar's star/fork counters), or None."""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True,
                             cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def load_trends(view):
    path = COMBINED / f"trends_{view}.json"
    return json.loads(path.read_text()) if path.exists() else {"periods": [], "layers": {}}


def top_or_other(values, n=N_TOP, empty="Not stated"):
    s = pd.Series([v or empty for v in values])
    keep = set(s[s != empty].value_counts().head(n).index)
    return np.where(s.isin(keep) | (s == empty), s, "Other").astype(object)


def first(values, order):
    """The first SCALE tag in display order, for colouring by a multi-valued facet."""
    vals = set(values)
    return next((o for o in order if o in vals), "Not tagged")


def build_view(view, papers, enrich, ext, audit, search_b64, hosted):
    m = pd.read_parquet(COMBINED / f"map_{view}.parquet")
    df = papers.merge(m, on="pid").merge(ext, on="pid", how="left").merge(audit, on="pid", how="left")
    # The browser search index is in load_mapped() order; the map points must be too.
    assert (df["pid"].to_numpy() == papers["pid"].to_numpy()).all()
    layers = sorted(int(c[1:-3]) for c in m.columns if c.endswith("_id"))
    trends = load_trends(view)
    note_layer = next((l for l in layers[1:-1] if str(l) in trends["layers"]), layers[min(1, len(layers) - 1)])

    def field(f, default=""):
        return [enrich[p].get(f, default) for p in df["pid"]]

    def joined(values):
        return ["; ".join(v) if isinstance(v, (list, np.ndarray)) else (v or "") for v in values]

    names = [df[f"L{l}_name"].to_numpy() for l in layers]  # finest first
    tags = joined(field("tags", []))
    path = [" › ".join(dict.fromkeys(n[i] for n in reversed(names) if n[i] != "Unlabelled")) for i in range(len(df))]
    trend_key = [f"{note_layer}:{t}" if t >= 0 else "" for t in df[f"L{note_layer}_id"]]
    trend_lookup = {f"{l}:{tid}": info for l, topics in trends["layers"].items() for tid, info in topics.items()}
    growth_layer = str(layers[min(1, len(layers) - 1)])
    growth = np.array([
        trends["layers"].get(growth_layer, {}).get(str(t), {}).get("growth", np.nan) if t >= 0 else np.nan
        for t in df[f"L{growth_layer}_id"]
    ], dtype=float)

    tier = [evidence_tier(enrich[p]) for p in df["pid"]]
    outcomes = field("outcomes", [])
    sample = np.array(field("sample_size", 0), dtype=float)
    citations = df["citations"].astype("Int64")
    peer = df["peer_reviewed"].astype("boolean")
    measured_learning = ["Learning / achievement" in o for o in outcomes]
    countries = [c[0] if len(c) == 1 else ("Multiple countries" if len(c) > 1 else "") for c in field("countries", [])]
    tools = [t[0] if len(t) else "" for t in field("ai_tools", [])]

    extra = pd.DataFrame({
        "pid": df["pid"],
        "period": df["period"],
        "month": df["first_month"],
        "cluster": names[0],
        "path": path,
        "authors": df["authors"],
        "publication": df["publication"],
        "summary": field("summary"),
        "eli5": field("eli5"),
        "problem": field("problem"),
        "method": field("method"),
        "key_finding": field("key_finding"),
        "takeaway": field("takeaway"),
        "study_type": field("study_type"),
        "tier": tier,
        "direction": field("direction"),
        "outcomes": joined(outcomes),
        "sample_size": [str(int(n)) if n > 0 else "" for n in sample],
        "participants": field("participants"),
        "countries": joined(field("countries", [])),
        "subjects": joined(field("subjects", [])),
        "ai_tools": joined(field("ai_tools", [])),
        "abstract": df["abstract"],
        "tags": tags,
        **{f"f_{k}": joined(df[k]) for k in FACETS},  # SCALE's own facet tags
        "link": df["link"],
        "url": df["url"],
        "s2_url": df["s2_url"].fillna(""),
        "citations": citations.astype(str).replace("<NA>", ""),
        "citation_pct": (df["citation_pct"] * 100).round(1).fillna("").astype(str),
        "peer": np.where(peer.isna(), "", np.where(peer.fillna(False), "Peer reviewed", "Preprint / working paper")),
        "trend_key": trend_key,
        **{f"tid{l}": df[f"L{l}_id"].astype(str) for l in layers},  # per-layer topic ids for the Trends panel
        "audit_flag": df["audit_flag"].fillna(""),
        "search": df["title"] + " | " + df["authors"] + " | " + pd.Series(tags) + " | " + pd.Series(path)
                  + " | " + pd.Series(joined(field("ai_tools", []))) + " | " + pd.Series(joined(field("countries", []))),
    })

    colormap_rawdata = [
        df["period"].to_numpy(),
        np.nan_to_num(np.clip(growth, -1.5, 1.5)),  # unclustered points read as "no change"
        np.array(tier, dtype=object),
        np.where(measured_learning, "Measures learning", "Doesn't measure learning"),
        np.array([d or "Not applicable" for d in field("direction")], dtype=object),
        np.array([first(v, FACETS["design"][1]) for v in df["design"]], dtype=object),
        np.array([first(v, FACETS["application"][1]) for v in df["application"]], dtype=object),
        np.array([age_band(v) for v in df["age"]], dtype=object),
        np.where(field("generative_ai", False), "Generative AI / LLM", "Other AI"),
        top_or_other(tools, empty="No tool named"),
        top_or_other(countries),
        np.log10(np.where(sample > 0, sample, 1)),
        np.log10(citations.astype(float).fillna(0).to_numpy() + 1),
        np.where(peer.isna(), "Not found in Semantic Scholar", np.where(peer.fillna(False), "Peer reviewed", "Preprint / working paper")),
    ]
    colormap_metadata = [
        {"field": "period", "description": "First appeared", "cmap": "viridis", "kind": "categorical"},
        {"field": "growth", "description": f"Topic growth {PERIODS[-2]}→{PERIODS[-1]} (share, log2)", "cmap": "RdYlGn", "kind": "continuous"},
        {"field": "tier", "description": "Evidence tier (derived)", "cmap": "tab10", "kind": "categorical"},
        {"field": "learning", "description": "Measures learning outcomes?", "cmap": "Set1", "kind": "categorical"},
        {"field": "direction", "description": "Direction of main result", "cmap": "Set2", "kind": "categorical"},
        {"field": "design", "description": "SCALE study design (first tag)", "cmap": "tab10", "kind": "categorical"},
        {"field": "application", "description": "SCALE application (first tag)", "cmap": "tab10", "kind": "categorical"},
        {"field": "age", "description": "SCALE age band", "cmap": "Set2", "kind": "categorical"},
        {"field": "genai", "description": "Generative AI?", "cmap": "Paired", "kind": "categorical"},
        {"field": "tool", "description": f"AI tool named (top {N_TOP})", "cmap": "tab20", "kind": "categorical"},
        {"field": "country", "description": f"Country (top {N_TOP})", "cmap": "tab20", "kind": "categorical"},
        {"field": "n", "description": "Sample size (log10 participants)", "cmap": "viridis", "kind": "continuous"},
        {"field": "cites", "description": "Citations (log10, Semantic Scholar)", "cmap": "viridis", "kind": "continuous"},
        {"field": "peer", "description": "Peer reviewed vs preprint", "cmap": "Set1", "kind": "categorical"},
    ]

    # Facet filters offered in the Filter panel: the column in `extra` and its options in display order.
    filters = [
        {"key": "tier", "label": "Evidence tier", "options": TIERS, "multi": False, "help": TIER_HELP},
        *[{"key": f"f_{k}", "label": FACETS[k][0], "options": FACETS[k][1], "multi": True} for k in ("age", "application", "user", "why", "design")],
        {"key": "outcomes", "label": "Outcome measured", "multi": True, "options": [
            "Learning / achievement", "Behaviour / engagement", "Perceptions / attitudes", "Teacher practice / workload",
            "AI output quality / accuracy"]},
        {"key": "direction", "label": "Direction of result", "multi": False, "options": ["Positive", "Mixed", "No effect", "Negative"]},
    ]

    level_names = ["Broad areas", "Topics", "Fine topics", "Finest"]
    trend_layers = [{"layer": l, "label": level_names[i]} for i, l in enumerate(sorted(layers, reverse=True))
                    if str(l) in trends["layers"]]
    counts = df["period"].value_counts()
    config = {
        "view": view,
        "views": [{"key": k, "file": f, "label": l, "help": h} for k, (f, l, h) in PAGES.items()],
        "periods": PERIODS,
        "periodCounts": {p: int(counts.get(p, 0)) for p in PERIODS + [UNDATED]},
        "repo": github_repo(),
        "latestPeriod": PERIODS[-1],
        "trends": trend_lookup,
        "trendLayers": trend_layers,
        "filters": filters,
        "searchIndexB64": search_b64,
        "searchDim": SEARCH_DIM,
        "searchModel": SEARCH_MODEL_ONNX,
        "searchInstruction": f"Instruct: {VIEWS['topic']}\nQuery: ",
    }
    custom_js = "window.NX = " + json.dumps(config, ensure_ascii=False) + ";\n" + (WEB / "app.js").read_text()
    page, _, _ = PAGES[view]

    kwargs = {}
    if hosted:
        # Point/label/metadata are written as data_<view>_*.zip next to the HTML and fetched on load.
        kwargs.update(inline_data=False, offline_data_path=SITE / f"data_{view}")

    plot = datamapplot.create_interactive_plot(
        df[["x", "y"]].to_numpy(),
        *names,
        hover_text=df["title"].to_numpy(),
        hover_text_html_template=HOVER_TEMPLATE,
        extra_point_data=extra,
        title=TITLES[view],
        sub_title=f"{len(df):,} papers in the Stanford SCALE AI Hub repository. Zoom for detail, hover to preview, "
                  f"click to read. {PAGES[view][2]}.",
        enable_search=True,
        search_field="search",
        enable_topic_tree=True,
        on_click="showPaper(index);",
        # datamapplot only enables lasso selection when a selection handler exists. A bare handler keeps
        # lasso (used by "+ Add N to reading list" in app.js) without the pull-up table drawer.
        selection_handler=SelectionHandlerBase(),
        colormap_rawdata=colormap_rawdata,
        colormap_metadata=colormap_metadata,
        custom_html=(WEB / "app.html").read_text(),
        custom_css=(WEB / "app.css").read_text(),
        custom_js=custom_js,
        font_family="Roboto",
        title_font_size=22,  # points
        sub_title_font_size=12,
        cluster_boundary_polygons=True,
        cluster_boundary_line_width=6,
        point_radius_max_pixels=16,  # fewer points than NeurIPS, so each can be bigger
        point_radius_min_pixels=3,
        text_outline_width=6,
        initial_zoom_fraction=0.92,
        **kwargs,
    )
    out = SITE / page
    plot.save(str(out))
    # datamapplot omits a viewport tag, so phones would render the desktop layout zoomed out.
    html = out.read_text()
    out.write_text(html.replace("<head>", '<head>\n    <meta name="viewport" content="width=device-width, initial-scale=1" />', 1))
    print(f"{view}: saved {out.name} ({out.stat().st_size / 1e6:.1f} MB), topics per layer={[len(set(n) - {'Unlabelled'}) for n in names]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosted", action="store_true", help="write split data files for static hosting")
    ap.add_argument("--views", default=",".join(VIEWS))
    args = ap.parse_args()

    papers, enrich = load_mapped()
    ext = pd.read_parquet(COMBINED / "external.parquet")
    audit_path = COMBINED / "audit.parquet"
    if audit_path.exists():
        a = pd.read_parquet(audit_path)
        text_fields = ["summary", "problem", "method", "eli5", "key_finding", "takeaway"]
        bad = (a[text_fields] == "unsupported").any(axis=1) | (a[["study_type", "direction", "sample_size"]] == "wrong").any(axis=1)
        audit = pd.DataFrame({"pid": a["pid"], "audit_flag": np.where(bad, a["issues"].apply(lambda x: "; ".join(list(x)[:2])), "")})
    else:
        audit = pd.DataFrame({"pid": [], "audit_flag": []})
    # int8 search index for the browser, in the same paper order as the map points
    z = np.load(COMBINED / "emb_search.npz", allow_pickle=False)
    lookup = dict(zip(z["pids"], range(len(z["pids"]))))
    vecs = z["vectors"][[lookup[p] for p in papers["pid"]]]
    search_b64 = base64.b64encode(np.clip(np.round(vecs * 127), -127, 127).astype(np.int8).tobytes()).decode()

    if args.hosted:
        for f in SITE.glob("data_*.zip"):
            f.unlink()
    for view in args.views.split(","):
        build_view(view, papers, enrich, ext, audit, search_b64, args.hosted)


if __name__ == "__main__":
    main()
