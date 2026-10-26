# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from omegaconf import OmegaConf
from .protocol import Paper
from .state import State, ReadOnlyCache, cache_key
from .dedup import merge_papers
from .selection import select_papers, recent_papers, shortlist_papers
from .journal_metrics import enrich_metrics, eligible_by_journal, enrich_citations
from .screening import screen_papers
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        count = config.executor.max_paper_num
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("Set executor.max_paper_num to an integer from 1 to 100 before running")
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url,
                                    timeout=60.0, max_retries=1)
    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        self.library_ids = set()
        for c in corpus:
            data = c['data']
            pmid = data.get('PMID', '')
            if not pmid:
                match = re.search(r'^PMID:\s*(\d+)', data.get('extra', ''), re.M)
                pmid = match.group(1) if match else ''
            item = Paper(source='zotero', title=data.get('title', ''), authors=[], abstract='',
                         url=data.get('url', ''), doi=data.get('DOI', ''), pmid=pmid,
                         arxiv_id=data.get('archiveID', '') if data.get('repository', '').casefold() == 'arxiv' else '')
            self.library_ids.update(item.identifiers())
        corpus = [c for c in corpus if c['data'].get('abstractNote', '').strip()]
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info("Applying configured Zotero inclusion paths")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info("Applying configured Zotero exclusion paths")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            logger.info("Selected {} Zotero papers; titles and collection paths omitted", len(corpus))
        return corpus

    
    def run(self):
        dry_run = self.config.executor.get("dry_run", False) or bool(self.config.executor.debug)
        prepare_only = self.config.executor.get("prepare_only", False)
        if dry_run and prepare_only:
            raise ValueError("Choose either read-only preview or queue preparation")
        state = None if dry_run else State(self.config.feedback.base_url, self.config.feedback.api_token)
        preview_state = (State(self.config.feedback.base_url, self.config.feedback.api_token)
                         if dry_run and self.config.executor.get("preview_use_cache", False) else None)
        cache = state or (ReadOnlyCache(preview_state) if preview_state else None)
        self.reranker.state = cache
        end = datetime.now(timezone.utc)
        corpus = self.filter_corpus(self.fetch_zotero_corpus())
        if not corpus:
            corpus = [CorpusPaper(title=t.name, abstract=t.get("description") or t.name,
                                  added_date=end.replace(tzinfo=None), paths=[])
                      for t in self.config.get("topics", [])]
        if not corpus:
            raise ValueError("No Zotero abstracts or configured topic descriptions available")
        all_papers, failures = [], []
        for source, retriever in self.retrievers.items():
            queries = retriever.queries() if hasattr(retriever, "queries") else [(source, source)]
            if not queries:
                raise ValueError(f"Configure queries for {source} before running")
            for label, query in queries:
                key = "checkpoint:" + cache_key([source, label, query])
                previous = state.get(key) if state else None
                start = datetime.fromisoformat(previous) - timedelta(days=self.config.retrieval.overlap_days) if previous else end - timedelta(days=self.config.retrieval.initial_days)
                if previous and end.day <= 7:
                    start = min(start, end - timedelta(days=90))
                retriever.set_window(start, end)
                retriever.query_override = (label, query)
                try:
                    logger.info("Retrieving {}/{}", source, label)
                    papers = merge_papers(retriever.retrieve_papers())
                    if self.config.retrieval.get("max_age_days"):
                        papers = recent_papers(papers, end, int(self.config.retrieval.max_age_days))
                    logger.info("Retrieved {} papers for {}/{}", len(papers), source, label)
                    if state:
                        state.ingest(papers)
                        state.set(key, end.isoformat())
                    all_papers.extend(papers)
                except Exception as exc:
                    logger.warning("Retrieval incomplete for {}/{} ({}); checkpoint retained", source, label, type(exc).__name__)
                    failures.append(source + "/" + label)
        if state:
            all_papers = state.pending()
        else:
            all_papers = merge_papers(all_papers)
            if preview_state:
                all_papers = preview_state.unseen(all_papers)
        all_papers = [p for p in all_papers if not set(p.identifiers()) & self.library_ids]
        fresh_days = self.config.retrieval.get("max_age_days")
        if fresh_days:
            all_papers = recent_papers(all_papers, end, int(fresh_days))
        selected, ranked = [], []
        if all_papers:
            llm_config = OmegaConf.to_container(self.config.llm, resolve=True)
            screening = self.config.get("screening", {})
            if screening.get("enabled", False):
                missing_abstracts = sum(not p.abstract.strip() for p in all_papers)
                if missing_abstracts:
                    logger.info("{} records retained pending authoritative abstracts", missing_abstracts)
                all_papers = [p for p in all_papers if p.abstract.strip()]
                all_papers = screen_papers(all_papers, self.openai_client, llm_config, screening, cache)
            enrich_metrics(all_papers, self.config.journal_metrics, cache)
            all_papers = [p for p in all_papers if eligible_by_journal(p, self.config.journal_metrics)]
        if all_papers:
            if screening.get("enabled", False):
                all_papers = shortlist_papers(all_papers, int(self.config.executor.get("embedding_candidate_limit", 60)),
                                              self.config.executor.group_weights, end)
            logger.info("Ranking {} candidates against {} interest papers", len(all_papers), len(corpus))
            ranked = self.reranker.rerank(all_papers, corpus)
            selected = select_papers(ranked, self.config.executor.max_paper_num,
                                     self.config.executor.explore_ratio, self.config.executor.min_score,
                                     self.config.executor.get("group_weights"), end)
            if self.config.journal_metrics.get("fetch_citations", False):
                enrich_citations(selected, cache)
            logger.info("Generating summaries for {} selected papers", len(selected))
            for paper in selected:
                key = "cache:summary:" + cache_key([paper.title, paper.abstract, llm_config.get("language"),
                                                      llm_config.get("generation_kwargs"), paper.reason, "reading-card-v4-general"])
                summary = cache.get(key) if cache else None
                if summary:
                    paper.tldr = summary
                else:
                    paper.generate_tldr(self.openai_client, self.config.llm)
                    if cache and paper.tldr and paper.tldr != paper.abstract:
                        cache.set(key, paper.tldr)
                paper.reason += ("命中主题：" + "、".join(paper.topics) + "。" if paper.topics else "以确认的研究任务为主，相关收藏为辅。")
                if paper.journal_matches:
                    paper.reason += "来自关注期刊，已按研究相关性筛选。"
                if paper.exploratory:
                    paper.reason += "本篇为探索性推荐。"
            if screening.get("enabled", False):
                complete = [p for p in selected if p.tldr and p.tldr != p.abstract]
                if selected and not complete:
                    raise RuntimeError("Reading cards unavailable; candidates retained without sending")
                if len(complete) < len(selected):
                    logger.warning("{} papers deferred until a reading card is available", len(selected) - len(complete))
                selected = complete
        if failures and not all_papers:
            raise RuntimeError("Retrieval failed; no successful candidates available")
        if not selected and not self.config.executor.send_empty:
            return
        batch = None
        if prepare_only and selected:
            state.ingest(selected)
        if not dry_run and not prepare_only and selected:
            # Persist presentation fields before issuing any live links.
            state.ingest(selected)
            batch = state.call("batch", ids=[p.paper_id for p in selected])
            links = {p["paper_id"]: p for p in batch["links"]}
            for paper in selected:
                paper.save_url = links[paper.paper_id]["save"]
                paper.reject_url = links[paper.paper_id]["reject"]
        content = render_email(selected, failures=failures)
        if dry_run or prepare_only:
            preview = Path(self.config.executor.preview_path).resolve()
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_text(content, encoding="utf-8")
            if prepare_only:
                logger.info(f"Prepared queue and caches; preview saved to {preview}; no email, tokens or Zotero writes")
            else:
                logger.info(f"Preview saved to {preview}; no email or production state changed")
            import json
            from dataclasses import asdict
            preview.with_suffix(".json").write_text(json.dumps([asdict(p) for p in selected], ensure_ascii=False, indent=2), encoding="utf-8")
            preview.with_suffix(".candidates.json").write_text(json.dumps([asdict(p) for p in ranked], ensure_ascii=False, indent=2), encoding="utf-8")
            return
        if batch:
            state.call("delivery", id=batch["id"], status="sending")
        # A failure after this point is uncertain. Do not release/re-send automatically.
        send_email(self.config, content, batch_id=batch["id"] if batch else None)
        if batch:
            state.call("delivery", id=batch["id"], status="sent")
        logger.info("Literature email accepted by SMTP server")
