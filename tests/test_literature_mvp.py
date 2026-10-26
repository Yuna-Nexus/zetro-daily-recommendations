# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timezone
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from omegaconf import OmegaConf, open_dict

from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.dedup import merge_papers
from zotero_arxiv_daily.retriever.pubmed_retriever import PubmedRetriever
from zotero_arxiv_daily.selection import select_papers
from zotero_arxiv_daily.construct_email import render_email
from zotero_arxiv_daily.executor import Executor
from tests.state_stub import MemoryState
from tests.canned_responses import make_sample_paper, make_stub_openai_client, make_stub_zotero_client, make_stub_smtp


XML = '''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
<ArticleTitle>Mixed <i>markup</i> title</ArticleTitle><Abstract><AbstractText Label="METHODS">We tested.</AbstractText><AbstractText Label="RESULTS">It worked.</AbstractText></Abstract>
<AuthorList><Author><LastName>Wang</LastName><ForeName>Li</ForeName></Author><Author><CollectiveName>Study Group</CollectiveName></Author></AuthorList>
<Journal><Title>Example Journal</Title><ISSN>1234-5678</ISSN><JournalIssue><Volume>4</Volume><Issue>2</Issue><PubDate><Year>2026</Year><Month>10</Month></PubDate></JournalIssue></Journal>
<Pagination><MedlinePgn>12-18</MedlinePgn></Pagination></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/Test</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''


def pubmed(config):
    config.topics = OmegaConf.create([{'name':'topic-a','description':'biology','pubmed_query':'biology[tiab]'}])
    config.source.pubmed.journals = ['Example Journal']
    return PubmedRetriever(config)


def test_pubmed_metadata_preserves_original_fields(config):
    p = pubmed(config).convert_to_paper((ET.fromstring(XML)[0], ['topic-a','journal:Example Journal']))
    assert p.title == 'Mixed markup title'
    assert p.creators[0]['lastName'] == 'Wang'
    assert p.creators[1]['name'] == 'Study Group'
    assert p.abstract == 'METHODS: We tested.\nRESULTS: It worked.'
    assert p.published == '2026-10'
    assert p.pmid == '123' and p.doi == '10.1000/Test'
    assert p.journal_matches == ['Example Journal']
    assert set(p.identifiers()) == {'doi:10.1000/test','pmid:123'}


def test_pubmed_preprint_is_not_misclassified_as_journal_article(config):
    xml=XML.replace('</Article>', '<PublicationTypeList><PublicationType>Preprint</PublicationType></PublicationTypeList></Article>')
    p=pubmed(config).convert_to_paper((ET.fromstring(xml)[0],['topic-a']))
    assert p.item_type=='preprint'


def test_pubmed_topic_and_journal_union_and_efetch_completeness(config, monkeypatch):
    retriever = pubmed(config)
    queries = retriever.queries()
    assert queries == [('topic-a','biology[tiab]'),('journal:Example Journal','"Example Journal"[Journal]')]
    monkeypatch.setattr(retriever, 'search_ids', lambda *args:['123'])
    monkeypatch.setattr(retriever, 'request', lambda *args:SimpleNamespace(content=XML.encode()))
    result = retriever._retrieve_raw_papers()
    assert len(result) == 1 and len(result[0][1]) == 2
    monkeypatch.setattr(retriever, 'request', lambda *args:SimpleNamespace(content=b'<PubmedArticleSet/>'))
    with pytest.raises(ValueError, match='Incomplete'):
        retriever._retrieve_raw_papers()


def test_pubmed_crdt_pagination_and_large_query_split(config, monkeypatch):
    retriever = pubmed(config)
    calls = []
    def request(endpoint, params):
        calls.append(params)
        offset = params.get('retstart', 0)
        return SimpleNamespace(json=lambda:{'esearchresult':{'count':'501','idlist':[str(i) for i in range(offset,min(offset+500,501))]}})
    monkeypatch.setattr(retriever, 'request', request)
    start,end = datetime(2026,10,1,tzinfo=timezone.utc),datetime(2026,10,3,tzinfo=timezone.utc)
    assert len(retriever.search_ids('biology',start,end)) == 501
    assert calls[0]['datetype'] == 'crdt'
    def oversized(endpoint, params):
        size = '10000' if params['mindate'] != params['maxdate'] else '1'
        return SimpleNamespace(json=lambda:{'esearchresult':{'count':size,'idlist':[params['mindate']]}})
    monkeypatch.setattr(retriever, 'request', oversized)
    assert len(retriever.search_ids('biology',start,end)) == 3
    monkeypatch.setattr(retriever, 'request', lambda *args:SimpleNamespace(json=lambda:{'esearchresult':{'count':'10000','idlist':[]}}))
    with pytest.raises(ValueError, match='one day'):
        retriever.search_ids('biology',start,start)


def test_identity_normalization_and_late_bridge():
    a = make_sample_paper(doi='https://doi.org/10.1/ABC', arxiv_id='2401.00001v1',topics=['a'])
    b = make_sample_paper(doi='doi:10.1/abc',pmid='123',topics=['b'])
    c = make_sample_paper(arxiv_id='2401.00001v2',topics=['c'])
    merged = merge_papers([a,b,c])
    assert len(merged) == 1
    assert merged[0].topics == ['a','b','c']
    assert merged[0].pmid == '123'
    different = make_sample_paper(title=a.title,url='https://example.org/other')
    assert len(merge_papers([a,different])) == 2


def test_selection_twenty_two_exploratory_and_relevance_floor():
    papers = [make_sample_paper(url=f'https://example.org/{i}',score=9-i/100,topics=['a'] if i<20 else ['b']) for i in range(30)]
    selected = select_papers(papers,20,.1,3)
    assert len(selected) == 20
    assert sum(p.exploratory for p in selected) == 2
    assert selected[:18] == papers[:18]
    assert all(p.topics == ['b'] for p in selected[18:])
    assert not select_papers([make_sample_paper(score=2)],20,.1,3)


def test_html_escapes_metadata_and_disallows_script_links():
    p = make_sample_paper(title='<script>bad</script>',tldr='<img src=x>',url='javascript:alert(1)',reason='<b>fake</b>')
    html = render_email([p])
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'javascript:' not in html and '&lt;img' in html


def test_weekly_rerun_partial_failure_and_uncertain_delivery(config,monkeypatch):
    state = MemoryState()
    monkeypatch.setattr('zotero_arxiv_daily.executor.State',lambda *args:state)
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero',lambda *args:make_stub_zotero_client())
    client = make_stub_openai_client()
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI',lambda **kw:client)
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI',lambda **kw:client)
    sent=[]
    monkeypatch.setattr('smtplib.SMTP',make_stub_smtp(sent))
    executor=Executor(config)
    retriever=executor.retrievers['arxiv']
    monkeypatch.setattr(retriever,'retrieve_papers',lambda:[make_sample_paper(doi='10.1/ABC')])
    executor.run()
    executor.run()
    assert len(sent)==1
    assert 'text/html' in sent[0][2]
    assert [e[1]['status'] for e in state.events if e[0]=='delivery']==['sending','sent']
    checkpoints = {k:v for k,v in state.values.items() if k.startswith('checkpoint:')}
    monkeypatch.setattr(retriever,'retrieve_papers',lambda:(_ for _ in ()).throw(RuntimeError('network')))
    with pytest.raises(RuntimeError,match='Retrieval failed'):
        executor.run()
    assert {k:v for k,v in state.values.items() if k.startswith('checkpoint:')}==checkpoints


def test_dry_run_never_touches_production_state_or_smtp(config,monkeypatch,tmp_path):
    config.executor.dry_run=True
    config.executor.preview_path=str(tmp_path/'preview.html')
    monkeypatch.setattr('zotero_arxiv_daily.executor.State',lambda *a:pytest.fail('production state touched'))
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero',lambda *a:make_stub_zotero_client())
    client=make_stub_openai_client()
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI',lambda **kw:client)
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI',lambda **kw:client)
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email',lambda *a,**kw:pytest.fail('SMTP touched'))
    executor=Executor(config)
    monkeypatch.setattr(executor.retrievers['arxiv'],'retrieve_papers',lambda:[make_sample_paper()])
    executor.run()
    html=(tmp_path/'preview.html').read_text(encoding='utf-8')
    assert '预览模式' in html and 'confirm?token=' not in html


def test_existing_zotero_without_abstract_is_still_in_duplicate_index(config,monkeypatch):
    items=[{'data':{'title':'Already saved','DOI':'https://doi.org/10.1/ABC','abstractNote':'',
                    'dateAdded':'2026-01-01T00:00:00Z','collections':[]}}]
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero',lambda *a:make_stub_zotero_client(items=items))
    executor=Executor.__new__(Executor)
    executor.config=config
    assert executor.fetch_zotero_corpus()==[]
    assert 'doi:10.1/abc' in executor.library_ids


def test_embedding_cache_avoids_repeat_calls(config,monkeypatch):
    from zotero_arxiv_daily.reranker.api import ApiReranker
    calls=[]
    def create(**kwargs):
        calls.append(kwargs['input'])
        return SimpleNamespace(data=[SimpleNamespace(index=i,embedding=[1.,float(i),1.]) for i,_ in enumerate(kwargs['input'])])
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI',lambda **kw:SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    reranker=ApiReranker(config)
    reranker.state=MemoryState()
    first=reranker.get_similarity_score(['new paper'],['collection paper'])
    second=reranker.get_similarity_score(['new paper'],['collection paper'])
    assert first.shape==second.shape==(1,1)
    assert len(calls)==1


def test_state_ingestion_respects_worker_batch_limit_without_losing_papers(monkeypatch):
    from zotero_arxiv_daily.state import State
    state=State('https://feedback.example','a'*64)
    batches=[]
    def call(action,**values):
        assert action=='ingest'
        assert len(values['papers'])<=10
        batches.append(values['papers'])
    monkeypatch.setattr(state,'call',call)
    papers=merge_papers([make_sample_paper(title=f'paper {i}',url=f'https://example.org/{i}') for i in range(25)])
    state.ingest(papers)
    assert [p['paper_id'] for batch in batches for p in batch]==[p.paper_id for p in papers]


def test_local_embedding_cache_separates_truncation_settings(config,monkeypatch):
    import sys
    import numpy as np
    from zotero_arxiv_daily.reranker.local import LocalReranker
    calls=[]
    class Encoder:
        def encode(self,texts,**kwargs):
            calls.append(self.max_seq_length)
            return np.array([[float(len(t[:self.max_seq_length])),1.] for t in texts])
        def similarity(self,a,b):
            return SimpleNamespace(numpy=lambda:a @ b.T)
    encoder=Encoder()
    monkeypatch.setitem(sys.modules,'sentence_transformers',SimpleNamespace(SentenceTransformer=lambda *a,**kw:encoder))
    config.executor.debug=True
    with open_dict(config.reranker.local):
        config.reranker.local.max_seq_length=1024
    reranker=LocalReranker(config)
    reranker.state=MemoryState()
    first=reranker.get_similarity_score(['x'*1500],['interest'])
    assert np.array_equal(first,reranker.get_similarity_score(['x'*1500],['interest']))
    config.reranker.local.max_seq_length=256
    shorter=reranker.get_similarity_score(['x'*1500],['interest'])
    assert calls==[1024,256]
    assert not np.array_equal(first,shorter)


def test_uncertain_smtp_does_not_mark_sent_or_resend(config,monkeypatch):
    state=MemoryState()
    monkeypatch.setattr('zotero_arxiv_daily.executor.State',lambda *a:state)
    monkeypatch.setattr('zotero_arxiv_daily.executor.zotero.Zotero',lambda *a:make_stub_zotero_client())
    client=make_stub_openai_client()
    monkeypatch.setattr('zotero_arxiv_daily.executor.OpenAI',lambda **kw:client)
    monkeypatch.setattr('zotero_arxiv_daily.reranker.api.OpenAI',lambda **kw:client)
    attempts=[]
    def fail(*args,**kwargs):
        attempts.append(1)
        raise OSError('lost SMTP acknowledgement')
    monkeypatch.setattr('zotero_arxiv_daily.executor.send_email',fail)
    executor=Executor(config)
    monkeypatch.setattr(executor.retrievers['arxiv'],'retrieve_papers',lambda:[make_sample_paper()])
    with pytest.raises(OSError):
        executor.run()
    executor.run()
    assert len(attempts)==1
    assert [values['status'] for action,values in state.events if action=='delivery']==['sending']
