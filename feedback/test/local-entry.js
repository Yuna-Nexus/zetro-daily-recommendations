// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
// LOCAL SMOKE TEST ENTRY ONLY. wrangler.toml deploys ../index.js, never this file.
import worker from '../index.js';

const items = new Map();
async function fakeZotero(url, options) {
  const path = new URL(url).pathname;
  if (options.method === 'POST') {
    const [data] = JSON.parse(options.body);
    if (items.has(data.key)) return Response.json({failed:{0:{code:412}}});
    items.set(data.key,{key:data.key,version:1,data});
    return Response.json({successful:{0:items.get(data.key)}});
  }
  if (options.method === 'PATCH') {
    Object.assign(items.get(path.split('/').at(-1)).data, JSON.parse(options.body));
    return new Response(null,{status:204});
  }
  if (path.endsWith('/top')) return Response.json([...items.values()],{headers:{'Total-Results':String(items.size)}});
  return items.has(path.split('/').at(-1)) ? Response.json(items.get(path.split('/').at(-1))) : new Response(null,{status:404});
}
export default {
  fetch(request, env) {
    return worker.fetch(request,{...env,ZOTERO_LIBRARY_TYPE:'user',ZOTERO_LIBRARY_ID:'123',
      ZOTERO_COLLECTION:'ABCD2345',ZOTERO_WRITE_KEY:'local-test-only',ZOTERO_FETCH:fakeZotero});
  },
};
