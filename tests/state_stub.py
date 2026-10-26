# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from dataclasses import asdict, fields
from zotero_arxiv_daily.protocol import Paper
from zotero_arxiv_daily.dedup import merge_papers


class MemoryState:
    """Test-only substitute; production always uses authenticated Worker state."""
    def __init__(self, *args):
        self.values, self.papers, self.sent, self.events = {}, [], set(), []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def ingest(self, papers):
        self.papers = merge_papers(self.papers + papers)

    def pending(self):
        allowed = {f.name for f in fields(Paper)}
        return [Paper(**{k:v for k,v in asdict(p).items() if k in allowed}) for p in self.papers if p.paper_id not in self.sent]

    def call(self, action, **values):
        self.events.append((action, values))
        if action == 'batch':
            self.sent.update(values['ids'])
            return {'id':'test-batch', 'links':[{'paper_id':i,'save':'https://feedback.example/confirm?token=save',
                                               'reject':'https://feedback.example/confirm?token=reject'} for i in values['ids']]}
        return {'ok':True}
