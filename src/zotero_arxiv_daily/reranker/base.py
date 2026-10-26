# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from abc import ABC, abstractmethod
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Type
class BaseReranker(ABC):
    def __init__(self, config:DictConfig):
        self.config = config

    def rerank(self, candidates:list[Paper], corpus:list[CorpusPaper]) -> list[Paper]:
        corpus = sorted(corpus,key=lambda x: x.added_date,reverse=True)
        time_decay_weight = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
        time_decay_weight: np.ndarray = time_decay_weight / time_decay_weight.sum()
        topics = [t.get('description') or t.get('name', '') for t in (self.config.get('topics', []) if self.config else [])]
        sim = self.get_similarity_score([c.abstract or c.title for c in candidates],
                                        [c.abstract or c.title for c in corpus] + topics)
        assert sim.shape == (len(candidates), len(corpus) + len(topics))
        library_sim = sim[:, :len(corpus)]
        scores = (library_sim * time_decay_weight).sum(axis=1)
        if topics:
            scores = 0.8 * sim[:, len(corpus):].max(axis=1) + 0.2 * scores
        scores *= 10
        for row, candidate in zip(library_sim, candidates):
            closest = corpus[int(np.argmax(row))]
            if not candidate.reason:
                candidate.reason = "相近收藏或主题：" + closest.title + "。"
        for s,c in zip(scores,candidates):
            c.score = s
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> Type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]
