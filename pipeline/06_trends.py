"""Per-topic growth across periods + LLM "what's new" notes.

Growth is computed from counts (not by the LLM): each topic's share of the latest period's
papers vs. its share of the previous period's, with +1 smoothing so tiny topics don't swing wildly.
Periods are by when a paper first appeared (arXiv id, else SCALE's date): ≤2023, 2024, 2025, 2026.
The latest period is a partial year, which is why growth compares *shares*, never raw counts.

With ~1.7k papers, many topics have only a handful of papers per period, so topics with fewer than
MIN_LATEST papers in the latest period are labelled "Small" instead of being called surging or shrinking.
Output: data/combined/trends_<view>.json  {layer: {topic_id: {...}}}
"""
import argparse
import json
import random

import numpy as np
import pandas as pd

from bedrock_batch import run_batch
from common import COMBINED, ENRICH_MODEL, PERIODS, VIEWS, load_mapped, tool_request, tool_result

N_EXAMPLES = 10
MIN_LATEST = 6

NOTE_TOOL = {
    "name": "record_trend_note",
    "description": "Describe how a research topic changed in the latest period.",
    "input_schema": {
        "type": "object",
        "properties": {
            "whats_new": {
                "type": "string",
                "description": "1-2 plain-language sentences on what is new or different in the latest period's papers.",
            }
        },
        "required": ["whats_new"],
    },
}

NOTE_PROMPT = """Topic on a map of research on AI in education: "{name}".
Its share of papers went from {prev_share:.1%} in {prev} to {last_share:.1%} in {last} (so far).

Sample of {last} papers in this topic:
{last_papers}

Sample of earlier papers in this topic:
{prev_papers}

In 1-2 plain sentences, say what is new or shifting in {last} compared with earlier papers
(new problems, uses of AI, study designs, learners or settings). Be specific and only use what the samples show."""


def trend_label(ratio, n_last):
    if n_last < MIN_LATEST:
        return "Small"
    if ratio >= 1.6:
        return "Surging"
    if ratio >= 1.15:
        return "Growing"
    if ratio <= 0.65:
        return "Shrinking"
    if ratio <= 0.87:
        return "Cooling"
    return "Steady"


def sample_lines(sub, enrich, k, rng):
    pids = list(sub["pid"])
    rng.shuffle(pids)
    titles = dict(zip(sub["pid"], sub["title"]))
    return "\n".join(f"- {titles[p]}: {enrich[p]['summary']}" for p in pids[:k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", default=",".join(VIEWS))
    args = ap.parse_args()
    papers, enrich = load_mapped()
    last, prev = PERIODS[-1], PERIODS[-2]
    totals = papers["period"].value_counts()
    rng = random.Random(0)

    for view in args.views.split(","):
        m = pd.read_parquet(COMBINED / f"map_{view}.parquet").merge(papers[["pid", "period", "title"]], on="pid")
        layers = sorted(int(c[1:-3]) for c in m.columns if c.endswith("_id"))
        # Notes for the middle layers: specific enough to be interesting, few enough to be cheap.
        note_layers = layers[1:-1] if len(layers) > 2 else layers
        trends, requests = {}, {}
        for layer in layers:
            trends[layer] = {}
            for tid, sub in m[m[f"L{layer}_id"] >= 0].groupby(f"L{layer}_id"):
                counts = sub["period"].value_counts()
                share = {p: (counts.get(p, 0) + 1) / (totals.get(p, 0) + 1) for p in PERIODS}
                ratio = share[last] / share[prev]
                info = {
                    "name": sub[f"L{layer}_name"].iloc[0],
                    "counts": {p: int(counts.get(p, 0)) for p in PERIODS},
                    "growth": round(float(np.log2(ratio)), 3),
                    "label": trend_label(ratio, int(counts.get(last, 0))),
                }
                trends[layer][int(tid)] = info
                latest, earlier = sub[sub["period"] == last], sub[sub["period"].isin(PERIODS[:-1])]
                if layer in note_layers and len(latest) >= 3 and len(earlier) >= 3:
                    prompt = NOTE_PROMPT.format(
                        name=info["name"], prev=prev, last=last, prev_share=share[prev], last_share=share[last],
                        last_papers=sample_lines(latest, enrich, N_EXAMPLES, rng),
                        prev_papers=sample_lines(earlier, enrich, N_EXAMPLES, rng),
                    )
                    requests[f"{view}-L{layer}-T{tid}"] = tool_request(prompt, NOTE_TOOL, max_tokens=300, model=ENRICH_MODEL)

        results = run_batch(f"trends-{view}-n{len(requests)}", ENRICH_MODEL, requests)
        for key, resp in results.items():
            _, l, t = key.rsplit("-", 2)
            try:
                trends[int(l[1:])][int(t[1:])]["whats_new"] = tool_result(resp["content"])["whats_new"]
            except Exception:
                pass
        (COMBINED / f"trends_{view}.json").write_text(json.dumps({"periods": PERIODS, "layers": trends}, indent=1, ensure_ascii=False))
        mid = trends[layers[1 if len(layers) > 1 else 0]].values()
        surging = sorted((v for v in mid if v["label"] != "Small"), key=lambda v: -v["growth"])[:5]
        print(f"{view}: {sum(len(v) for v in trends.values())} topics, {len(results)} notes. Fastest growing:",
              [f"{v['name']} ({2 ** v['growth']:.1f}x)" for v in surging])


if __name__ == "__main__":
    main()
