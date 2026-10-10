"""Shared config, paths and LLM helpers for the SCALE AI-in-education research map pipeline."""
import json
import os
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = Path(os.environ.get("SCALE_SITE_DIR", ROOT / "site"))
COMBINED = Path(os.environ.get("SCALE_COMBINED_DIR", DATA / "combined"))
for d in (DATA, SITE, COMBINED):
    d.mkdir(exist_ok=True)

BASE_URL = "https://scale.stanford.edu"
LIST_URL = BASE_URL + "/ai/repository?page={page}"
PAPERS = DATA / "papers.parquet"
ENRICHMENT = DATA / "enrichment.jsonl"

# SCALE's own human-curated taxonomy (the five facets on every repository entry), in display order.
FACETS = {
    "application": ("What is the application?", [
        "Teaching – Instructional Materials", "Teaching – Assessment and Feedback", "Teaching – Professional Learning",
        "Learning – Student Support", "Communicating / Social Tools", "Organizing", "Analyzing",
    ]),
    "user": ("Who is the user?", ["Student", "Parent/Caregiver", "Educator", "School Leader", "Others"]),
    "age": ("Which age?", [
        "0-3 years", "Elementary (PK5)", "Middle School (6-8)", "High School (9-12)", "Post-Secondary", "Adult",
    ]),
    "why": ("Why use AI?", [
        "Efficiency", "Outcomes – Literacy", "Outcomes – Numeracy", "Outcomes – Other Academic",
        "Outcomes – Differentiation", "Outcomes – Social Emotional", "Outcomes – Durable Skills",
        "Reimagined Schooling", "Other",
    ]),
    "design": ("Study design", [
        "Descriptive – Implementation and Use", "Descriptive – Product Development",
        "Impact – Randomized Controlled Trial", "Impact – Quasi-experimental", "Systematic Review",
        "Technical – Computational", "Quantitative – Others",
    ]),
}
# Time periods for trends, by when a paper first appeared. Pre-2024 papers are few, so they're pooled.
PERIODS = ["≤2023", "2024", "2025", "2026"]
UNDATED = "Undated"


def period_of(year):
    if not year:
        return UNDATED
    return PERIODS[0] if int(year) <= 2023 else str(year)


K12_AGES = {"Elementary (PK5)", "Middle School (6-8)", "High School (9-12)"}
IMPACT_DESIGNS = {"Impact – Randomized Controlled Trial", "Impact – Quasi-experimental"}

# Three views of the same papers: what it's about, what education need it addresses, and how AI is used.
# Each is embedded with a Qwen3 task instruction so the space emphasises that aspect.
VIEWS = {
    "topic": "Identify the research topic of this paper on AI in education",
    "problem": "Identify the educational problem or need this paper on AI in education addresses",
    "method": "Identify how AI is used or studied in this education research paper",
}
EMBED_MODEL = os.environ.get("SCALE_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
# Small model for the in-browser "where does my paper fit?" index (runs via transformers.js).
SEARCH_MODEL = "Qwen/Qwen3-Embedding-0.6B"
SEARCH_MODEL_ONNX = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
SEARCH_DIM = 256  # Matryoshka truncation keeps the shipped index small

# Bedrock model IDs; override via env if your account uses different inference profiles.
# The corpus is small (~1.7k papers), so every LLM step runs on Sonnet and the audit covers every paper.
ENRICH_MODEL = os.environ.get("SCALE_ENRICH_MODEL", "us.anthropic.claude-sonnet-4-6")
NAMING_MODEL = os.environ.get("SCALE_NAMING_MODEL", "us.anthropic.claude-sonnet-5-5")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def use_os_certs():
    """Trust the OS certificate store (needed behind TLS-inspecting proxies like Cisco Umbrella).

    Only call this in scripts that use requests/HF downloads: truststore's global injection
    recurses inside botocore, so it must not be active in scripts that talk to AWS via boto3.
    """
    import truststore

    truststore.inject_into_ssl()


def load_papers():
    import pandas as pd

    if not PAPERS.exists():
        raise SystemExit("Run 01_fetch.py first")
    df = pd.read_parquet(PAPERS)
    df["period"] = df["year"].map(period_of)
    return df


def load_mapped():
    """(papers, enrichment) for the papers on the map: those with an abstract that have been enriched.

    Every step after 02 uses this, so all outputs share one paper order.
    """
    enrich = load_enrichment()
    df = load_papers()
    return df[df["pid"].isin(enrich)].reset_index(drop=True), enrich


LIST_FIELDS = ["outcomes", "countries", "subjects", "ai_tools", "tags"]
NOT_STATED = {"", "none", "not stated", "n/a", "[]"}


def _as_list(v):
    """The model occasionally returns a list field as a string ('United States', 'USA, Canada', '[]')."""
    if isinstance(v, list):
        return v
    if not isinstance(v, str) or v.strip().lower() in NOT_STATED:
        return []
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return parsed
    except ValueError:
        pass
    return [x.strip() for x in v.split(",") if x.strip()]


def load_enrichment():
    out = {}
    if ENRICHMENT.exists():
        with open(ENRICHMENT) as f:
            for line in f:
                rec = json.loads(line)
                for k in LIST_FIELDS:
                    if k in rec:
                        rec[k] = _as_list(rec[k])
                out[rec["pid"]] = rec  # later lines (refreshes) win
    return out


def llm_client(async_=False):
    """Anthropic client: direct API if ANTHROPIC_API_KEY is set (and USE_BEDROCK isn't), else Bedrock."""
    if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("USE_BEDROCK"):
        return (anthropic.AsyncAnthropic if async_ else anthropic.Anthropic)(max_retries=10)
    cls = anthropic.AsyncAnthropicBedrock if async_ else anthropic.AnthropicBedrock
    return cls(aws_region=AWS_REGION, max_retries=10)


def forced_tools_ok(model):
    """Whether `model` accepts tool_choice={"type": "tool"}. Probed once, cached in data/model_caps.json."""
    caps_path = DATA / "model_caps.json"
    caps = json.loads(caps_path.read_text()) if caps_path.exists() else {}
    if model not in caps:
        tool = {"name": "ping", "description": "ping", "input_schema": {"type": "object", "properties": {}}}
        try:
            llm_client().messages.create(
                model=model, max_tokens=16, tools=[tool], tool_choice={"type": "tool", "name": "ping"},
                messages=[{"role": "user", "content": "ping"}],
            )
            caps[model] = True
        except anthropic.BadRequestError as e:
            if "tool_choice" not in str(e):
                raise
            caps[model] = False
        caps_path.write_text(json.dumps(caps, indent=2))
    return caps[model]


def tool_request(prompt, tool, max_tokens=1024, system=None, model=None):
    """Messages API body asking for a single tool call (shared by on-demand and batch paths).

    Forces the tool when the model allows it; otherwise (e.g. Claude 5-family models on Bedrock)
    uses tool_choice=auto with an instruction to answer only via the tool.
    """
    body = {"max_tokens": max_tokens, "tools": [tool], "messages": [{"role": "user", "content": prompt}]}
    if model is None or forced_tools_ok(model):
        body["tool_choice"] = {"type": "tool", "name": tool["name"]}
    else:
        body["tool_choice"] = {"type": "auto"}
        system = (system + "\n\n" if system else "") + f"Respond only by calling the {tool['name']} tool."
    if system:
        body["system"] = system
    return body


def tool_result(content):
    """Extract the tool input dict from a list of response content blocks (SDK objects or dicts)."""
    for block in content:
        btype = block["type"] if isinstance(block, dict) else block.type
        if btype == "tool_use":
            return block["input"] if isinstance(block, dict) else block.input
    raise RuntimeError("No tool_use block in response")


# Evidence tiers: a fixed, published rule over the LLM-extracted study type and outcomes. It sorts papers
# by *what kind of evidence they report*; it is not a quality score (an RCT can still be weak).
TIERS = [
    "Causal evidence on learning",
    "Causal evidence, other outcomes",
    "Correlational / pre-post",
    "Qualitative / design",
    "Technical evaluation of AI",
    "Evidence synthesis",
    "Conceptual / commentary",
    "Unclear",
]
TIER_HELP = {
    TIERS[0]: "RCT or quasi-experiment that measures learning or achievement",
    TIERS[1]: "RCT or quasi-experiment measuring engagement, perceptions or teacher outcomes only",
    TIERS[2]: "Single-group pre-post, survey, correlational or usage-data study",
    TIERS[3]: "Qualitative, case study, or design / pilot / product-development work",
    TIERS[4]: "Evaluates AI outputs (accuracy, quality, benchmarks) without learner outcomes",
    TIERS[5]: "Systematic review or meta-analysis",
    TIERS[6]: "Other review, position or conceptual paper",
    TIERS[7]: "The abstract doesn't make the design clear",
}


def evidence_tier(rec):
    st, outcomes = rec.get("study_type", ""), set(rec.get("outcomes") or [])
    if st.startswith("Randomized") or st.startswith("Quasi"):
        return TIERS[0] if "Learning / achievement" in outcomes else TIERS[1]
    return {
        "Pre-post, single group": TIERS[2], "Correlational / survey": TIERS[2],
        "Descriptive analysis of usage or interaction data": TIERS[2],
        "Qualitative / case study": TIERS[3], "Design / product development": TIERS[3],
        "Technical evaluation of AI (no learner outcomes)": TIERS[4],
        "Systematic review / meta-analysis": TIERS[5], "Other review / conceptual / position": TIERS[6],
    }.get(st, TIERS[7])


def age_band(ages):
    """Collapse SCALE's age tags into one band for colouring the map."""
    ages = set(ages)
    k12, post = bool(ages & K12_AGES), bool(ages & {"Post-Secondary", "Adult"})
    if "0-3 years" in ages and not (k12 or post):
        return "Early childhood"
    if k12 and post:
        return "K-12 and post-secondary"
    if k12:
        return "K-12 only"
    if post:
        return "Post-secondary / adult only"
    return "Not tagged"
