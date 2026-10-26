# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
"""NCBI E-utilities, CRDT windows and batched authoritative metadata."""
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
import xml.etree.ElementTree as ET
import calendar

import requests

from .base import BaseRetriever, register_retriever
from ..protocol import Paper


def text(node) -> str:
    return "" if node is None else " ".join("".join(node.itertext()).split())


def publication_date(node, article):
    electronic = list(article.findall('ArticleDate[@DateType="Electronic"]'))
    electronic += list(node.findall('PubmedData/History/PubMedPubDate[@PubStatus="epublish"]'))
    electronic += list(node.findall('PubmedData/History/PubMedPubDate[@PubStatus="aheadofprint"]'))
    dates = electronic or list(article.findall('ArticleDate')) or list(article.findall('Journal/JournalIssue/PubDate'))
    result = []
    months = {name.casefold(): str(i).zfill(2) for i, name in enumerate(calendar.month_abbr) if name}
    for date in dates:
        values = [text(date.find(k)) for k in ('Year', 'Month', 'Day')]
        if values[1]:
            values[1] = months.get(values[1][:3].casefold(), values[1].zfill(2))
        if values[2]:
            values[2] = values[2].zfill(2)
        value = '-'.join(v for v in values if v)
        if value:
            result.append(value)
    return min(result, default='')


@register_retriever("pubmed")
class PubmedRetriever(BaseRetriever):
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def __init__(self, config):
        super().__init__(config)
        self.last_request = 0

    def queries(self):
        result = []
        for topic in self.config.get("topics", []):
            query = topic.get("pubmed_query", "")
            if query:
                result.append((topic.name, query))
        for journal in self.retriever_config.get("journals", []):
            if '"' in journal:
                raise ValueError("Journal names must not contain quotes")
            result.append(("journal:" + journal, f'"{journal}"[Journal]'))
        return result

    def request(self, endpoint, params):
        common = {"db": "pubmed", "tool": "zotero-literature-weekly",
                  "email": self.retriever_config.get("email", "")}
        api_key = self.retriever_config.get("api_key")
        if api_key:
            common["api_key"] = api_key
        for attempt in range(4):
            sleep(max(0, 0.4 - (monotonic() - self.last_request)))
            self.last_request = monotonic()
            try:
                response = requests.get(self.base_url + endpoint, params=common | params, timeout=60)
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == 3:
                    # Do not expose the NCBI key from a requests exception URL.
                    raise RuntimeError("NCBI request failed after retries") from None
                sleep(2 ** attempt)

    def search_ids(self, query, start, end):
        params = {"term": query, "datetype": "crdt", "mindate": start.strftime("%Y/%m/%d"),
                  "maxdate": end.strftime("%Y/%m/%d"), "retmode": "json", "retmax": 500}
        first = self.request("esearch.fcgi", params).json()
        if "error" in first or "errorlist" in first.get("esearchresult", {}):
            raise ValueError("NCBI rejected query")
        result = first["esearchresult"]
        count = int(result["count"])
        if count > 9999:
            if start.date() == end.date():
                raise ValueError("PubMed query exceeds 9999 results in one day; narrow the query")
            middle = start.date() + (end.date() - start.date()) // 2
            split = datetime.combine(middle, datetime.min.time(), tzinfo=timezone.utc)
            return self.search_ids(query, start, split) + self.search_ids(query, split + timedelta(days=1), end)
        ids = list(result["idlist"])
        for offset in range(len(ids), count, 500):
            page = self.request("esearch.fcgi", params | {"retstart": offset}).json()["esearchresult"]
            ids.extend(page["idlist"])
        if len(set(ids)) != count:
            raise ValueError("PubMed result set changed during paging; retry window")
        return ids

    def _retrieve_raw_papers(self):
        end = datetime.now(timezone.utc)
        start, end = self.window or (end - timedelta(days=30), end)
        queries = [self.query_override] if self.query_override else self.queries()
        if not queries:
            raise ValueError("Configure PubMed topics or journal subscriptions first")
        matches = {}
        for label, query in queries:
            for pmid in self.search_ids(query, start, end):
                matches.setdefault(pmid, []).append(label)
        ids, articles = list(matches), []
        for offset in range(0, len(ids), 200):
            batch = ids[offset:offset + 200]
            root = ET.fromstring(self.request("efetch.fcgi", {"id": ",".join(batch), "retmode": "xml"}).content)
            nodes = root.findall("PubmedArticle")
            returned = {text(a.find("MedlineCitation/PMID")) for a in nodes}
            # PGx queries also return updated Bookshelf chapters. They are not journal papers,
            # but must count toward EFetch completeness so they do not discard the whole article batch.
            returned.update(text(a.find("BookDocument/PMID")) for a in root.findall("PubmedBookArticle"))
            if returned != set(batch):
                raise ValueError("Incomplete PubMed EFetch response")
            articles.extend((a, matches[text(a.find("MedlineCitation/PMID"))]) for a in nodes)
        return articles

    def convert_to_paper(self, raw):
        node, labels = raw
        article = node.find("MedlineCitation/Article")
        if article is None:
            raise ValueError("PubMed article missing")
        pmid = text(node.find("MedlineCitation/PMID"))
        title = text(article.find("ArticleTitle"))
        if not pmid or not title:
            raise ValueError("PubMed title or PMID missing")
        creators, authors = [], []
        for author in article.findall("AuthorList/Author"):
            collective = text(author.find("CollectiveName"))
            if collective:
                creators.append({"creatorType": "author", "name": collective})
                authors.append(collective)
            else:
                last, first = text(author.find("LastName")), text(author.find("ForeName")) or text(author.find("Initials"))
                creators.append({"creatorType": "author", "lastName": last, "firstName": first})
                authors.append((first + " " + last).strip())
        abstract = "\n".join((a.get("Label", "") + ": " if a.get("Label") else "") + text(a)
                             for a in article.findall("Abstract/AbstractText"))
        published = publication_date(node, article)
        doi = next((text(i) for i in node.findall("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "doi"), "")
        doi = doi or next((text(i) for i in article.findall("ELocationID") if i.get("EIdType") == "doi"), "")
        item_type = "preprint" if any(text(p) == "Preprint" for p in article.findall("PublicationTypeList/PublicationType")) else "journalArticle"
        return Paper(source="pubmed", title=title, authors=authors, creators=creators, abstract=abstract,
                     url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", pmid=pmid, doi=doi,
                     published=published, item_type=item_type, journal=text(article.find("Journal/Title")),
                     issn=text(article.find("Journal/ISSN")), volume=text(article.find("Journal/JournalIssue/Volume")),
                     issue=text(article.find("Journal/JournalIssue/Issue")), pages=text(article.find("Pagination/MedlinePgn")),
                     topics=[s for s in labels if not s.startswith("journal:")],
                     journal_matches=[s[8:] for s in labels if s.startswith("journal:")])
