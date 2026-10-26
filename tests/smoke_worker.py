# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Explicit local-only end-to-end smoke. Start Wrangler with test/local-entry.js first.

Run from repository root: uv run python -m tests.smoke_worker
Uses stub retrieval/Embedding/LLM/SMTP/Zotero, real Python HTTP client and local Worker+D1.
"""
from html import unescape
from pathlib import Path
import re
import uuid

import requests
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.dedup import merge_papers
from tests.canned_responses import make_sample_paper, make_stub_openai_client, make_stub_zotero_client


def main():
    import zotero_arxiv_daily.executor as module
    import zotero_arxiv_daily.reranker.api as api_module
    base = 'http://127.0.0.1:18787'
    token = 'local-smoke-test-token-0123456789abcdef'
    original_post = requests.Session.post
    # Production State rejects HTTP. Only this explicit local test redirects its requests.
    def local_post(session, url, **kwargs):
        if url.startswith('https://feedback.example/'):
            url = url.replace('https://feedback.example',base)
        assert url.startswith(base + '/')
        return original_post(session, url,**kwargs)
    requests.Session.post = local_post
    with initialize_config_dir(config_dir=str(Path(__file__).resolve().parents[1] / 'config'),version_base=None):
        config=compose(config_name='default')
    OmegaConf.update(config,'zotero.user_id','123')
    OmegaConf.update(config,'zotero.api_key','local-test-only')
    OmegaConf.update(config,'zotero.include_path',None)
    OmegaConf.update(config,'zotero.ignore_path',None)
    OmegaConf.update(config,'source.arxiv.category',['cs.AI'])
    OmegaConf.update(config,'executor.source',['arxiv'])
    OmegaConf.update(config,'executor.reranker','api')
    OmegaConf.update(config,'executor.dry_run',False)
    OmegaConf.update(config,'executor.debug',False)
    OmegaConf.update(config,'executor.max_paper_num',3)
    OmegaConf.update(config,'executor.preview_use_cache',False)
    OmegaConf.update(config,'executor.group_weights',None)
    OmegaConf.update(config,'screening.enabled',False)
    OmegaConf.update(config,'retrieval.max_age_days',None)
    OmegaConf.update(config,'journal_metrics.filter_by_jcr',False)
    OmegaConf.update(config,'journal_metrics.fetch_citations',False)
    OmegaConf.update(config,'topics',[])
    OmegaConf.update(config,'llm.api.key','local-test-only')
    OmegaConf.update(config,'llm.api.base_url','https://example.invalid')
    OmegaConf.update(config,'llm.generation_kwargs.model','stub')
    OmegaConf.update(config,'reranker.api.model','stub')
    OmegaConf.update(config,'feedback.base_url','https://feedback.example')
    OmegaConf.update(config,'feedback.api_token',token)
    client=make_stub_openai_client()
    module.OpenAI=lambda **kwargs:client
    api_module.OpenAI=lambda **kwargs:client
    module.zotero.Zotero=lambda *args:make_stub_zotero_client()
    # Capture the rendered message, so no real SMTP service is involved.
    messages=[]
    module.send_email=lambda cfg,html,**kwargs:messages.append(html)
    run_id=uuid.uuid4().hex
    papers=merge_papers([make_sample_paper(title=f'Smoke {run_id} {i}',url=f'https://example.org/{run_id}/{i}',doi=f'10.9999/{run_id}-{i}',topics=['smoke']) for i in range(3)])
    executor=Executor(config)
    executor.retrievers['arxiv'].retrieve_papers=lambda:papers
    executor.run()
    assert len(messages)==1
    html=messages[0]
    links=[unescape(s) for s in re.findall(r'href="([^"]+/confirm\?token=[^"]+)"',html)]
    assert len(links)==6
    def confirm(link):
        url=link.replace('https://feedback.example',base)
        session=requests.Session()
        page=session.get(url,timeout=10)
        page.raise_for_status()
        assert '确认' in page.text
        match=re.search(r'name="csrf" value="([^"]+)"',page.text)
        if not match:
            return page.text
        csrf=match[1]
        nonce=page.headers['Set-Cookie'].split(';')[0]
        result=session.post(base+'/confirm',data={'token':url.split('token=')[1],'csrf':csrf},
                            headers={'Origin':base,'Cookie':nonce},timeout=10)
        result.raise_for_status()
        return result.text
    assert '已保存' in confirm(links[0])
    assert '无需重复' in confirm(links[0])
    assert '不感兴趣' in confirm(links[3])
    # Third remains pending. A second run must not send the same batch again.
    executor.run()
    assert len(messages)==1
    Path('outputs').mkdir(exist_ok=True)
    # The preview deliberately strips live operation URLs.
    Path('outputs/local-smoke-email.html').write_text(re.sub(r'/confirm\?token=[a-f0-9]+','/confirm?preview=1',html),encoding='utf-8')
    print('PASS: Python retrieval/ranking/email -> HTTP Worker+D1 -> explicit save/reject -> duplicate retry; third paper unhandled, no repeat email.')


if __name__=='__main__':
    main()
