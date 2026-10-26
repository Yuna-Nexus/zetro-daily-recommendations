// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker from '../index.js';

export function setup() {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  const db = {
    prepare(sql) {
      return {bind(...params) {
        const statement = sqlite.prepare(sql);
        return {
          first:async()=>statement.get(...params) || null,
          all:async()=>({results:statement.all(...params)}),
          run:async()=>({meta:{changes:Number(statement.run(...params).changes)}}),
        };
      }};
    },
    async batch(statements) {
      sqlite.exec('BEGIN');
      try {
        const result = [];
        for (const s of statements) result.push(await s.run());
        sqlite.exec('COMMIT');
        return result;
      } catch (error) { sqlite.exec('ROLLBACK'); throw error; }
    },
  };
  const items = new Map(), calls = [];
  let loseResponse = false, failWrite = false;
  const env = {DB:db, FEEDBACK_API_TOKEN:'a'.repeat(64), ZOTERO_LIBRARY_ID:'123', ZOTERO_LIBRARY_TYPE:'user',
    ZOTERO_COLLECTION:'ABCD2345', ZOTERO_WRITE_KEY:'private-key',
    ZOTERO_FETCH:async (url, options) => {
      calls.push({url, method:options.method || 'GET'});
      const path = new URL(url).pathname;
      if (options.method === 'POST') {
        if (failWrite) return Response.json({failed:{0:{code:400}}});
        const [data] = JSON.parse(options.body);
        assert.equal(data.version, 0);
        assert.equal(items.has(data.key), false, 'must not blindly create twice');
        items.set(data.key, {key:data.key,version:1,data});
        if (loseResponse) { loseResponse = false; throw new Error('response lost'); }
        return Response.json({successful:{0:items.get(data.key)}, failed:{}});
      }
      if (options.method === 'PATCH') {
        const key = path.split('/').at(-1);
        Object.assign(items.get(key).data, JSON.parse(options.body));
        return new Response(null, {status:204});
      }
      if (path.endsWith('/top')) return Response.json([...items.values()], {headers:{'Total-Results':String(items.size)}});
      const item = items.get(path.split('/').at(-1));
      return item ? Response.json(item) : new Response(null, {status:404});
    },
  };
  const request = (path, options={}) => worker.fetch(new Request('https://feedback.example' + path, options), env);
  const api = (action, values={}) => request('/api/state', {method:'POST',
    headers:{Authorization:'Bearer '+env.FEEDBACK_API_TOKEN, 'Content-Type':'application/json'}, body:JSON.stringify({action,...values})});
  const paper = {paper_id:'b'.repeat(64), title:'<script>bad</script>', source:'pubmed', authors:['A'], creators:[],
    abstract:'Original abstract', url:'https://pubmed.ncbi.nlm.nih.gov/123/', pmid:'123', doi:'10.1/test',
    topics:['测试主题'], identifiers:['pmid:123','doi:10.1/test'], journal_matches:[]};
  const issue = async() => {assert.equal((await api('ingest',{papers:[paper]})).status,200); return (await (await api('batch',{ids:[paper.paper_id]})).json());};
  const formFor = async url => {
    const page = await request(new URL(url).pathname + new URL(url).search);
    const text = await page.text();
    const cookie = page.headers.get('Set-Cookie').split(';')[0];
    return {page, text, cookie, token:new URL(url).searchParams.get('token'), csrf:text.match(/name="csrf" value="([^"]+)"/)?.[1]};
  };
  const post = f => request('/confirm',{method:'POST',headers:{Origin:'https://feedback.example',Cookie:f.cookie,'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({token:f.token,csrf:f.csrf})});
  return {env, sqlite, items, calls, request, api, paper, issue, formFor, post,
    lose:()=>loseResponse=true, fail:()=>failWrite=true};
}
