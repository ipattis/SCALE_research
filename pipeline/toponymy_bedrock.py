"""Glue for using Toponymy with EVoC clusters and Claude on Bedrock."""
import asyncio
import hashlib

import evoc
import numpy as np
from sklearn.neighbors import NearestNeighbors
from toponymy.clustering import Clusterer, build_cluster_tree, centroids_from_labels
from toponymy.cluster_layer import ClusterLayerText
from toponymy.llm_wrappers import AsyncLLMWrapper

from common import llm_client


class AsyncBedrockNamer(AsyncLLMWrapper):
    """Toponymy LLM wrapper backed by the (async) Anthropic Bedrock client."""

    def __init__(self, model, max_concurrent_requests=12):
        self.model = model
        self.callback = None
        self.max_concurrent_requests = max_concurrent_requests
        self._loop = None

    def _bind_loop(self):
        # Toponymy runs each layer in a fresh event loop; asyncio primitives and the async HTTP
        # client must be created inside the loop that uses them.
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop = loop
            self.client = llm_client(async_=True)
            self.semaphore = asyncio.Semaphore(self.max_concurrent_requests)

    async def _create(self, messages, temperature, max_tokens, system=None):
        # temperature is ignored: current Claude models / SDK no longer take sampling params.
        kwargs = dict(model=self.model, max_tokens=max_tokens, messages=messages)
        if system:
            kwargs["system"] = system
        self._bind_loop()
        async with self.semaphore:
            resp = await self.client.messages.create(**kwargs)
        return "".join(b.text for b in resp.content if b.type == "text")

    async def _call_single_llm(self, prompt, temperature, max_tokens):
        return await self._create([{"role": "user", "content": prompt["combined"]}], temperature, max_tokens)

    async def _call_single_llm_with_system(self, prompt, temperature, max_tokens):
        return await self._create([{"role": "user", "content": prompt["user"]}], temperature, max_tokens, prompt["system"])


class EVoCClusterer(Clusterer):
    """Data-driven multi-resolution clusters from EVoC, run on the full embedding vectors.

    Keeps up to `max_layers` EVoC layers with between `min_clusters` and `max_clusters` clusters
    (finest first), so the number of topics at each zoom level comes from the data.
    """

    def __init__(self, min_clusters=6, max_clusters=400, max_layers=4, base_min_cluster_size=10, random_state=42,
                 assign_noise_k=15):
        self.assign_noise_k = assign_noise_k
        self.min_clusters = min_clusters
        self.max_clusters = max_clusters
        self.max_layers = max_layers
        self.base_min_cluster_size = base_min_cluster_size
        self.random_state = random_state

    def fit(self, clusterable_vectors, embedding_vectors, layer_class=ClusterLayerText, verbose=None, show_progress_bar=None, **layer_kwargs):
        model = evoc.EVoC(base_min_cluster_size=self.base_min_cluster_size, random_state=self.random_state)
        model.fit(embedding_vectors)
        n_of = lambda labels: len(set(labels.tolist()) - {-1})  # noqa: E731
        layers = [l for l in model.cluster_layers_ if self.min_clusters <= n_of(l) <= self.max_clusters]
        if len(layers) > self.max_layers:
            # keep the finest and coarsest, spread the rest evenly
            idx = np.unique(np.linspace(0, len(layers) - 1, self.max_layers).round().astype(int))
            layers = [layers[i] for i in idx]
        print("EVoC layers (clusters per layer, finest first):", [n_of(l) for l in layers])
        if self.assign_noise_k:
            layers = [self._assign_noise(l, embedding_vectors) for l in layers]
        self.cluster_tree_ = build_cluster_tree(layers)
        self.cluster_layers_ = [
            layer_class(labels, centroids_from_labels(labels, embedding_vectors), layer_id=i, **layer_kwargs)
            for i, labels in enumerate(layers)
        ]
        return self

    def _assign_noise(self, labels, vectors):
        """Give EVoC's noise points the majority label of their labelled nearest neighbours.

        EVoC leaves ~30% of papers as noise, which would leave a third of the map uncoloured and
        unsearchable by topic. Neighbours are searched among labelled points only, so every paper
        ends up in a cluster.
        """
        labels = labels.copy()
        noise = labels < 0
        if not noise.any():
            return labels
        nn = NearestNeighbors(n_neighbors=self.assign_noise_k, metric="cosine").fit(vectors[~noise])
        _, idx = nn.kneighbors(vectors[noise])
        neighbour_labels = labels[~noise][idx]
        labels[noise] = [np.bincount(row).argmax() for row in neighbour_labels]
        print(f"  assigned {noise.sum()} noise points to their neighbours' clusters")
        return labels

    def fit_predict(self, clusterable_vectors, embedding_vectors, layer_class=ClusterLayerText, verbose=None, show_progress_bar=None, **layer_kwargs):
        self.fit(clusterable_vectors, embedding_vectors, layer_class, verbose, show_progress_bar, **layer_kwargs)
        return self.cluster_layers_, self.cluster_tree_


class InstructedEmbedder:
    """Wraps a SentenceTransformer so keyphrases are embedded with the same Qwen3 instruction as papers.

    Embeddings are cached on disk (keyed by prompt + text), so re-running naming skips the
    ~50k keyphrase embeddings.
    """

    def __init__(self, model, prompt, cache_path=None):
        self.model = model
        self.prompt = prompt
        self.cache_path = cache_path
        self.cache = {}
        if cache_path and cache_path.exists():
            z = np.load(cache_path, allow_pickle=False)
            self.cache = dict(zip(z["keys"], z["vectors"]))

    def _key(self, text):
        return hashlib.sha1((self.prompt + "\x00" + text).encode()).hexdigest()

    def encode(self, texts, show_progress_bar=False, **kwargs):
        kwargs.pop("prompt", None)
        kwargs.setdefault("normalize_embeddings", True)
        keys = [self._key(t) for t in texts]
        todo = [i for i, k in enumerate(keys) if k not in self.cache]
        if todo:
            vecs = self.model.encode([texts[i] for i in todo], prompt=self.prompt, show_progress_bar=show_progress_bar,
                                     batch_size=32, **kwargs)
            for i, v in zip(todo, np.asarray(vecs, dtype=np.float32)):  # fp16 model output; numba needs float32
                self.cache[keys[i]] = v
            if self.cache_path and len(todo) > 100:
                np.savez(self.cache_path, keys=np.array(list(self.cache)), vectors=np.stack(list(self.cache.values())))
        return np.stack([self.cache[k] for k in keys]).astype(np.float32)
