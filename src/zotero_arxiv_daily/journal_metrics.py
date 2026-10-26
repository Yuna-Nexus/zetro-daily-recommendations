# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Provider-labelled JCR/IF with explicit provenance and conservative missing-data rules."""
from datetime import datetime, timezone
from time import sleep
import re
from urllib.parse import quote

import requests
from loguru import logger


def journal_key(name):
    return re.sub(r'[^a-z0-9]', '', re.split(r'[:=]', name, maxsplit=1)[0].casefold().replace('&', 'and'))


def enrich_metrics(papers, config, state=None):
    key = config.get("api_key")
    if not key:
        return
    cached = {}
    known_names = list(config.get("focus_journals", [])) + [
        "Journal of the American Medical Informatics Association", "Clinical Pharmacology & Therapeutics",
        "JAMIA Open", "Journal of Biomedical Informatics", "Cell Reports Medicine",
        "Farmacia Hospitalaria", "Biomedicine & Pharmacotherapy"]
    canonical = {journal_key(name): name for name in known_names}
    for paper in papers:
        if not paper.journal or paper.item_type == "preprint":
            continue
        name = canonical.get(journal_key(paper.journal), paper.journal.strip())
        if name not in cached:
            cache_id = "cache:journal:v2:" + name.casefold()
            old = state.get(cache_id) if state else None
            if old and (datetime.now(timezone.utc) - datetime.fromisoformat(old["fetched_at"])).days < (30 if old.get("jcr") else 1):
                cached[name] = old
            else:
                try:
                    short_name = re.split(r'[:=]', name, maxsplit=1)[0].strip()
                    for lookup_name in dict.fromkeys([name, short_name]):
                        sleep(0.6)
                        response = requests.get("https://www.easyscholar.cc/open/getPublicationRank",
                                                params={"secretKey": key, "publicationName": lookup_name}, timeout=30)
                        response.raise_for_status()
                        data = response.json()
                        if data.get("code") != 200:
                            raise ValueError("EasyScholar rejected request")
                        rank = (((data.get("data") or {}).get("officialRank") or {}).get("all") or {})
                        if not isinstance(rank, dict):
                            raise ValueError("Invalid journal ranking response")
                        if rank:
                            break
                    quartiles = re.findall(r'Q[1-4]', str(rank.get("sci") or rank.get("ssci") or ""), re.I)
                    cached[name] = {"jcr": min((q.upper() for q in quartiles), default=None), "if": rank.get("sciif"),
                                    "cas": rank.get("sciUp"), "year": None, "verified": False,
                                    "esci": rank.get("esci"), "raw_jcr": rank.get("sci") or rank.get("ssci"),
                                    "source": "EasyScholar", "fetched_at": datetime.now(timezone.utc).isoformat()}
                    if state:
                        state.set(cache_id, cached[name])
                except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
                    logger.warning("Journal metrics unavailable; retaining unverified/stale data")
                    cached[name] = {**old, "stale": True} if old else {}
        paper.metrics = cached[name]


def eligible_by_journal(paper, config):
    if not config.get("filter_by_jcr", False) or not paper.journal or paper.item_type == "preprint":
        return True
    quartile = (paper.metrics or {}).get("jcr")
    if quartile in ("Q1", "Q2"):
        return True
    if quartile == "Q4":
        return False
    directly_relevant = paper.recommendation_group == "core" and paper.relevance_level >= 3
    if quartile == "Q3":
        return directly_relevant
    focus = {journal_key(j) for j in config.get("focus_journals", [])}
    return directly_relevant or journal_key(paper.journal) in focus


def enrich_citations(papers, state=None):
    for paper in papers:
        if not paper.doi:
            continue
        key = "cache:citations:" + paper.doi.lower()
        old = state.get(key) if state else None
        if old and (datetime.now(timezone.utc) - datetime.fromisoformat(old["checked"])).days < 7:
            result = old
        else:
            try:
                response = requests.get("https://api.crossref.org/works/" + quote(paper.doi, safe=""),
                                        headers={"User-Agent": "zotero-arxiv-daily/1.0"}, timeout=20)
                response.raise_for_status()
                count = response.json()["message"].get("is-referenced-by-count")
                if type(count) is not int or count < 0:
                    continue
                result = {"count": count, "checked": datetime.now(timezone.utc).isoformat()}
                if state:
                    state.set(key, result)
            except (requests.RequestException, ValueError, KeyError):
                continue
        paper.citation_count = result["count"]
        paper.citation_source = "Crossref"
        paper.citation_checked = result["checked"][:10]
