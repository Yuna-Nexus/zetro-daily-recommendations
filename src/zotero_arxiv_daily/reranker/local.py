# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from .base import BaseReranker, register_reranker
import logging
import warnings
import numpy as np
from loguru import logger
from omegaconf import OmegaConf
from ..state import cache_key
@register_reranker("local")
class LocalReranker(BaseReranker):
    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        from sentence_transformers import SentenceTransformer
        if not self.config.executor.debug:
            from transformers.utils import logging as transformers_logging
            from huggingface_hub.utils import logging as hf_logging
    
            transformers_logging.set_verbosity_error()
            hf_logging.set_verbosity_error()
            logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
            logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
            logging.getLogger("transformers").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
            warnings.filterwarnings("ignore", category=FutureWarning)

        if not hasattr(self, "encoder"):
            self.encoder = SentenceTransformer(self.config.reranker.local.model, trust_remote_code=True)
        encoder = self.encoder
        max_length = int(self.config.reranker.local.get("max_seq_length", 1024))
        encoder.max_seq_length = max_length
        if self.config.reranker.local.encode_kwargs:
            encode_kwargs = self.config.reranker.local.encode_kwargs
        else:
            encode_kwargs = {}
        state = getattr(self, "state", None)
        texts = s1 + s2
        params = OmegaConf.to_container(encode_kwargs, resolve=True) if OmegaConf.is_config(encode_kwargs) else encode_kwargs
        keys = ["cache:embedding:" + cache_key([self.config.reranker.local.model, max_length, params, t]) for t in texts]
        values = {k: state.get(k) if state else None for k in set(keys)}
        missing = list(dict.fromkeys(k for k in keys if values[k] is None))
        mapping = dict(zip(keys, texts))
        if missing:
            logger.info("Encoding {} texts, max {} tokens, batch size {}", len(missing), max_length, params.get("batch_size", 32))
            encoded = encoder.encode([mapping[k] for k in missing], **encode_kwargs, show_progress_bar=True)
            for key, value in zip(missing, encoded):
                values[key] = value.tolist()
                if state:
                    state.set(key, values[key])
        s1_feature = np.array([values[k] for k in keys[:len(s1)]])
        s2_feature = np.array([values[k] for k in keys[len(s1):]])
        sim = encoder.similarity(s1_feature, s2_feature)
        return sim.numpy()
