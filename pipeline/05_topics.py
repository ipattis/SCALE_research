"""Shared 2D layout + data-driven topic hierarchy + LLM topic names, per view.

For each view (topic / problem / method):
  1. UMAP all years together into ONE 2D layout, so the same region means the same thing
     every year (the year filter in the map then shows how the field moved).
  2. EVoC clusters the full embeddings into several resolutions (the number of clusters is
     chosen by the data, not by us).
  3. Toponymy names every cluster with Claude, using exemplar papers, contrastive keyphrases and
     subtopic names, and disambiguates duplicate names across the hierarchy.

The corpus is ~10x smaller than NeurIPS, so clusters are allowed to be smaller (base size 5) and the
hierarchy is capped at three levels: a 1.7k-paper map with four zoom levels is mostly one-paper topics.

Output: data/combined/map_<view>.parquet with x, y and one name/id column pair per layer.
Usage: python 05_topics.py [--views topic,problem,method]
"""
import argparse

import numpy as np
import pandas as pd
import torch
import umap
from sentence_transformers import SentenceTransformer
from toponymy import Toponymy

from common import COMBINED, EMBED_MODEL, NAMING_MODEL, VIEWS, load_mapped
from toponymy_bedrock import AsyncBedrockNamer, EVoCClusterer, InstructedEmbedder

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

OBJECT_DESCRIPTIONS = {
    "topic": ("paper titles and abstracts", "research papers on AI in education"),
    "problem": ("educational problem statements", "educational problems addressed by research on AI in education"),
    "method": ("descriptions of how AI is used and studied", "uses of AI studied in education research papers"),
}


def load_view(view, pids):
    z = np.load(COMBINED / f"emb_{view}.npz", allow_pickle=False)
    lookup = dict(zip(z["pids"], range(len(z["pids"]))))
    missing = [p for p in pids if p not in lookup]
    if missing:
        raise SystemExit(f"{len(missing)} papers lack {view} embeddings; run 03_embed.py")
    return z["vectors"][[lookup[p] for p in pids]]


def objects_for(view, df, enrich):
    if view == "topic":
        return (df["title"] + ". " + df["abstract"]).tolist()
    field = {"problem": ["problem"], "method": ["method"]}[view]
    return [" ".join(enrich[p][f] for f in field) for p in df["pid"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", default=",".join(VIEWS))
    args = ap.parse_args()

    df, enrich = load_mapped()
    pids = df["pid"].tolist()
    st_model = SentenceTransformer(EMBED_MODEL, device=DEVICE, model_kwargs={"torch_dtype": torch.float16})

    for view in args.views.split(","):
        print(f"\n=== {view} view ===")
        emb = load_view(view, pids)
        coords = umap.UMAP(n_neighbors=15, min_dist=0.05, metric="cosine", random_state=42).fit_transform(emb)

        obj_desc, corpus_desc = OBJECT_DESCRIPTIONS[view]
        topic_model = Toponymy(
            llm_wrapper=AsyncBedrockNamer(NAMING_MODEL),
            text_embedding_model=InstructedEmbedder(
                st_model, f"Instruct: {VIEWS[view]}\nQuery: ", cache_path=COMBINED / f"keyphrase_cache_{view}.npz"
            ),
            clusterer=EVoCClusterer(min_clusters=5, max_clusters=120, max_layers=3, base_min_cluster_size=5),
            object_description=obj_desc,
            corpus_description=corpus_desc,
            # Name length per layer: finest -> "clear and concise (3 to 6 word)", coarsest -> "1 to 4 word".
            # Toponymy's default starts at 8-15 words, which is too long for map labels.
            lowest_detail_level=0.5,
            highest_detail_level=0.8,
            verbose=True,
        )
        topic_model.fit(objects_for(view, df, enrich), embedding_vectors=emb, clusterable_vectors=coords)

        out = pd.DataFrame({"pid": pids, "x": coords[:, 0], "y": coords[:, 1]})
        for i, layer in enumerate(topic_model.cluster_layers_):
            labels = np.asarray(layer.cluster_labels)
            names = np.array(layer.topic_names + ["Unlabelled"], dtype=object)
            out[f"L{i}_id"] = labels
            out[f"L{i}_name"] = names[np.where(labels >= 0, labels, len(layer.topic_names))]
            print(f"  layer {i}: {labels.max() + 1} topics, e.g. {layer.topic_names[:4]}")
        out.to_parquet(COMBINED / f"map_{view}.parquet")
        print(f"Saved map_{view}.parquet")


if __name__ == "__main__":
    main()
