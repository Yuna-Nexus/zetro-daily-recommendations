// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
const KEY = /^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$/;

export function newItemKey() {
  const chars = '23456789ABCDEFGHIJKLMNPQRSTUVWXYZ';
  return Array.from(crypto.getRandomValues(new Uint8Array(8)), n => chars[n % 32]).join('');
}

function identities(data) {
  const ids = [];
  if (data.url) ids.push('url:' + data.url);
  if (data.DOI) ids.push('doi:' + data.DOI.trim().replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/i, '').toLowerCase());
  const pmid = data.PMID || data.extra?.match(/^PMID:\s*(\d+)/m)?.[1];
  if (pmid) ids.push('pmid:' + pmid);
  const arxiv = (data.url || '').match(/arxiv\.org\/abs\/([^?#]+)/)?.[1] || (data.repository === 'arXiv' ? data.archiveID : '');
  if (arxiv) ids.push('arxiv:' + arxiv.replace(/v\d+$/i, ''));
  return ids;
}

export function itemData(paper, collection) {
  const data = {
    itemType: paper.item_type || (paper.source === 'pubmed' ? 'journalArticle' : 'preprint'),
    title: paper.title, abstractNote: paper.abstract || '', date: paper.published || '',
    creators: paper.creators?.length ? paper.creators : paper.authors.map(name => ({creatorType: 'author', name})),
    DOI: paper.doi || '', url: paper.url,
    tags: paper.topics.map(tag => ({tag})), collections: [collection],
  };
  if (data.itemType === 'journalArticle') {
    Object.assign(data, {publicationTitle: paper.journal || '', ISSN: paper.issn || '',
      volume: paper.volume || '', issue: paper.issue || '', pages: paper.pages || '',
      extra: paper.pmid ? 'PMID: ' + paper.pmid : ''});
  } else {
    Object.assign(data, {repository: paper.source === 'arxiv' ? 'arXiv' : (paper.journal || paper.source),
      archiveID: paper.arxiv_id || ''});
  }
  return data;
}

export async function saveToZotero(env, paper, assignedKey) {
  if (!/^\d+$/.test(env.ZOTERO_LIBRARY_ID) || !['user', 'group'].includes(env.ZOTERO_LIBRARY_TYPE)
      || !KEY.test(env.ZOTERO_COLLECTION) || !KEY.test(assignedKey) || !env.ZOTERO_WRITE_KEY) {
    throw new Error('Zotero server configuration is incomplete');
  }
  const base = `https://api.zotero.org/${env.ZOTERO_LIBRARY_TYPE}s/${env.ZOTERO_LIBRARY_ID}`;
  const fetcher = env.ZOTERO_FETCH || fetch;
  const backoff = await env.DB.prepare("SELECT value FROM state WHERE key='cache:zotero_backoff'").bind().first();
  let nextAllowed = backoff ? Number(JSON.parse(backoff.value)) : 0;
  async function request(path, options = {}) {
    if (Date.now() < nextAllowed) throw new Error('Zotero requested backoff; retry later');
    const response = await fetcher(base + path, {...options, signal: AbortSignal.timeout(20000),
      headers: {'Zotero-API-Key': env.ZOTERO_WRITE_KEY, 'Zotero-API-Version': '3',
        'Content-Type': 'application/json', ...options.headers}});
    const delay = Number(response.headers.get('Backoff') || response.headers.get('Retry-After') || 0);
    if (delay > 0) {
      nextAllowed = Date.now() + delay * 1000;
      await env.DB.prepare("INSERT INTO state(key,value) VALUES('cache:zotero_backoff',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").bind(JSON.stringify(nextAllowed)).run();
    }
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('Zotero unavailable or write conflict');
    return {response, data: response.status === 204 ? null : await response.json()};
  }
  async function ensureCollection(item) {
    const tags = [...(item.data.tags || [])];
    for (const tag of paper.topics) if (!tags.some(t => t.tag === tag)) tags.push({tag});
    const collections = [...new Set([...(item.data.collections || []), env.ZOTERO_COLLECTION])];
    if (collections.length !== (item.data.collections || []).length || tags.length !== (item.data.tags || []).length) {
      await request('/items/' + item.key, {method: 'PATCH',
        headers: {'If-Unmodified-Since-Version': String(item.version)}, body: JSON.stringify({collections, tags})});
    }
    return item.key;
  }
  const existing = await request('/items/' + assignedKey);
  if (existing) return ensureCollection(existing.data);
  // Inspect the authoritative library immediately before creation, including items without abstracts.
  const wanted = new Set(paper.identifiers);
  for (const identifier of wanted) {
   let offset = 0;
   while (true) {
    const query = identifier.slice(identifier.indexOf(':') + 1);
    const result = await request(`/items/top?format=json&q=${encodeURIComponent(query)}&qmode=everything&limit=100&start=${offset}`);
    if (!result || !Array.isArray(result.data)) throw new Error('Cannot verify Zotero duplicates');
    for (const item of result.data) {
      if (identities(item.data).some(i => wanted.has(i))) return ensureCollection(item);
    }
    offset += result.data.length;
    if (offset >= Number(result.response.headers.get('Total-Results') || Infinity) || result.data.length < 100) break;
    if (result.response.headers.has('Backoff')) throw new Error('Zotero requested backoff; retry later');
    if (offset >= 1000) throw new Error('Ambiguous identifier search; narrow or review metadata');
   }
  }
  const payload = {...itemData(paper, env.ZOTERO_COLLECTION), key: assignedKey, version: 0};
  // version:0 is create-only. Retries must never overwrite a previously created item.
  const result = await request('/items', {method: 'POST', body: JSON.stringify([payload])});
  if (!result || result.data.failed?.['0'] || !(result.data.successful?.['0'] || result.data.success?.['0'] || result.data.unchanged?.['0'])) {
    const reconciled = await request('/items/' + assignedKey);
    if (reconciled) return ensureCollection(reconciled.data);
    throw new Error('Zotero item was not confirmed');
  }
  return assignedKey;
}
