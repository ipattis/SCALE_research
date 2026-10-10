# SCALE maps: what the AI-in-education evidence actually says

Interactive maps and an evidence read-out of the **Stanford SCALE AI Hub research repository**
([scale.stanford.edu/ai/repository](https://scale.stanford.edu/ai/repository)): 1,652 papers on AI in education
(fetched 2026-10-10). Open **`site/index.html`** for the maps or **`site/evidence.html`** for the evidence page.

> ### Why this exists
> SCALE's own announcement ([Chris Agnew, LinkedIn](https://www.linkedin.com/posts/chris-agnew-74bb8217_with-additions-this-month-were-now-approaching-share-7513351149807300608-UnUJ/))
> points out that the repository "does not bring a research quality or 'so what?' screen (critical thinking
> required!)". This project adds a transparent, audited first pass at that screen. It records **what kind of
> evidence** each paper reports, not how good the paper is. It is a rebuild of the sibling
> [`neurIPS`](../neurIPS) project, which itself builds on Jay Alammar's *Illustrated NeurIPS 2025* map.

## What's different from the NeurIPS version, and why

SCALE is about 12× smaller (1.7k vs 19.5k papers) and it already carries human-curated tags. So the effort goes
into **reading each paper more closely** rather than mapping more of them:

| | NeurIPS | SCALE |
|---|---|---|
| Per-paper LLM notes | summary, problem, method, ELI5, applications | the same, plus **key finding**, an **educator takeaway**, and an **evidence profile**: study type, outcomes measured, direction of the result, people sampled, country, subject, named AI tools |
| Audit | 600-paper sample | **every paper**, including the structured labels the charts count |
| Filters | year | period **plus SCALE's own five facets plus evidence tier / outcome / result direction**, all combinable |
| Extra page | – | **`evidence.html`**: gap map, evidence mix over time, what is measured, what causal studies find, sample sizes, topic growth, SCALE tags vs abstracts, and a searchable list of every causal study of learning. Everything re-renders for a scope (K-12 / post-secondary, generative AI, application) |
| Dates | conference year | **first posted** (from the arXiv id), because SCALE's date is often the latest arXiv *version* |
| Clusters | up to 4 levels | 3 levels with smaller minimum clusters (5–8 broad areas → ~20 topics → ~60 fine topics) |
| Agenda | conference schedule, .ics | **reading list** grouped by evidence tier, exported as .ris (Zotero / EndNote) or CSV |

## Headline findings (whole repository; see `data/insights.md`)

- **Fast growth.** 262 papers first appeared in 2024, 739 in 2025, and 515 in 2026 through August.
- **Little of it measures learning.** 18% of papers measure learning or achievement. More empirical studies
  measure perceptions (~73%) than learning (~32%).
- **Causal evidence on learning is a small slice.** 154 papers (9%) are RCTs or quasi-experiments that measure
  learning, and only **51 are in K-12**. The share has not risen with volume: 12% of 2024 papers, 7% of 2025, 10% of 2026.
- **The biggest category is technical evaluation of AI outputs** (30%): accuracy, grading agreement and benchmarks,
  with no learners involved.
- **SCALE's RCT tag is broader than the abstracts.** Of the 142 papers SCALE tags as RCTs, the abstract describes
  random assignment in 97 (68%), and 28 describe quasi-experiments. So filtering on the tag alone gives a
  different set of papers than "papers that report an RCT".
- **Results lean positive, as expected from abstracts.** In K-12 causal studies of learning, 35 of 51 report a
  positive result, 11 mixed, 5 no effect or negative. Abstracts and publication both favour positive findings.
- **Small samples are common.** The median causal study of learning that states a sample has 148 participants.

## Trust signals

`data/audit_report.md`: a second Sonnet pass checked **every** paper's notes and labels against its abstract. The
first audit showed takeaways overreaching (40% minor issues), sample sizes counting messages or records as
participants, and "pre-post" used for post-only surveys. Prompt v2 fixed these, and only the affected fields were
regenerated (`02_enrich.py --refresh-fields`). After the re-audit:

- **Overall:** 5.9% of papers have a questioned field (down from 9.7%). They show a ⚠ in the map and the evidence table.
- **Text fields:** 0.0–0.2% are unsupported.
- **Labels:** 4.1% of study types are wrong and 1.5% of sample sizes.
- **Caveat:** study type is "debatable" for 42% of papers. Designs are often under-described in abstracts, and the
  boundaries between categories are fuzzy, so treat tiers as a sorting aid, not a verdict. The judge is the same
  model family as the extractor.

## Pipeline (`pipeline/`)

```
01_fetch.py      scale.stanford.edu listing pages (facets) + detail pages (abstract, link) → data/papers.parquet
02_enrich.py     Claude via Bedrock batch: notes + evidence profile; --refresh-fields after an audit
03_embed.py      Qwen3-Embedding-4B, 3 task-instructed views (topic / need / AI use) + 0.6B browser search index
04_external.py   Semantic Scholar batch lookup by arXiv id / DOI, title fallback → citations, peer review
05_topics.py     shared UMAP + EVoC 3-level clusters + Toponymy topic names (Claude Sonnet 5.5)
06_trends.py     per-topic share change by period + "what's new" notes
07_audit.py      faithfulness audit of every paper (re-audits papers whose enrichment changed)
08_build_map.py  datamapplot HTML ×3 with toolbar, filters, trends, reading list, search (pipeline/web/)
09_insights.py   site/evidence.html (pipeline/web/evidence.html template) + data/insights.md
```

Run with `./run_all.sh` (or `./run_all.sh --hosted`). It reuses the NeurIPS project's venv (`~/.venvs/neurips`), the
Bedrock batch bucket and role, and the local MPS GPU. Every step caches, so next month's run only processes new papers.
Approximate LLM cost for the full build: ~$40 (enrichment ~$14, field refresh ~$5, two full audits ~$15, naming/trends a few dollars).

Notes:
- **Semantic Scholar:** the API key in `~/.config/semanticscholar.env` now returns 403. `04_external.py` falls back to
  anonymous requests, which was enough here (98% matched). Renew the key for more reliable runs.
- **Fetching:** `/ai/repository` is allowed by robots.txt; detail pages are fetched with 4 workers and cached.
- **Tests:** serve `site/` (`python -m http.server -d site 8000`) and run `python tests/ui_test.py http://localhost:8000`.
