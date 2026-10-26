# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from .biorxiv_retriever import BiorxivRetriever
from .base import register_retriever

@register_retriever("medrxiv")
class MedrxivRetriever(BiorxivRetriever):
    server = "medrxiv"