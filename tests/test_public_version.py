# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""The public version must use the configured field, including nonmedical research."""
import json
import pytest
from omegaconf import MissingMandatoryValue

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.screening import screen_papers
from zotero_arxiv_daily.selection import select_papers
from zotero_arxiv_daily.selection import shortlist_papers
from zotero_arxiv_daily.executor import Executor


def test_screening_uses_supplied_nonmedical_profile(monkeypatch):
    prompts = []
    def request(client, params, messages):
        prompts.append(messages[-1]['content'])
        return json.dumps({'papers': [{'id': 0, 'group': 'core', 'level': 3,
                                      'reason': '直接支持组合优化算法对比'}]})
    monkeypatch.setattr('zotero_arxiv_daily.screening._request_llm', request)
    p = make_sample_paper(title='Combinatorial optimization', abstract='An evaluated graph algorithm.')
    assert screen_papers([p], None, {}, {'profile': 'Graph algorithms for combinatorial optimization'}) == [p]
    assert 'Graph algorithms for combinatorial optimization' in prompts[0]
    assert 'PGx' not in prompts[0] and 'patient-specific medication' not in prompts[0]
    assert p.topics == ['核心课题']


def test_reading_card_follows_reader_scope(monkeypatch):
    prompts = []
    def request(client, params, messages):
        prompts.append(messages[-1]['content'])
        return '阅读卡'
    monkeypatch.setattr('zotero_arxiv_daily.protocol._request_llm', request)
    p = make_sample_paper(title='Graph algorithm', abstract='An evaluated graph algorithm.',
                          reason='用于组合优化实验设计')
    assert p.generate_tldr(None, {'language': 'Chinese'}) == '阅读卡'
    assert p.reason in prompts[0] and 'PGx' not in prompts[0] and 'DDGI' not in prompts[0]


def test_non_ai_research_is_not_demoted_by_field():
    core = make_sample_paper(title='Graph algorithm', abstract='A combinatorial proof.', score=8,
                             published='2026-10-24', relevance_level=3, recommendation_group='core')
    adjacent = make_sample_paper(title='LLM agent', abstract='A large language model.', score=8,
                                 published='2026-10-24', relevance_level=2, recommendation_group='core')
    assert select_papers([adjacent, core], limit=1, group_weights={'core': 1}) == [core]


def test_no_group_quotas_shortlists_by_relevance():
    from datetime import datetime, timezone
    core = make_sample_paper(title='Direct research', published='2026-10-24', relevance_level=3,
                             recommendation_group='core')
    cross = make_sample_paper(title='Related method', published='2026-10-24', relevance_level=2,
                              recommendation_group='cross')
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert shortlist_papers([cross, core], 1, None, now) == [core]


@pytest.mark.parametrize('count', ['???', 0, -1, 101, True, 1.5])
def test_count_must_be_chosen_before_external_initialization(config, monkeypatch, count):
    config.executor.max_paper_num = count
    monkeypatch.setattr('zotero_arxiv_daily.executor.get_reranker_cls',
                        lambda *a: pytest.fail('Model initialization must not happen'))
    with pytest.raises((MissingMandatoryValue, ValueError)):
        Executor(config)
