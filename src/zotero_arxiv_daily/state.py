# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Small authenticated client; D1 state stays behind the Worker, never in Git."""
from dataclasses import asdict, fields
import hashlib
import json
from urllib.parse import urlparse

import requests

from .protocol import Paper


def cache_key(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class State:
    def __init__(self, base_url, token):
        if urlparse(base_url).scheme != "https" or not token or len(token) < 32:
            raise ValueError("Configure an HTTPS feedback URL and a random API token of at least 32 characters")
        self.url = base_url.rstrip("/") + "/api/state"
        self.token = token
        self.memory = {}
        self.session = requests.Session()

    def call(self, action, **values):
        try:
            response = self.session.post(self.url, json={"action": action, **values},
                                     headers={"Authorization": "Bearer " + self.token}, timeout=60)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError(f"State operation '{action}' failed; no checkpoint or delivery success assumed") from None

    def get(self, key):
        if key not in self.memory:
            self.memory[key] = self.call("get", key=key)["value"]
        return self.memory[key]

    def set(self, key, value):
        self.call("set", key=key, value=value)
        self.memory[key] = value

    def ingest(self, papers):
        # Each paper uses two reads and one D1 batch; Free allows 50 calls per invocation.
        for offset in range(0, len(papers), 10):
            batch = []
            for paper in papers[offset:offset+10]:
                payload = asdict(paper)
                payload.update(identifiers=paper.identifiers(), save_url="", reject_url="", full_text=None)
                batch.append(payload)
            self.call("ingest", papers=batch)

    def pending(self):
        after, papers = "", []
        allowed = {f.name for f in fields(Paper)}
        while True:
            result = self.call("pending", after=after)
            papers.extend(Paper(**{k:v for k,v in p.items() if k in allowed}) for p in result["papers"])
            if len(result["papers"]) < 100:
                break
            after = result["after"]
        return papers

    def unseen(self, papers):
        identifiers = sorted({i for p in papers for i in p.identifiers()})
        seen = set()
        for offset in range(0, len(identifiers), 80):
            seen.update(self.call("seen", identifiers=identifiers[offset:offset + 80])["identifiers"])
        return [p for p in papers if not set(p.identifiers()) & seen]


class ReadOnlyCache:
    """Preview may reuse cached results; it cannot reserve papers or advance checkpoints."""
    def __init__(self, state):
        self.state = state

    def get(self, key):
        if not key.startswith("cache:"):
            raise ValueError("Preview cache only reads cache keys")
        return self.state.get(key)

    def set(self, key, value):
        pass
