"""Faithfulness audit of the LLM-written paper notes and evidence profile (LLM as judge).

NeurIPS audited a 600-paper sample; SCALE is small enough to audit EVERY paper. A judge compares each
enrichment against the abstract, rating the text fields for faithfulness and checking the structured
evidence fields (study type, result direction, sample size) that the evidence dashboard counts.
Runs through Bedrock batch. Writes data/audit_report.md and data/combined/audit.parquet (per-paper
verdicts, used to badge flagged papers in the map). Verdicts are cached per paper.
Usage: python 07_audit.py
"""
import os

import pandas as pd

from bedrock_batch import run_batch
from common import COMBINED, DATA, load_mapped, tool_request, tool_result

# Sonnet 4.6: strongest Sonnet that Bedrock supports for batch inference (5.5 is on-demand only).
JUDGE_MODEL = os.environ.get("SCALE_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-6")
TEXT_FIELDS = ["summary", "problem", "method", "eli5", "key_finding", "takeaway"]
GIVEN_FIELDS = TEXT_FIELDS + ["study_type", "direction", "sample_size", "data_size"]
CHECK_FIELDS = ["study_type", "direction", "sample_size"]

TOOL = {
    "name": "record_audit",
    "description": "Record whether each generated field is faithful to the abstract.",
    "input_schema": {
        "type": "object",
        "properties": {
            **{f: {"type": "string", "enum": ["faithful", "minor_issue", "unsupported"]} for f in TEXT_FIELDS},
            **{f: {"type": "string", "enum": ["correct", "debatable", "wrong"]} for f in CHECK_FIELDS},
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Each specific claim or label that the abstract does not support (empty if none).",
            },
        },
        "required": TEXT_FIELDS + CHECK_FIELDS + ["issues"],
    },
}

PROMPT = """Check generated notes about an education research paper against its abstract.
Text fields:
- faithful: everything stated is supported by (or a fair simplification of) the abstract
- minor_issue: slight overstatement or vague imprecision, not misleading
- unsupported: states a result, method, number, recommendation or claim that the abstract does not support
The plain-language (eli5) field may simplify; judge only whether its substance is correct. The takeaway must not
recommend more than the findings support.
Labels (study_type, direction, sample_size): correct / debatable (a reasonable reader could choose otherwise) /
wrong. sample_size counts HUMAN participants only; 0 means "not stated" and is correct when the abstract gives no
count of people (counts of messages, records, papers or institutions belong in data_size, not sample_size).
Study types: "Pre-post, single group" requires measurement before and after; a post-only survey is "Correlational /
survey"; analyses of logs or chats are "Descriptive analysis of usage or interaction data"; pilots and feasibility
deployments of a new tool are "Design / product development"; only reviews with a systematic search are
"Systematic review / meta-analysis".

Title: {title}
Abstract: {abstract}

Generated:
{generated}"""


def main():
    papers, enrich = load_mapped()
    audit_path = COMBINED / "audit.parquet"
    previous = pd.read_parquet(audit_path) if audit_path.exists() else pd.DataFrame(columns=["pid"])
    # Re-audit any paper whose enrichment changed since it was audited (e.g. after a --refresh-fields run).
    version = {p: enrich[p].get("prompt_version", 1) for p in papers["pid"]}
    audited = dict(zip(previous["pid"], previous.get("enrich_version", pd.Series(1, index=previous.index)))) if len(previous) else {}
    todo = papers[papers["pid"].map(lambda p: audited.get(p) != version[p])]
    requests = {}
    for row in todo.itertuples():
        gen = "\n".join(f"{f}: {enrich[row.pid].get(f, 'Not stated')}" for f in GIVEN_FIELDS)
        requests[row.pid] = tool_request(PROMPT.format(title=row.title, abstract=row.abstract, generated=gen), TOOL, 800, model=JUDGE_MODEL)

    results = run_batch(f"audit-v{max(version.values())}-n{len(requests)}", JUDGE_MODEL, requests)
    rows = []
    for pid, resp in results.items():
        try:
            rows.append({"pid": pid, **tool_result(resp["content"]), "enrich_version": version[pid]})
        except Exception:
            pass
    audit = pd.concat([previous, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("pid", keep="last")
    audit["issues"] = audit["issues"].apply(lambda x: list(x) if x is not None else [])
    audit.to_parquet(audit_path)
    audit = audit.merge(papers[["pid", "title"]], on="pid")

    lines = ["# Enrichment faithfulness audit\n",
             f"Judge: `{JUDGE_MODEL}`. **Every** mapped paper is audited ({len(audit)} of {len(papers)}), not a sample. "
             f"Enrichment prompt version: {sorted(set(version.values()))}.\n",
             "The judge is the same model family as the enrichment model, so treat this as a consistency check rather "
             "than independent verification.\n",
             "| Field | Faithful | Minor issue | Unsupported |", "|---|---|---|---|"]
    for f in TEXT_FIELDS:
        vc = audit[f].value_counts(normalize=True)
        lines.append(f"| {f} | {vc.get('faithful', 0):.1%} | {vc.get('minor_issue', 0):.1%} | {vc.get('unsupported', 0):.1%} |")
    lines += ["", "| Label | Correct | Debatable | Wrong |", "|---|---|---|---|"]
    for f in CHECK_FIELDS:
        vc = audit[f].value_counts(normalize=True)
        lines.append(f"| {f} | {vc.get('correct', 0):.1%} | {vc.get('debatable', 0):.1%} | {vc.get('wrong', 0):.1%} |")
    bad = audit[(audit[TEXT_FIELDS] == "unsupported").any(axis=1) | (audit[CHECK_FIELDS] == "wrong").any(axis=1)]
    lines.append(f"\n**Papers with at least one unsupported field or wrong label: {len(bad)} / {len(audit)} "
                 f"({len(bad) / max(len(audit), 1):.1%}).** These are flagged in the map's detail panel.\n")
    lines.append("## Examples of flagged issues\n")
    for row in bad.head(20).itertuples():
        lines.append(f"- *{row.title}*: " + "; ".join(row.issues[:2]))
    (DATA / "audit_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:20]))


if __name__ == "__main__":
    main()
