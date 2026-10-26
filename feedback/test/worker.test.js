// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setup} from './harness.js';
import {itemData} from '../zotero.js';

test('source access is public, configurable and never writes Zotero',async()=>{
  const s=setup();
  s.env.SOURCE_URL='https://github.com/example/research-papers';
  const response=await s.request('/source');
  assert.equal(response.status,302);
  assert.equal(response.headers.get('Location'),s.env.SOURCE_URL);
  const page=await s.request('/confirm?token=invalid');
  const notice=await page.text();
  assert.match(notice,/href="\/source"/);
  assert.match(notice,/TideDra/);
  assert.match(notice,/© 2026 Yuna-Nexus/);
  assert.match(notice,/可按 AGPL-3.0 再分发/);
  assert.equal(s.calls.length,0);
  s.env.SOURCE_URL='javascript:alert(1)';
  assert.equal((await s.request('/source')).status,400);
});

test('preview seen lookup excludes issued aliases without changing decisions',async()=>{
  const s=setup();
  await s.api('ingest',{papers:[s.paper]});
  assert.deepEqual((await (await s.api('seen',{identifiers:s.paper.identifiers})).json()).identifiers,[]);
  await s.issue();
  const result=await (await s.api('seen',{identifiers:s.paper.identifiers})).json();
  assert.equal(result.identifiers.length,s.paper.identifiers.length);
  assert.equal(s.sqlite.prepare('SELECT decision FROM papers').get().decision,'pending');
  assert.equal(s.calls.length,0);
  assert.equal((await s.api('seen',{identifiers:[]})).status,400);
});

test('ingestion fits the D1 Free invocation budget and rejects oversized input before writes',async()=>{
  const s=setup();
  let calls=0, inBatch=false;
  const charge=()=>{if(!inBatch) assert.ok(++calls<=50,'D1 Free invocation limit exceeded');};
  const prepare=s.env.DB.prepare.bind(s.env.DB), batch=s.env.DB.batch.bind(s.env.DB);
  s.env.DB.prepare=sql=>({bind(...params){
    const statement=prepare(sql).bind(...params);
    return Object.fromEntries(['first','all','run'].map(method=>[method,async()=>{
      charge();return statement[method]();
    }]));
  }});
  s.env.DB.batch=async statements=>{
    charge();inBatch=true;
    try{return await batch(statements);}finally{inBatch=false;}
  };
  const papers=Array.from({length:11},(_,i)=>({...s.paper,paper_id:i.toString(16).padStart(64,'0'),
    identifiers:['pmid:'+String(1000+i),'doi:10.1/'+i]}));
  assert.equal((await s.api('ingest',{papers})).status,400);
  assert.equal(calls,0);
  assert.equal((await s.api('ingest',{papers:papers.slice(0,10)})).status,200);
  assert.equal(s.sqlite.prepare('SELECT COUNT(*) AS n FROM papers').get().n,10);
  assert.equal(calls,30);
  calls=0;
  assert.equal((await s.api('ingest',{papers:papers.slice(0,10)})).status,200);
  assert.equal(s.sqlite.prepare('SELECT COUNT(*) AS n FROM papers').get().n,10);
});

test('GET/HEAD are read-only, escaped, no secret, and require POST with CSRF', async()=>{
  const s=setup(), batch=await s.issue(), link=batch.links[0].save;
  const f=await s.formFor(link);
  assert.match(f.text,/&lt;script&gt;/);
  assert.equal(f.page.headers.get('Referrer-Policy'),'strict-origin');
  assert.ok(!f.text.includes(s.env.ZOTERO_WRITE_KEY));
  await s.request(new URL(link).pathname+new URL(link).search,{method:'HEAD'});
  assert.equal(s.sqlite.prepare('SELECT decision FROM papers').get().decision,'pending');
  assert.equal(s.calls.length,0);
  assert.equal((await s.request('/confirm',{method:'POST',body:new URLSearchParams({token:f.token})})).status,403);
  assert.equal((await s.post({...f,csrf:'invalid'})).status,403);
  for(const origin of ['null','https://other.example']) {
    const response=await s.request('/confirm',{method:'POST',headers:{Origin:origin,Cookie:f.cookie},
      body:new URLSearchParams({token:f.token,csrf:f.csrf})});
    assert.equal(response.status,403);
  }
  assert.equal(s.calls.length,0);
  assert.equal((await s.post(f)).status,200);
  assert.equal(s.items.size,1);
  assert.equal((await s.post(f)).status,200);
  assert.equal(s.calls.filter(c=>c.method==='POST').length,1);
  assert.equal(s.sqlite.prepare('SELECT decision FROM papers').get().decision,'saved');
});

test('lost write response retries the persisted key and never duplicates', async()=>{
  const s=setup(), batch=await s.issue(), f=await s.formFor(batch.links[0].save);
  s.lose();
  assert.equal((await s.post(f)).status,503);
  assert.equal(s.items.size,1);
  const key=s.sqlite.prepare('SELECT zotero_key FROM papers').get().zotero_key;
  assert.equal((await s.post(f)).status,200);
  assert.equal(s.items.size,1);
  assert.equal(s.sqlite.prepare('SELECT zotero_key FROM papers').get().zotero_key,key);
});

test('opening another confirmation tab does not invalidate the first form',async()=>{
  const s=setup(),batch=await s.issue();
  const first=await s.formFor(batch.links[0].save);
  const reject=new URL(batch.links[0].reject);
  const second=await s.request(reject.pathname+reject.search,{headers:{Cookie:first.cookie}});
  assert.equal(second.headers.get('Set-Cookie').split(';')[0],first.cookie);
  const secondText=await second.text();
  assert.notEqual(secondText.match(/name="csrf" value="([^"]+)"/)[1],first.csrf);
  assert.equal((await s.post(first)).status,200);
  assert.equal(s.items.size,1);
});

test('negative feedback, expiry and recovery never implicitly save', async()=>{
  const s=setup(), batch=await s.issue();
  assert.equal((await s.post(await s.formFor(batch.links[0].reject))).status,200);
  assert.equal(s.calls.length,0);
  assert.equal(s.sqlite.prepare('SELECT decision FROM papers').get().decision,'rejected');
  await s.api('ingest',{papers:[s.paper]});
  assert.equal((await (await s.api('pending')).json()).papers.length,0);
  s.sqlite.exec('UPDATE tokens SET expires=0');
  assert.equal((await s.request(new URL(batch.links[0].save).pathname+new URL(batch.links[0].save).search)).status,410);
});

test('existing item gains Collection and topic without metadata replacement',async()=>{
  const s=setup();
  s.items.set('ZZZZ2345',{key:'ZZZZ2345',version:8,data:{title:'User edited title',DOI:s.paper.doi,collections:[],tags:[{tag:'mine'}]}});
  const b=await s.issue();
  assert.equal((await s.post(await s.formFor(b.links[0].save))).status,200);
  assert.equal(s.items.size,1);
  assert.equal(s.items.get('ZZZZ2345').data.title,'User edited title');
  assert.deepEqual(s.items.get('ZZZZ2345').data.collections,['ABCD2345']);
  assert.equal(s.items.get('ZZZZ2345').data.tags.length,2);
});

test('200 with per-item failure is not a saved decision',async()=>{
  const s=setup(), b=await s.issue(); s.fail();
  assert.equal((await s.post(await s.formFor(b.links[0].save))).status,503);
  assert.equal(s.sqlite.prepare('SELECT decision FROM papers').get().decision,'save_failed');
});

test('authentication, stable alias dedup, reservation, manual delivery recovery',async()=>{
  const s=setup();
  assert.equal((await s.request('/api/state',{method:'POST',body:'{}'})).status,401);
  const b=await s.issue();
  assert.equal((await s.api('batch',{ids:[s.paper.paper_id]})).status,400);
  assert.equal((await s.api('delivery',{id:b.id,status:'sending'})).status,200);
  assert.equal((await (await s.api('pending')).json()).papers.length,0);
  assert.equal((await s.api('recover',{id:b.id})).status,400);
  await s.api('recover',{id:b.id,confirm_not_delivered:true});
  assert.equal((await (await s.api('pending')).json()).papers.length,1);
  assert.equal((await s.request(new URL(b.links[0].save).pathname+new URL(b.links[0].save).search)).status,410);
  await s.api('ingest',{papers:[{...s.paper,paper_id:'c'.repeat(64),identifiers:['pmid:123'],topics:['另一个主题']}]});
  const pending=(await (await s.api('pending')).json()).papers;
  assert.equal(pending.length,1);
  assert.equal(pending[0].topics.length,2);
});

test('concurrent save/reject cannot create then reject an item',async()=>{
  const s=setup(), b=await s.issue();
  const f=await s.formFor(b.links[0].save), r=await s.formFor(b.links[0].reject);
  await Promise.all([s.post(f),s.post(r)]);
  const decision=s.sqlite.prepare('SELECT decision FROM papers').get().decision;
  assert.ok(['saved','rejected'].includes(decision));
  assert.equal(s.items.size,decision==='saved'?1:0);
});

test('metadata mapping keeps original abstract, structured names and omits attachments',()=>{
  const data=itemData({source:'arxiv',title:'T',authors:['A'],creators:[{creatorType:'author',name:'A'}],abstract:'English',topics:['AI'],arxiv_id:'1234.56789',url:'https://arxiv.org/abs/1234.56789'},'ABCD2345');
  assert.equal(data.itemType,'preprint');
  assert.equal(data.abstractNote,'English');
  assert.equal(data.archiveID,'1234.56789');
  assert.equal(data.attachments,undefined);
});

test('Zotero backoff survives the next user retry',async()=>{
  const s=setup(), b=await s.issue(), f=await s.formFor(b.links[0].save);
  let calls=0;
  s.env.ZOTERO_FETCH=async()=>{
    calls++;
    return new Response(null,{status:429,headers:{'Retry-After':'60'}});
  };
  assert.equal((await s.post(f)).status,503);
  assert.equal((await s.post(f)).status,503);
  assert.equal(calls,1);
});
