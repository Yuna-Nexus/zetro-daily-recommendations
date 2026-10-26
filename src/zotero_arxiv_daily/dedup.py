# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Exact identity only: similar titles are not proof of the same paper."""
import hashlib
import re
from urllib.parse import unquote


def normalize_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", unquote(value.strip()), flags=re.I).lower()


def identifiers(paper) -> list[str]:
    ids = []
    if paper.doi:
        ids.append("doi:" + normalize_doi(paper.doi))
    if paper.pmid:
        ids.append("pmid:" + str(paper.pmid).strip())
    arxiv_id = paper.arxiv_id
    if not arxiv_id and "arxiv.org/abs/" in paper.url:
        arxiv_id = paper.url.split("/abs/", 1)[1]
    if arxiv_id:
        ids.append("arxiv:" + re.sub(r"v\d+$", "", arxiv_id.strip(), flags=re.I))
    if not ids:
        ids.append("url:" + paper.url)
    return sorted(set(ids))


def paper_key(paper) -> str:
    return hashlib.sha256(paper.identifiers()[0].encode()).hexdigest()


def merge_papers(papers):
    merged, aliases = {}, {}
    for paper in papers:
        ids = paper.identifiers()
        roots = {aliases[i] for i in ids if i in aliases}
        key = sorted(roots)[0] if roots else paper_key(paper)
        if key not in merged:
            merged[key] = paper
        target = merged[key]
        priority = {"pubmed": 3, "journal": 2}
        if priority.get(paper.source, 1) > priority.get(target.source, 1):
            for name in ("source", "item_type", "title", "authors", "creators", "abstract", "url", "published", "journal", "issn", "volume", "issue", "pages"):
                if getattr(paper, name):
                    setattr(target, name, getattr(paper, name))
        for root in roots - {key}:
            other = merged.pop(root)
            target.topics = sorted(set(target.topics + other.topics))
            target.journal_matches = sorted(set(target.journal_matches + other.journal_matches))
            for name in ("doi", "pmid", "arxiv_id"):
                if not getattr(target, name):
                    setattr(target, name, getattr(other, name))
            aliases = {i: key if r == root else r for i, r in aliases.items()}
        target.topics = sorted(set(target.topics + paper.topics))
        target.journal_matches = sorted(set(target.journal_matches + paper.journal_matches))
        for name in ("doi", "pmid", "arxiv_id"):
            if not getattr(target, name):
                setattr(target, name, getattr(paper, name))
        for i in ids:
            aliases[i] = key
        target.paper_id = key
    return list(merged.values())
