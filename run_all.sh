#!/usr/bin/env bash
# Rebuild the SCALE maps + evidence page end to end. Every step caches its outputs in data/, so re-running
# (e.g. next month, when the repository grows) only processes new papers.
#   ./run_all.sh            # self-contained HTML in site/
#   ./run_all.sh --hosted   # site/ with split data files, ready for GitHub Pages
set -euo pipefail
cd "$(dirname "$0")/pipeline"
PY="${PYTHON:-$HOME/.venvs/neurips/bin/python}"   # shares the NeurIPS project's venv
export HF_HUB_DISABLE_XET=1   # the Xet CDN is blocked on some corporate networks

"$PY" 01_fetch.py           # scale.stanford.edu/ai/repository -> data/papers.parquet
"$PY" 02_enrich.py          # Bedrock batch: plain-language notes + evidence profile per paper
"$PY" 03_embed.py           # Qwen3-Embedding-4B (3 views) + 0.6B search index, local GPU
"$PY" 04_external.py        # Semantic Scholar citations + peer-review status
"$PY" 05_topics.py          # shared UMAP + EVoC clusters + Toponymy names (Claude Sonnet)
"$PY" 06_trends.py          # topic growth by period + "what's new" notes
"$PY" 07_audit.py           # LLM-as-judge audit of EVERY paper's extraction
"$PY" 08_build_map.py "$@"  # site/index.html, problem.html, method.html
"$PY" 09_insights.py        # site/evidence.html + data/insights.md
echo "Done: open site/index.html"
