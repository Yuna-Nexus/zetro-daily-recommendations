# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timedelta, timezone
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import numpy as np
import pytest
from omegaconf import OmegaConf, open_dict

from tests.canned_responses import make_sample_paper
from tests.state_stub import MemoryState
from zotero_arxiv_daily.construct_email import render_email
from zotero_arxiv_daily.journal_metrics import eligible_by_journal, enrich_metrics
from zotero_arxiv_daily.protocol import CorpusPaper
from zotero_arxiv_daily.reranker.base import BaseReranker
from zotero_arxiv_daily.retriever.journal_retriever import JournalRetriever
from zotero_arxiv_daily.screening import screen_papers
from zotero_arxiv_daily.selection import recent_papers, select_papers, shortlist_papers
from zotero_arxiv_daily.state import ReadOnlyCache, State
from zotero_arxiv_daily.executor import Executor


NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)


def paper(**values):
    return make_sample_paper(published='2026-10-24', score=6, **values)


def test_online_publication_age_not_ingestion_date():
    fresh = paper(title='fresh')
    old = make_sample_paper(published='2026-08-01')
    future = make_sample_paper(published='2026-11-01')
    unknown = make_sample_paper(published='')
    assert recent_papers([fresh, old, future, unknown], NOW) == [fresh]


def test_pubmed_first_online_date_precedes_new_print_issue(config):
    from tests.test_literature_mvp import XML
    from zotero_arxiv_daily.retriever.pubmed_retriever import PubmedRetriever
    xml = XML.replace('</Article>', '<ArticleDate DateType="Electronic"><Year>2026</Year><Month>Sep</Month><Day>10</Day></ArticleDate></Article>')
    p = PubmedRetriever(config).convert_to_paper((ET.fromstring(xml)[0], ['topic']))
    assert p.published == '2026-09-10' and not recent_papers([p], NOW)


def test_pubmed_books_do_not_discard_matching_journal_papers(config, monkeypatch):
    from tests.test_literature_mvp import XML, pubmed
    retriever = pubmed(config)
    xml = XML.replace('</PubmedArticleSet>', '<PubmedBookArticle><BookDocument><PMID>456</PMID></BookDocument></PubmedBookArticle></PubmedArticleSet>')
    monkeypatch.setattr(retriever, 'search_ids', lambda *a: ['123', '456'])
    monkeypatch.setattr(retriever, 'request', lambda *a: SimpleNamespace(content=xml.encode()))
    records = retriever._retrieve_raw_papers()
    assert len(records) == 1
    assert retriever.convert_to_paper(records[0]).pmid == '123'


def test_group_targets_and_no_quality_padding():
    pool = [paper(title=f'{g}-{i}', recommendation_group=g, relevance_level=3)
            for g in ['core', 'cross', 'explore'] for i in range(8)]
    selected = select_papers(pool, 10, .2, 0, {'core': 5, 'cross': 3, 'explore': 2}, NOW)
    assert [sum(p.recommendation_group == g for p in selected) for g in ['core', 'cross', 'explore']] == [5, 3, 2]
    only_core = [p for p in pool if p.recommendation_group == 'core']
    assert len(select_papers(only_core, 10, .2, 0, {'core': 5, 'cross': 3, 'explore': 2}, NOW)) == 8
    assert len({id(p) for p in selected}) == 10
    large_pool = [paper(title=f'{g}-{i}', recommendation_group=g, relevance_level=3)
                  for g in ['core', 'cross', 'explore'] for i in range(100)]
    shortlist = shortlist_papers(large_pool, 60, {'core': 5, 'cross': 3, 'explore': 2}, NOW)
    assert [sum(p.recommendation_group == g for p in shortlist) for g in ['core', 'cross', 'explore']] == [30, 18, 12]
    assert len(large_pool) == 300


def test_profile_relevance_precedes_newer_supporting_paper():
    agent = make_sample_paper(title='LLM medication decision agent', published='2026-10-10',
                             abstract='A large language model for clinical medication decisions.',
                             score=5, relevance_level=3, recommendation_group='core')
    association = paper(title='Population pharmacogenomics cohort', abstract='Genotype associations with plasma concentration.',
                        relevance_level=2, recommendation_group='core')
    selected = select_papers([association, agent], 1, .2, 0, {'core': 5, 'cross': 3, 'explore': 2}, NOW)
    assert selected == [agent]


@pytest.mark.parametrize('quartile,group,level,expected', [
    ('Q1', 'cross', 2, True), ('Q2', 'explore', 1, True), ('Q3', 'core', 3, True),
    ('Q3', 'cross', 3, False), ('Q3', 'core', 2, False), ('Q4', 'core', 3, False),
    (None, 'core', 3, True), (None, 'cross', 2, False),
])
def test_jcr_policy_preserves_strong_core_exception(quartile, group, level, expected):
    p = paper(journal='Specialist Journal', item_type='journalArticle', metrics={'jcr': quartile},
              recommendation_group=group, relevance_level=level)
    assert eligible_by_journal(p, {'filter_by_jcr': True}) is expected
    p.item_type = 'preprint'
    assert eligible_by_journal(p, {'filter_by_jcr': True})


def test_missing_jcr_focus_journal_is_kept():
    p = paper(journal='NEJM AI', item_type='journalArticle', recommendation_group='cross', relevance_level=2)
    assert eligible_by_journal(p, {'filter_by_jcr': True, 'focus_journals': ['NEJM AI']})
    p.journal = 'The Lancet. Digital health'
    assert eligible_by_journal(p, {'filter_by_jcr': True, 'focus_journals': ['The Lancet Digital Health']})


def test_metrics_cache_preserves_if_and_does_not_invent_year_or_esci_semantics(monkeypatch):
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {'code': 200, 'data': {'officialRank': {'all': {'sciif': '4.7', 'esci': 'Q1'}}}})
    monkeypatch.setattr('zotero_arxiv_daily.journal_metrics.requests.get', get)
    monkeypatch.setattr('zotero_arxiv_daily.journal_metrics.sleep', lambda _: None)
    p = paper(journal='JAMIA Open', item_type='journalArticle')
    state = MemoryState()
    enrich_metrics([p], {'api_key': 'fake'}, state)
    enrich_metrics([p], {'api_key': 'fake'}, state)
    assert len(calls) == 1
    assert p.metrics['if'] == '4.7' and p.metrics['year'] is None
    assert p.metrics['jcr'] is None and p.metrics['esci'] == 'Q1'
    html = render_email([p])
    assert 'IF 4.7' in html and 'JCR 未知' in html and 'ESCI 返回 Q1' in html


@pytest.mark.parametrize('data', [None, {'officialRank': None}, {'officialRank': {'all': None}}])
def test_success_response_with_no_journal_match_does_not_abort_recommendations(data, monkeypatch):
    monkeypatch.setattr('zotero_arxiv_daily.journal_metrics.requests.get', lambda *a, **k:
                        SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'code': 200, 'data': data}))
    monkeypatch.setattr('zotero_arxiv_daily.journal_metrics.sleep', lambda _: None)
    p = paper(journal='Not indexed', item_type='journalArticle')
    enrich_metrics([p], {'api_key': 'fake'})
    assert p.metrics['jcr'] is None and p.metrics['if'] is None


def test_screening_caches_acceptance_and_exclusion_and_never_releases_unparsed_output(monkeypatch):
    calls = []
    def request(*args):
        calls.append(1)
        return json.dumps({'papers': [
            {'id': 0, 'group': 'core', 'level': 3, 'reason': '直接对标PGx证据链'},
            {'id': 1, 'group': 'exclude', 'level': 0, 'reason': '只有分子生成任务'},
        ]})
    monkeypatch.setattr('zotero_arxiv_daily.screening._request_llm', request)
    papers = [paper(title='PGx agent'), paper(title='Molecule generation')]
    state = MemoryState()
    for _ in range(2):
        result = screen_papers(papers, None, {'generation_kwargs': {}}, {'profile': 'PGx'}, state)
        assert result == [papers[0]] and result[0].relevance_level == 3
        assert result[0].topics == ['核心课题']
    assert len(calls) == 1
    monkeypatch.setattr('zotero_arxiv_daily.screening._request_llm', lambda *a: '{"papers": []}')
    with pytest.raises(RuntimeError, match='retained'):
        screen_papers(papers, None, {}, {'profile': 'changed'}, state)


def test_research_profile_outweighs_old_library_interest(config):
    class Reranker(BaseReranker):
        def get_similarity_score(self, *args):
            return np.array([[.1, .9], [.9, .1]])
    config.topics = [{'name': 'PGx', 'description': 'patient-specific PGx decision'}]
    corpus = [CorpusPaper('Old antibiotic study', 'antibiotic', NOW.replace(tzinfo=None), [])]
    papers = [paper(title='PGx'), paper(title='Old interest')]
    assert Reranker(config).rerank(papers, corpus)[0].title == 'PGx'


def test_crossref_online_date_abstract_authors_and_missing_pagination(config, monkeypatch):
    retriever = JournalRetriever(config)
    item = {'title': ['Clinical <i>agent</i>'], 'DOI': '10.1/test', 'abstract': '<jats:p>Evidence &amp; safety.</jats:p>',
            'container-title': ['NEJM AI'], 'published-online': {'date-parts': [[2026, 9, 10]]},
            'published-print': {'date-parts': [[2026, 10, 25]]}, 'author': [{'given': 'A', 'family': 'Wang'}]}
    p = retriever.convert_to_paper((item, 'NEJM AI'))
    assert p.published == '2026-09-10' and p.abstract == 'Evidence & safety.'
    assert p.creators[0]['lastName'] == 'Wang'
    assert not recent_papers([p], NOW)
    retriever.query_override = ('journal:NEJM AI', '2836-9386')
    monkeypatch.setattr(retriever, '_get_json', lambda *a: {'items': [item] * 100})
    with pytest.raises(ValueError, match='pagination'):
        retriever._retrieve_raw_papers()


def test_preview_only_reads_cache_and_excludes_sent_aliases(monkeypatch):
    s = State('https://feedback.example', 'a' * 64)
    calls = []
    def call(action, **values):
        calls.append(action)
        if action == 'seen':
            return {'identifiers': ['pmid:123']}
        return {'value': [1, 2]}
    monkeypatch.setattr(s, 'call', call)
    p = paper(pmid='123')
    assert s.unseen([p]) == []
    cache = ReadOnlyCache(s)
    assert cache.get('cache:vector') == [1, 2]
    cache.set('cache:vector', [3])
    assert calls == ['seen', 'get']
    with pytest.raises(ValueError):
        cache.get('checkpoint:arxiv')


def test_preview_and_preparation_cannot_be_combined(config, monkeypatch):
    config.executor.dry_run = True
    config.executor.prepare_only = True
    monkeypatch.setattr('zotero_arxiv_daily.executor.State', lambda *a: pytest.fail('Unexpected state access'))
    executor = Executor.__new__(Executor)
    executor.config = config
    with pytest.raises(ValueError, match='Choose either'):
        executor.run()


def test_email_displays_both_jcr_if_and_reading_card():
    p = paper(journal='npj Digital Medicine', item_type='journalArticle',
              metrics={'jcr': 'Q1', 'if': '18.0', 'source': 'EasyScholar'},
              tldr='做了什么：整合PGx证据。\n新意与价值：可追溯建议。',
              recommendation_group='core', citation_count=0, citation_source='Crossref', citation_checked='2026-10-26')
    result = render_email([p])
    assert all(s in result for s in ['JCR Q1', 'IF 18.0', '年份未核实', '核心课题', '引用 0', '新意与价值'])


def test_daily_pipeline_carries_backlog_and_never_resends(config, monkeypatch, tmp_path):
    from tests.canned_responses import make_stub_openai_client, make_stub_zotero_client
    state = MemoryState()
    client = make_stub_openai_client()
    monkeypatch.setattr('zotero_arxiv_daily.executor.State', lambda *a: state)
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero', lambda *a: make_stub_zotero_client())
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI', lambda **k: client)
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI', lambda **k: client)
    sent = []
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email', lambda *a, **k: sent.append(k['batch_id']))
    def screen(papers, *args):
        for p in papers:
            p.recommendation_group, p.relevance_level, p.reason = 'core', 3, 'PGx直接对标'
        return papers
    monkeypatch.setattr('zotero_arxiv_daily.executor.screen_papers', screen)
    config.screening.enabled = True
    config.retrieval.max_age_days = 30
    config.executor.max_paper_num = 10
    config.executor.min_score = 0
    config.executor.group_weights = {'core': 5, 'cross': 3, 'explore': 2}
    config.executor.preview_path = str(tmp_path / 'prepared.html')
    executor = Executor(config)
    papers = [make_sample_paper(title=f'PGx {i}', doi=f'10.1/{i}', url=f'https://arxiv.org/abs/2610.{i:05d}',
              published=datetime.now(timezone.utc).date().isoformat()) for i in range(15)]
    monkeypatch.setattr(executor.retrievers['arxiv'], 'retrieve_papers', lambda: papers)
    config.executor.prepare_only = True
    executor.run()
    assert sent == [] and len(state.pending()) == 15
    assert not any(action == 'batch' for action, _ in state.events)
    config.executor.prepare_only = False
    executor.run()
    assert len(sent) == 1 and len(state.pending()) == 5
    executor.run()
    assert len(sent) == 2 and len(state.pending()) == 0
    executor.run()
    assert len(sent) == 2
