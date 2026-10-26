# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""Crossref complements PubMed's incomplete coverage of selected journals."""
from datetime import datetime, timedelta, timezone
import html
import re
from time import sleep

import requests

from .base import BaseRetriever, register_retriever
from ..protocol import Paper


def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


@register_retriever("journal")
class JournalRetriever(BaseRetriever):
    def queries(self):
        return [("journal:" + j.name, j.issn) for j in self.retriever_config.get("journals", [])]

    def _get_json(self, issn, params):
        for attempt in range(3):
            try:
                response = requests.get("https://api.crossref.org/journals/" + issn + "/works", params=params,
                                        headers={"User-Agent": "zotero-arxiv-daily/1.0"}, timeout=30)
                response.raise_for_status()
                return response.json()["message"]
            except (requests.RequestException, ValueError, KeyError):
                if attempt == 2:
                    raise RuntimeError("Crossref journal retrieval failed; checkpoint retained") from None
                sleep(2 ** attempt)

    def _retrieve_raw_papers(self):
        end = datetime.now(timezone.utc)
        start, end = self.window or (end - timedelta(days=30), end)
        publication_start = end - timedelta(days=int(self.config.retrieval.get("max_age_days") or 30))
        queries = [self.query_override] if self.query_override else self.queries()
        result = []
        for label, issn in queries:
            if not re.fullmatch(r"\d{4}-\d{3}[\dXx]", issn):
                raise ValueError("Invalid journal ISSN")
            cursor, seen = "*", set()
            for _ in range(100):
                message = self._get_json(issn, {"filter": f"from-index-date:{start.date()},until-index-date:{end.date()},from-pub-date:{publication_start.date()},until-pub-date:{end.date()}",
                                                "rows": 100, "cursor": cursor,
                                                "select": "DOI,title,abstract,author,container-title,published-online,published-print,published,issued,ISSN,volume,issue,page,type,is-referenced-by-count"})
                items = message.get("items", [])
                result.extend((item, label[8:]) for item in items if item.get("type") == "journal-article")
                if len(items) < 100:
                    break
                next_cursor = message.get("next-cursor")
                if not next_cursor or next_cursor in seen:
                    raise ValueError("Incomplete Crossref cursor pagination")
                seen.add(next_cursor)
                cursor = next_cursor
            else:
                raise ValueError("Crossref pagination budget exceeded; checkpoint retained")
        return result

    def convert_to_paper(self, raw):
        item, name = raw
        title, doi = clean((item.get("title") or [""])[0]), item.get("DOI", "")
        if not title or not doi:
            raise ValueError("Crossref journal item missing title or DOI")
        date = next((item[k]["date-parts"][0] for k in ("published-online", "published", "issued", "published-print")
                     if item.get(k, {}).get("date-parts")), [])
        published = "-".join(str(v).zfill(4 if i == 0 else 2) for i, v in enumerate(date))
        creators, authors = [], []
        for author in item.get("author", []):
            if author.get("family"):
                creators.append({"creatorType": "author", "lastName": author["family"], "firstName": author.get("given", "")})
                authors.append((author.get("given", "") + " " + author["family"]).strip())
            elif author.get("name"):
                creators.append({"creatorType": "author", "name": author["name"]})
                authors.append(author["name"])
        count = item.get("is-referenced-by-count")
        return Paper(source="journal", title=title, abstract=clean(item.get("abstract")), authors=authors,
                     creators=creators, doi=doi, url="https://doi.org/" + doi, published=published,
                     journal=clean((item.get("container-title") or [name])[0]), issn=(item.get("ISSN") or [""])[0],
                     volume=item.get("volume", ""), issue=item.get("issue", ""), pages=item.get("page", ""),
                     journal_matches=[name], item_type="journalArticle",
                     citation_count=count if type(count) is int else None, citation_source="Crossref",
                     citation_checked=datetime.now(timezone.utc).date().isoformat())
