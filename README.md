# SCALE maps: what the AI-in-education evidence says

Interactive maps and an evidence read-out of the **Stanford SCALE AI Hub research repository**
([scale.stanford.edu/ai/repository](https://scale.stanford.edu/ai/repository)), covering 1,652 papers on AI in
education (snapshot of 10 October 2026).

**Live site:** published from `site/` by GitHub Pages: [map](site/index.html) · [evidence page](site/evidence.html)
(locally, open `site/index.html` in a browser).

> SCALE's own announcement ([Chris Agnew on LinkedIn](https://www.linkedin.com/posts/chris-agnew-74bb8217_with-additions-this-month-were-now-approaching-share-7513351149807300608-UnUJ/))
> notes that the repository "does not bring a research quality or 'so what?' screen (critical thinking required!)".
> This project is a transparent, audited first pass at that screen. For each paper it records **what kind of
> evidence** the abstract reports. It does **not** judge how good the paper is.

## What's on the site

| Page | What it shows |
|---|---|
| **By topic** (`index.html`) | Every paper on a zoomable map, placed by what it's about, with topics named at three zoom levels |
| **By need** (`problem.html`) | The same papers placed by the educational problem they tackle |
| **By AI use** (`method.html`) | The same papers placed by how AI is used or studied |
| **Evidence** (`evidence.html`) | Charts: growth over time, the evidence mix, an evidence gap map, what studies measure, what causal studies find, sample sizes, fastest-growing topics, SCALE's tags vs the abstracts, and a searchable list of every causal study of learning. A scope filter (K-12 / post-secondary, generative AI, application) re-renders the whole page |

On the maps you can:
- **Filter** by period, SCALE's five tag groups (application, user, age, purpose, study design), evidence tier,
  outcome measured and direction of result. Filters combine.
- **Read** each paper's plain-language summary, key finding, educator takeaway and evidence profile, alongside
  SCALE's tags and links to the paper and its SCALE entry.
- **See trends**: which topics are gaining or losing share of the repository, with a note on what's new in 2026.
- **Find where your work fits**: paste an abstract and it is embedded in your browser (nothing is uploaded), then
  placed next to its nearest papers.
- **Keep a reading list**, grouped by evidence tier and exported as `.ris` (Zotero, EndNote, Mendeley) or CSV.

## Headline findings

Repository-wide figures; full numbers are in [`data/insights.md`](data/insights.md).

- **Fast growth.** 262 papers first appeared in 2024, 739 in 2025 and 515 in 2026 through August.
- **Little of it measures learning.** 18% of papers measure learning or achievement. Among empirical studies, more
  measure perceptions (73%) than learning (32%).
- **Causal evidence on learning is a small slice.** 154 papers (9%) are randomized or quasi-experimental studies
  that measure learning, and 51 of them are in K-12. That share hasn't risen with volume: 12% of 2024 papers,
  7% of 2025 and 10% of 2026.
- **The largest category is technical evaluation of AI** (30%): accuracy, grading agreement and benchmarks, with no
  learners involved.
- **SCALE's RCT tag is broader than what the abstracts describe.** Of 142 papers tagged as RCTs, 97 describe random
  assignment and 28 describe quasi-experiments.
- **Reported results lean positive**, as expected from abstracts. Of the 51 K-12 causal studies of learning,
  35 report a positive result, 11 mixed and 5 no effect or negative.
- **Samples are modest.** The median causal study of learning that states a sample has 148 participants.

## How it's made

1. **Fetch** every repository entry: SCALE's tags from the listing pages, abstracts and links from each paper's page.
2. **Read each abstract** with Claude and record:
   - a summary, a plain-language version and the problem addressed
   - how AI is used, the key finding and an educator takeaway
   - an **evidence profile**: study type, outcomes measured, direction of the main result, number of
     participants, country, subject and named AI tools

   The model is told to answer "not stated" rather than guess.
3. **Assign an evidence tier** with a fixed rule, not a judgement. For example, an RCT or quasi-experiment that
   measures learning is "Causal evidence on learning". The tier describes *what kind* of evidence a paper reports.
4. **Audit every paper.** A second LLM pass compares each paper's notes and labels with its abstract (report:
   [`data/audit_report.md`](data/audit_report.md)). The first audit led to a tighter prompt and regenerated fields.
   - 5.9% of papers now have a questioned field; they are marked ⚠ on the site.
   - Unsupported text is 0–0.2% per field, and 4.1% of study types are wrong.
   - Study type is "debatable" for 42% of papers, because abstracts often under-describe designs. Treat tiers as a
     sorting aid.
   - The judge is the same model family as the extractor, so this is a consistency check, not independent verification.
5. **Map**: embed each paper three ways (topic, need, AI use) with Qwen3-Embedding, then lay them out with UMAP.
   Clusters come from EVōC, are named with [Toponymy](https://github.com/TutteInstitute/toponymy) and Claude, and are
   rendered with [datamapplot](https://github.com/TutteInstitute/datamapplot).
6. **Add context**: citation counts and peer-review status from [Semantic Scholar](https://www.semanticscholar.org/).

**Dates.** "First posted" uses the arXiv identifier where there is one, because SCALE's date is often that of the
latest arXiv version. The 2026 period is a partial year, so trends compare each topic's *share* of papers, not raw counts.

### Rebuilding

```
pipeline/01_fetch.py … 09_insights.py      # run in order, or ./run_all.sh
```

The pipeline runs locally. It needs AWS Bedrock access for Claude and a GPU (or Apple silicon) for the embeddings.
Every step caches its outputs, so a monthly re-run only processes papers added since the last run. The published
site is the prebuilt `site/` folder; `.github/workflows/static.yml` deploys it to GitHub Pages on every push to `main`.

| Step | Does |
|---|---|
| `01_fetch.py` | SCALE listing + paper pages → `data/papers.parquet` |
| `02_enrich.py` | per-paper notes and evidence profile (Claude, Bedrock batch); `--refresh-fields` after an audit |
| `03_embed.py` | Qwen3-Embedding-4B, three views, plus the small in-browser search index |
| `04_external.py` | Semantic Scholar citations and peer-review status |
| `05_topics.py` | shared UMAP layout, EVōC clusters, Toponymy topic names |
| `06_trends.py` | topic share change by period and "what's new" notes |
| `07_audit.py` | faithfulness audit of every paper |
| `08_build_map.py` | the three map pages (`pipeline/web/app.*`) |
| `09_insights.py` | the evidence page (`pipeline/web/evidence.html`) and `data/insights.md` |

UI smoke test: `python -m http.server -d site 8000`, then `python tests/ui_test.py http://localhost:8000`.

## Credits and caveats

- **Papers, curation and tags:** the [Stanford SCALE Initiative](https://scale.stanford.edu/ai) AI Hub for Education.
  Titles and abstracts belong to their authors. This is an independent project, not affiliated with or endorsed by
  SCALE or Stanford. To correct or add a paper, use the repository's own feedback form.
- **Design:** a rebuild of the [NeurIPS maps](https://github.com/ipattis/neurIPS) project, which builds on Jay Alammar's
  [*The Illustrated NeurIPS 2025*](https://newsletter.languagemodels.co/p/the-illustrated-neurips-2025-a-visual).
- **AI-written content:** summaries, takeaways, evidence profiles, topic names and trend notes are written by an LLM
  from abstracts only. They can be wrong. Read the paper before citing or acting on it.
