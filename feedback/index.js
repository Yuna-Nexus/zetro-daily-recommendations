// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
import {newItemKey, saveToZotero} from './zotero.js';

const encoder = new TextEncoder();
const now = () => Math.floor(Date.now() / 1000);
export const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
async function hash(s) { return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(s))), b => b.toString(16).padStart(2, '0')).join(''); }
function randomToken() { return Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join(''); }
async function same(a, b) { return (await hash(a)) === (await hash(b)); }
async function csrf(secret, value) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
  return Array.from(new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value))), b => b.toString(16).padStart(2, '0')).join('');
}
// no-referrer turns a browser form POST's Origin into null; strict-origin also hides token URLs.
const headers = {'Cache-Control':'no-store', 'Referrer-Policy':'strict-origin', 'X-Content-Type-Options':'nosniff',
  'Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"};
function json(value, status = 200) { return Response.json(value, {status, headers}); }
function html(body, status = 200, extra = {}) {
  return new Response('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>文献确认</title><body>' + body + '<hr><small>zetro-daily-recommendations · <a href="/source">源码（AGPL-3.0）</a> · <a href="https://github.com/Yuna-Nexus/zetro-daily-recommendations/blob/main/LICENSE">许可证</a> · 不提供担保</small></body></html>',
    {status, headers:{...headers, 'Content-Type':'text/html; charset=utf-8', ...extra}});
}
function statement(env, sql, ...args) { return env.DB.prepare(sql).bind(...args); }
function check(condition, message) { if (!condition) throw new Error(message); }
async function readBody(request, limit) {
  const reader = request.body?.getReader();
  if (!reader) return '';
  const chunks = [];
  let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); throw new Error('Request too large'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}

async function machine(request, env) {
  check(env.FEEDBACK_API_TOKEN?.length >= 32, 'Missing API secret');
  if (!await same(request.headers.get('Authorization') || '', 'Bearer ' + env.FEEDBACK_API_TOKEN)) return json({error:'Unauthorized'}, 401);
  check(Number(request.headers.get('Content-Length') || 0) <= 2000000, 'Request too large');
  const raw = await readBody(request, 2000000);
  check(raw.length <= 2000000, 'Request too large');
  const data = JSON.parse(raw);
  if (data.action === 'get') {
    check(typeof data.key === 'string' && /^(checkpoint:|cache:)/.test(data.key), 'Invalid state key');
    const row = await statement(env, 'SELECT value FROM state WHERE key=?', data.key).first();
    return json({value:row ? JSON.parse(row.value) : null});
  }
  if (data.action === 'set') {
    check(typeof data.key === 'string' && /^(checkpoint:|cache:)/.test(data.key), 'Invalid state key');
    await statement(env, 'INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', data.key, JSON.stringify(data.value)).run();
    return json({ok:true});
  }
  if (data.action === 'seen') {
    check(Array.isArray(data.identifiers) && data.identifiers.length > 0 && data.identifiers.length <= 80 &&
      data.identifiers.every(i => typeof i === 'string'), 'Invalid identifier lookup');
    const placeholders = data.identifiers.map(() => '?').join(',');
    const rows = (await statement(env, `SELECT identifiers.identifier FROM identifiers JOIN papers ON papers.id=identifiers.paper_id WHERE identifiers.identifier IN (${placeholders}) AND (papers.decision!='pending' OR papers.batch_id IS NOT NULL)`, ...data.identifiers).all()).results;
    return json({identifiers: rows.map(r => r.identifier)});
  }
  if (data.action === 'ingest') {
    check(Array.isArray(data.papers) && data.papers.length <= 10, 'Ingest at most 10 papers');
    for (const paper of data.papers) {
      check(/^[a-f0-9]{64}$/.test(paper.paper_id) && paper.title && Array.isArray(paper.identifiers) && paper.identifiers.length > 0, 'Invalid paper');
      check(Array.isArray(paper.topics) && paper.topics.every(t => typeof t === 'string'), 'Invalid topics');
      const placeholders = paper.identifiers.map(() => '?').join(',');
      const roots = (await statement(env, `SELECT DISTINCT paper_id FROM identifiers WHERE identifier IN (${placeholders})`, ...paper.identifiers).all()).results;
      // A late identity bridge across already-issued records needs operator review, never silent reparenting.
      check(roots.length <= 1, 'Identity bridge conflict; review records before retry');
      const id = roots[0]?.paper_id || paper.paper_id;
      const previous = await statement(env, 'SELECT payload FROM papers WHERE id=?', id).first();
      if (previous) {
        const old = JSON.parse(previous.payload);
        paper.topics = [...new Set([...old.topics, ...paper.topics])];
        paper.journal_matches = [...new Set([...(old.journal_matches || []), ...(paper.journal_matches || [])])];
        for (const field of ['doi','pmid','arxiv_id']) if (!paper[field]) paper[field] = old[field];
        paper.identifiers = [...new Set([...old.identifiers, ...paper.identifiers])];
        const priority = {pubmed:3,journal:2};
        if ((priority[old.source] || 1) > (priority[paper.source] || 1)) {
          for (const field of ['source','item_type','title','authors','creators','abstract','url','published','journal','issn','volume','issue','pages']) {
            if (old[field]) paper[field] = old[field];
          }
        }
      }
      paper.paper_id = id;
      await env.DB.batch([
        statement(env, 'INSERT INTO papers(id,payload,created) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', id, JSON.stringify(paper), now()),
        ...paper.identifiers.map(i => statement(env, 'INSERT INTO identifiers(identifier,paper_id) VALUES(?,?) ON CONFLICT(identifier) DO NOTHING', i, id)),
      ]);
    }
    return json({ok:true});
  }
  if (data.action === 'pending') {
    const rows = (await statement(env, "SELECT id,payload FROM papers WHERE decision='pending' AND batch_id IS NULL AND id>? ORDER BY id LIMIT 100", data.after || '').all()).results;
    return json({papers:rows.map(r => JSON.parse(r.payload)), after:rows.at(-1)?.id || ''});
  }
  if (data.action === 'batch') {
    check(Array.isArray(data.ids) && data.ids.length > 0 && data.ids.length <= 100 && new Set(data.ids).size === data.ids.length, 'Invalid batch');
    const id = crypto.randomUUID(), expires = now() + 90 * 86400;
    const links = [], statements = [statement(env, 'INSERT INTO batches(id,created) VALUES(?,?)', id, now())];
    for (const paperId of data.ids) {
      const paper = await statement(env, 'SELECT decision,batch_id FROM papers WHERE id=?', paperId).first();
      check(paper?.decision === 'pending' && paper.batch_id === null, 'Paper already handled or reserved');
      statements.push(statement(env, 'UPDATE papers SET batch_id=? WHERE id=?', id, paperId));
      const link = {paper_id:paperId};
      for (const action of ['save','reject']) {
        const token = randomToken();
        link[action] = new URL('/confirm?token=' + token, request.url).href;
        statements.push(statement(env, 'INSERT INTO tokens(hash,paper_id,action,expires) VALUES(?,?,?,?)', await hash(token), paperId, action, expires));
      }
      links.push(link);
    }
    // Production Actions are serialized. A unique active-batch claim also prevents duplicate links.
    await env.DB.batch(statements);
    return json({id, links});
  }
  if (data.action === 'delivery') {
    check(['sending','sent'].includes(data.status), 'Invalid delivery state');
    const from = data.status === 'sending' ? 'prepared' : 'sending';
    const result = await statement(env, 'UPDATE batches SET status=? WHERE id=? AND status=?', data.status, data.id, from).run();
    check(result.meta.changes === 1, 'Invalid batch transition');
    return json({ok:true});
  }
  if (data.action === 'batches') return json({batches:(await statement(env, "SELECT * FROM batches WHERE status IN ('prepared','sending') ORDER BY created").all()).results});
  if (data.action === 'recover') {
    check(data.confirm_not_delivered === true, 'Confirm delivery status before recovery');
    await env.DB.batch([
      statement(env, 'UPDATE tokens SET revoked=1 WHERE paper_id IN (SELECT id FROM papers WHERE batch_id=?)', data.id),
      statement(env, 'UPDATE papers SET batch_id=NULL WHERE batch_id=?', data.id),
      statement(env, "UPDATE batches SET status='released' WHERE id=?", data.id),
    ]);
    return json({ok:true});
  }
  return json({error:'Unknown action'}, 400);
}

async function confirm(request, env) {
  const url = new URL(request.url);
  if (!['GET','HEAD','POST'].includes(request.method)) return html('不支持该请求。', 405);
  let form;
  if (request.method === 'POST') {
    if (request.headers.get('Origin') !== url.origin) return html('请求来源无效。', 403);
    if (Number(request.headers.get('Content-Length') || 0) > 4096) return html('请求过大。', 413);
    const body = await readBody(request, 4096);
    if (body.length > 4096) return html('请求过大。', 413);
    form = new URLSearchParams(body);
  }
  const token = form?.get('token') || url.searchParams.get('token') || '';
  if (!/^[a-f0-9]{64}$/.test(token)) return html('链接无效。', 400);
  const tokenHash = await hash(token);
  const row = await statement(env, `SELECT t.*,p.payload,p.decision,p.zotero_key,p.lease_until FROM tokens t JOIN papers p ON p.id=t.paper_id WHERE t.hash=?`, tokenHash).first();
  if (!row || row.revoked || row.expires < now()) return html('链接无效或已过期，文献未被自动处理。', 410);
  const paper = JSON.parse(row.payload);
  const cookieNonce = request.headers.get('Cookie')?.match(/(?:^|;\s*)confirm_nonce=([a-f0-9]{64})(?:;|$)/)?.[1];
  if (request.method === 'HEAD') return new Response(null, {headers});
  if (['saved','rejected'].includes(row.decision)) return html(row.decision === 'saved' ? '已收藏，无需重复操作。' : '已记录不感兴趣。');
  if (request.method === 'GET') {
    const nonce = cookieNonce || randomToken();
    const sig = await csrf(env.FEEDBACK_API_TOKEN, tokenHash + ':' + nonce);
    return html(`<h1>${row.action === 'save' ? '确认保存到 Zotero' : '确认不感兴趣'}</h1><p>${escapeHtml(paper.title)}</p><p>Collection：${escapeHtml(env.ZOTERO_COLLECTION || '尚未配置')}</p><p>关闭页面会保持未处理状态。</p><form method="post" action="/confirm"><input type="hidden" name="token" value="${token}"><input type="hidden" name="csrf" value="${sig}"><button type="submit">${row.action === 'save' ? '确认收藏' : '确认不感兴趣'}</button></form>`, 200,
      {'Set-Cookie':`confirm_nonce=${nonce}; Secure; HttpOnly; SameSite=Strict; Path=/confirm; Max-Age=1800`});
  }
  const nonce = cookieNonce;
  if (!nonce || !await same(form.get('csrf') || '', await csrf(env.FEEDBACK_API_TOKEN, tokenHash + ':' + nonce))) return html('确认信息无效，请重新打开邮件链接。', 403);
  if (row.action === 'reject') {
    const result = await statement(env, "UPDATE papers SET decision='rejected' WHERE id=? AND decision='pending'", row.paper_id).run();
    return html(result.meta.changes ? '已记录不感兴趣。' : '该文献正在保存或已经处理。');
  }
  const assignedKey = row.zotero_key || newItemKey();
  const claim = await statement(env, "UPDATE papers SET decision='saving', zotero_key=COALESCE(zotero_key,?),lease_until=? WHERE id=? AND (decision IN ('pending','save_failed') OR (decision='saving' AND lease_until<?))", assignedKey, now()+120, row.paper_id, now()).run();
  if (!claim.meta.changes) return html('正在保存，请稍后重新打开此链接重试。', 409);
  try {
    const key = await saveToZotero(env, paper, assignedKey);
    await statement(env, "UPDATE papers SET decision='saved', zotero_key=?,lease_until=0 WHERE id=?", key, row.paper_id).run();
    return html('已保存到 Zotero。');
  } catch {
    await statement(env, "UPDATE papers SET decision='save_failed',lease_until=0 WHERE id=?", row.paper_id).run();
    return html('尚未确认保存成功。请稍后重新打开本链接确认重试；系统会先核对已有条目。', 503);
  }
}

export default {
  async fetch(request, env) {
    try {
      const path = new URL(request.url).pathname;
      if (path === '/source' && ['GET','HEAD'].includes(request.method)) {
        const source = new URL(env.SOURCE_URL || 'https://github.com/Yuna-Nexus/zetro-daily-recommendations');
        check(source.protocol === 'https:', 'Source URL requires HTTPS');
        return new Response(null, {status:302, headers:{...headers, Location:source.href}});
      }
      check(env.FEEDBACK_API_TOKEN?.length >= 32, 'Missing server secret');
      if (path === '/api/state' && request.method === 'POST') return await machine(request, env);
      if (path === '/confirm') return await confirm(request, env);
      return html('未找到页面。', 404);
    } catch {
      // Never include upstream URLs, credentials, tokens or SQL diagnostics.
      return json({error:'Request failed; check configuration or retry'}, 400);
    }
  },
};
