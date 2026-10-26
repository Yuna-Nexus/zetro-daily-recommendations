// Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
// Low-memory local test bridge; production deployment never includes this entry point.
import {createServer} from 'node:http';
import worker from '../index.js';
import {setup} from './harness.js';
const {env}=setup();
const port=Number(process.env.LOCAL_TEST_PORT || 18787);
env.FEEDBACK_API_TOKEN='local-smoke-test-token-0123456789abcdef';
const server=createServer(async(req,res)=>{
  try {
    const body=[];
    for await (const chunk of req) body.push(chunk);
    if(req.method==='POST' && req.url==='/confirm') process.stdout.write(JSON.stringify({
      confirmationOrigin:req.headers.origin || null,refererContainsToken:(req.headers.referer || '').includes('token=')})+'\n');
    const request=new Request('http://127.0.0.1:'+port+req.url,{method:req.method,headers:req.headers,
      body:['GET','HEAD'].includes(req.method)?undefined:Buffer.concat(body)});
    const response=await worker.fetch(request,env);
    res.writeHead(response.status,Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {res.writeHead(500);res.end('Local test bridge error');}
});
server.listen(port,'127.0.0.1',()=>process.stdout.write('Local SQLite/Worker test bridge ready on '+port+'\n'));
