"""Per-paper LLM enrichment via Bedrock batch: plain-language notes plus a structured evidence profile.

The corpus is small (~1.7k papers vs ~19k for NeurIPS), so each paper gets a richer extraction than
the NeurIPS map: besides summary / problem / ELI5, the model records what kind of evidence the
abstract reports (study type, what was measured, direction of the result, sample size, country,
subject, AI tool). Those fields drive the evidence dashboard (09_insights.py) and answer the
repository's own caveat that it "does not bring a research quality or 'so what?' screen".

Everything is extracted from the abstract only, and "not stated" is always an allowed answer.
The evidence *tier* is not asked of the LLM: 09_insights.py derives it with a fixed, published rule.

Already-enriched papers are skipped, so re-running only processes new papers.
Usage: python 02_enrich.py [--limit N] [--on-demand] [--refresh-fields study_type,sample_size,data_size,takeaway]
"""
import argparse
import json

from bedrock_batch import run_batch
from common import ENRICH_MODEL, ENRICHMENT, load_enrichment, load_papers, tool_request, tool_result

STUDY_TYPES = [
    "Randomized controlled trial",
    "Quasi-experimental (comparison group, not randomized)",
    "Pre-post, single group",
    "Correlational / survey",
    "Descriptive analysis of usage or interaction data",
    "Qualitative / case study",
    "Design / product development",
    "Technical evaluation of AI (no learner outcomes)",
    "Systematic review / meta-analysis",
    "Other review / conceptual / position",
    "Unclear from abstract",
]
OUTCOMES = [
    "Learning / achievement",
    "Behaviour / engagement",
    "Perceptions / attitudes",
    "Teacher practice / workload",
    "AI output quality / accuracy",
    "None reported",
]
DIRECTIONS = ["Positive", "Mixed", "No effect", "Negative", "Not applicable"]
SUBJECTS = [
    "Mathematics", "Reading / writing / language arts", "Second / foreign language", "Science",
    "Computer science / programming", "Medicine / health professions", "Engineering", "Social sciences / humanities",
    "Business / economics", "Teacher education", "General / multiple / not stated",
]

TOOL = {
    "name": "record_paper_analysis",
    "description": "Record a faithful, structured analysis of an education research paper, using only its abstract.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2 sentence summary of what the study does and finds."},
            "problem": {"type": "string", "description": "1-2 sentences: the educational problem or need the paper addresses."},
            "method": {
                "type": "string",
                "description": "1-2 sentences: how AI is used or studied (the tool, model or approach) and how the study is done.",
            },
            "eli5": {
                "type": "string",
                "description": "Plain language for a parent or teacher: 2-3 short sentences, no jargon.",
            },
            "key_finding": {
                "type": "string",
                "description": "The main result exactly as the abstract reports it, keeping any numbers. If the abstract "
                "reports no results, write 'The abstract reports no results.'",
            },
            "takeaway": {
                "type": "string",
                "description": "1 sentence: what an educator or school leader could take from this. Stay strictly within "
                "the findings and their stated context: no recommendation stronger, broader or more specific than the "
                "abstract itself makes, and no added detail. Prefer 'This study suggests…' over imperatives. If the "
                "abstract supports no practical takeaway, write 'No practical takeaway stated.'",
            },
            "study_type": {
                "type": "string", "enum": STUDY_TYPES,
                "description": "The study design the abstract describes. 'Randomized controlled trial': random assignment "
                "of people to conditions is stated or clearly implied (e.g. a randomized or between-subjects experiment). "
                "'Pre-post, single group': the same group is measured before AND after; a post-only survey is "
                "'Correlational / survey'. Quantitative analysis of logs, chats or usage without a comparison group is "
                "'Descriptive analysis of usage or interaction data'. A pilot or feasibility deployment of a new tool is "
                "'Design / product development'. Narrative or scoping reviews are 'Other review / conceptual / position'; "
                "only reviews with a stated systematic search are 'Systematic review / meta-analysis'. A comparison of AI "
                "outputs with human outputs without learner outcomes is a technical evaluation.",
            },
            "outcomes": {
                "type": "array", "items": {"type": "string", "enum": OUTCOMES},
                "description": "Every kind of outcome the study measures. 'Learning / achievement' only for measured "
                "learning (tests, grades, task performance), not self-reported learning, which is a perception.",
            },
            "direction": {
                "type": "string", "enum": DIRECTIONS,
                "description": "Direction of the main reported effect of the AI use on learners or educators. "
                "'Not applicable' for reviews, designs, technical evaluations or when no effect is reported.",
            },
            "sample_size": {
                "type": "integer",
                "description": "Number of HUMAN participants (learners, educators, parents) if the abstract states it, "
                "else 0. Count people, not messages, records, papers or institutions (those go in data_size). If the "
                "unit is pairs or groups, give the number of people only if the abstract states it, else 0. If several "
                "samples are reported, give the largest sample of people.",
            },
            "data_size": {
                "type": "string",
                "description": "Size of the non-participant data analysed, as stated (e.g. '12,650 chat messages', "
                "'65 papers', '30 universities'), or 'Not stated'.",
            },
            "participants": {
                "type": "string",
                "description": "Who took part, in a few words (e.g. '9th-grade students', 'pre-service teachers'), or 'Not stated'.",
            },
            "countries": {
                "type": "array", "items": {"type": "string"},
                "description": "Countries where the study took place, as stated in the abstract (e.g. 'United States'). "
                "Empty if not stated. Do not guess from author names.",
            },
            "subjects": {"type": "array", "items": {"type": "string", "enum": SUBJECTS}},
            "ai_tools": {
                "type": "array", "items": {"type": "string"},
                "description": "Named AI products or models used or studied (e.g. 'ChatGPT', 'GPT-4o', 'Khanmigo'). Not "
                "benchmarks, datasets or frameworks. Empty if none are named.",
            },
            "generative_ai": {"type": "boolean", "description": "Whether the AI studied is generative AI / an LLM."},
            "tags": {
                "type": "array", "items": {"type": "string"},
                "description": "3-5 short lowercase topic tags, e.g. 'intelligent tutoring', 'automated essay scoring'.",
            },
        },
        "required": [
            "summary", "problem", "method", "eli5", "key_finding", "takeaway", "study_type", "outcomes",
            "direction", "sample_size", "data_size", "participants", "countries", "subjects", "ai_tools", "generative_ai", "tags",
        ],
    },
}

PROMPT = """You are helping build an explorable map of research on AI in education for teachers, school leaders
and researchers. Read the paper below and record your analysis with the tool. Be concrete and faithful to the
abstract: do not invent results, numbers, countries or tools, and prefer "not stated" over a guess.

Title: {title}

Venue: {publication}

Abstract: {abstract}"""

# Bump when a field's prompt changes; --refresh-fields only redoes records below this version.
# v2 (after the first full audit): clearer study-type definitions + a usage-data category, people-only
# sample sizes with a separate data_size, and takeaways that may not go beyond the abstract.
PROMPT_VERSION = 2
TEXT_FIELDS = ["summary", "problem", "method", "eli5", "key_finding", "takeaway", "study_type", "direction"]


def subset_tool(fields):
    props = {f: TOOL["input_schema"]["properties"][f] for f in fields}
    return {**TOOL, "input_schema": {"type": "object", "properties": props, "required": list(fields)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--on-demand", action="store_true", help="skip batch (faster turnaround, ~2x cost)")
    ap.add_argument("--refresh-fields", default=None, help="comma-separated fields to regenerate for older records")
    args = ap.parse_args()

    df = load_papers()
    df = df[df["abstract"].str.len() > 100]
    done = load_enrichment()
    if args.refresh_fields:
        fields = args.refresh_fields.split(",")
        tool = subset_tool(fields)
        todo = df[df["pid"].map(lambda p: p in done and done[p].get("prompt_version", 1) < PROMPT_VERSION)]
        job = f"refresh-{len(fields)}f"
    else:
        tool, fields, job = TOOL, None, "enrich"
        # Incomplete records (a model occasionally returns a partial tool call) are redone too.
        todo = df[df["pid"].map(lambda p: p not in done or any(not done[p].get(f) for f in TEXT_FIELDS))]
    if args.limit:
        todo = todo.head(args.limit)
    print(f"{len(done)} enriched, {len(todo)} to process with {ENRICH_MODEL}" + (f" (refreshing {fields})" if fields else ""))
    if todo.empty:
        return

    requests = {
        row.pid: tool_request(PROMPT.format(title=row.title, publication=row.publication or "Not stated", abstract=row.abstract),
                              tool, max_tokens=1500, model=ENRICH_MODEL)
        for row in todo.itertuples()
    }
    if args.on_demand:
        from bedrock_batch import _on_demand

        results = _on_demand(ENRICH_MODEL, requests, workers=16)
    else:
        results = run_batch(f"{job}-n{len(requests)}", ENRICH_MODEL, requests)

    tokens_in = tokens_out = failed = 0
    with open(ENRICHMENT, "a") as f:
        for pid, resp in results.items():
            try:
                out = tool_result(resp["content"])
                if any(not out.get(k) for k in (fields or TEXT_FIELDS) if k in TEXT_FIELDS + ["data_size"]):
                    raise ValueError("missing fields")
            except Exception:
                failed += 1
                continue
            if fields:  # a refresh keeps the old record and overwrites only the refreshed fields
                rec = {**done[pid], **out, "prompt_version": PROMPT_VERSION, "refresh_model": ENRICH_MODEL}
            else:
                rec = {"pid": pid, **out, "prompt_version": PROMPT_VERSION, "model": ENRICH_MODEL}
            f.write(json.dumps(rec) + "\n")  # load_enrichment() keeps the last line per paper
            tokens_in += resp["usage"]["input_tokens"]
            tokens_out += resp["usage"]["output_tokens"]
    print(f"Saved {len(results) - failed} records. Tokens: {tokens_in:,} in / {tokens_out:,} out. Failed: {failed} (re-run to retry)")


if __name__ == "__main__":
    main()
