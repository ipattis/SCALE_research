"""Embed papers with Qwen3-Embedding, locally.

- Map embeddings (Qwen3-Embedding-4B), one per view, each with a Qwen3 task instruction:
    topic   -> title + abstract
    problem -> LLM-extracted problem statement
    method  -> LLM-extracted method
- Search index (Qwen3-Embedding-0.6B, Matryoshka-truncated to SEARCH_DIM; quantised to int8 by 08) used in the
  browser by transformers.js for "where does my paper fit?".

Embeddings are cached per paper id, so re-runs only embed new papers.
"""
import argparse

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from common import COMBINED, EMBED_MODEL, SEARCH_DIM, SEARCH_MODEL, VIEWS, load_mapped, use_os_certs

DEVICE = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


def view_texts(df, enrich):
    e = lambda pid, f: enrich.get(pid, {}).get(f, "")  # noqa: E731
    return {
        "topic": (df["title"] + ". " + df["abstract"]).tolist(),
        "problem": [e(p, "problem") for p in df["pid"]],
        "method": [e(p, "method") for p in df["pid"]],
    }


def instruct(task):
    return f"Instruct: {task}\nQuery: "


def embed_cached(path, pids, texts, model, prompt, batch_size, dim=None):
    cache = {}
    if path.exists():
        z = np.load(path, allow_pickle=False)
        cache = dict(zip(z["pids"], z["vectors"]))
    todo = [i for i, p in enumerate(pids) if p not in cache]
    if todo:
        missing = [i for i in todo if not texts[i]]
        if missing:
            raise SystemExit(f"{len(missing)} papers have no text for {path.name}; run 02_enrich.py first")
        vecs = model.encode(
            [texts[i] for i in todo], prompt=prompt, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=True, truncate_dim=dim,
        )
        if dim:  # re-normalise after Matryoshka truncation
            vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        for i, v in zip(todo, vecs):
            cache[pids[i]] = v.astype(np.float32)
        np.savez(path, pids=np.array(list(cache)), vectors=np.stack(list(cache.values())))
    print(f"{path.name}: {len(todo)} new, {len(pids)} total")
    return np.stack([cache[p] for p in pids])


def load_model(name):
    model = SentenceTransformer(name, device=DEVICE, model_kwargs={"torch_dtype": torch.float16})
    model.max_seq_length = 512
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", default=",".join(VIEWS), help="comma-separated subset of views to embed")
    ap.add_argument("--skip-search", action="store_true")
    args = ap.parse_args()
    use_os_certs()
    df, enrich = load_mapped()
    pids = df["pid"].tolist()
    texts = view_texts(df, enrich)

    model = load_model(EMBED_MODEL)
    for view in args.views.split(","):
        task = VIEWS[view]
        embed_cached(COMBINED / f"emb_{view}.npz", pids, texts[view], model, instruct(task), batch_size=16)
    del model
    if args.skip_search:
        return

    small = load_model(SEARCH_MODEL)
    embed_cached(COMBINED / "emb_search.npz", pids, texts["topic"], small, instruct(VIEWS["topic"]), 32, SEARCH_DIM)


if __name__ == "__main__":
    main()
