# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from .base import BaseReranker, register_reranker
from openai import OpenAI
import numpy as np
from ..state import cache_key
@register_reranker("api")
class ApiReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)
        batch_size = self.config.reranker.api.get("batch_size") or 64
        all_texts = s1 + s2
        state = getattr(self, "state", None)
        keys = ["cache:embedding:" + cache_key([self.config.reranker.api.base_url, self.config.reranker.api.model, t]) for t in all_texts]
        values = {k: state.get(k) if state else None for k in set(keys)}
        missing = list(dict.fromkeys(k for k in keys if values[k] is None))
        texts = dict(zip(keys, all_texts))
        for i in range(0, len(missing), batch_size):
            batch_keys = missing[i:i + batch_size]
            batch = [texts[k] for k in batch_keys]
            response = client.embeddings.create(
                input=batch,
                model=self.config.reranker.api.model
            )
            embeddings = sorted(response.data, key=lambda r: getattr(r, 'index', 0))
            if len(embeddings) != len(batch_keys):
                raise ValueError("Incomplete embedding response")
            for key, result in zip(batch_keys, embeddings):
                values[key] = result.embedding
                if state:
                    state.set(key, result.embedding)
        all_embeddings = [values[k] for k in keys]
        s1_embeddings = np.array(all_embeddings[:len(s1)])           # [n_s1, d]
        s2_embeddings = np.array(all_embeddings[len(s1):])           # [n_s2, d]
        s1_embeddings_normalized = s1_embeddings / np.maximum(np.linalg.norm(s1_embeddings, axis=1, keepdims=True), 1e-12)
        s2_embeddings_normalized = s2_embeddings / np.maximum(np.linalg.norm(s2_embeddings, axis=1, keepdims=True), 1e-12)
        sim = np.dot(s1_embeddings_normalized, s2_embeddings_normalized.T) # [n_s1, n_s2]
        return sim
