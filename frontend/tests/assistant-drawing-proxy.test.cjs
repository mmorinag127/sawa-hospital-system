const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
const helper = {exports: {}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(require('node:path').resolve(__dirname,'../src/services/portalProxySession.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports:helper.exports,Headers,URL});
test('authenticated drawing proxy preserves question identity and source response protections',async()=>{
 const source=fs.readFileSync(require('node:path').resolve(__dirname,'../src/pages/api/assistant-trial/[[...path]].ts'),'utf8');const exports={},seen=[];
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(code,{exports,require:()=>helper.exports,Headers,process:{env:{SAWA_ASSISTANT_TRIAL_ENABLED:'true',SAWA_TRIAL_WEB_TOKEN:'synthetic-web-token',API_PROXY_TARGET:'http://127.0.0.1:9000',SAWA_TRIAL_OPERATOR_ACCOUNT:'synthetic-operator'}},Buffer,URL,URLSearchParams,AbortSignal,fetch:async(url)=>{seen.push(url);return seen.length===1?new Response(JSON.stringify({account:'synthetic-operator'})):new Response('<svg/>',{headers:{'content-type':'image/svg+xml','cache-control':'no-store','x-content-type-options':'nosniff','content-security-policy':"sandbox; default-src 'none'"}});}});
 const headers={},res={status(n){this.code=n;return this},setHeader(k,v){headers[k]=v},send(v){this.body=v},json(v){this.body=v},end(){}};
 await exports.default({method:'GET',headers:{authorization:'Bearer synthetic-user-token'},query:{path:['runs','r1','question-drawings','a'.repeat(64)],question_id:'question-3'},async *[Symbol.asyncIterator](){}},res);
 assert.equal(res.code,200);assert.equal(new URL(seen[1]).searchParams.get('question_id'),'question-3');assert.equal(res.body.toString(),'<svg/>');assert.equal(headers['cache-control'],'no-store');assert.equal(headers['x-content-type-options'],'nosniff');assert.match(headers['content-security-policy'],/sandbox/);
});

test('business mutations and artifact reads share a deadline while authentication stays bounded',async()=>{
 for(const [method,segments,expected] of [
  ['POST',['runs','r1','stop'],270000],['POST',['runs','r1','rollback'],270000],['POST',['runs','r1','refresh-usage'],270000],
  ['POST',['runs','r1','reply'],270000],['POST',['runs','r1','decision'],270000],['POST',['runs','r1','faxes','f1','decision'],270000],
  ['POST',['runs'],60000],['POST',['tasks','task1','targets'],60000],['PUT',['budget'],60000],['PUT',['provider'],60000],
  ['GET',['runs','r1','artifacts','a1'],270000],['GET',['runs','r1','stop'],60000],['GET',['runs','r1'],60000]
 ]){
  const exports={},timeouts=[],seen=[];
  const source=fs.readFileSync(require('node:path').resolve(__dirname,'../src/pages/api/assistant-trial/[[...path]].ts'),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code,{exports,require:()=>helper.exports,Headers,process:{env:{SAWA_ASSISTANT_TRIAL_ENABLED:'true',SAWA_TRIAL_WEB_TOKEN:'synthetic-web-token',API_PROXY_TARGET:'http://127.0.0.1:9000',SAWA_TRIAL_OPERATOR_ACCOUNT:'synthetic-operator'}},Buffer,URL,URLSearchParams,AbortSignal:{timeout(ms){timeouts.push(ms);return undefined}},fetch:async(url)=>{seen.push(url);return seen.length===1?new Response(JSON.stringify({account:'synthetic-operator'})):new Response('{}',{headers:{'content-type':'application/json'}});}});
  const res={status(n){this.code=n;return this},setHeader(){},send(){},json(){},end(){}};
  await exports.default({method,headers:{authorization:'Bearer synthetic-user-token'},query:{path:segments},async *[Symbol.asyncIterator](){}},res);
  assert.equal(res.code,200);assert.deepEqual(timeouts,[10000,expected]);
 }
});

for (const mode of ['cookie', 'bad-origin', 'missing-csrf', 'wrong-operator', 'revoked']) test('assistant cookie authentication ' + mode, async () => {
 const exports={},seen=[],origin='https://web-stg-avlnzjjrca-dt.a.run.app',token='ps_'+'a'.repeat(43);
 const source=fs.readFileSync(require('node:path').resolve(__dirname,'../src/pages/api/assistant-trial/[[...path]].ts'),'utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
  exports,require:()=>helper.exports,Headers,Buffer,URL,URLSearchParams,AbortSignal,
  process:{env:{SAWA_ASSISTANT_TRIAL_ENABLED:'true',SAWA_TRIAL_WEB_TOKEN:'server-only',API_PROXY_TARGET:'https://worker.invalid',SAWA_TRIAL_OPERATOR_ACCOUNT:'operator',PORTAL_PUBLIC_ORIGIN:origin}},
  fetch:async(url,options)=>{seen.push({url,options});return seen.length===1?new Response(JSON.stringify({account:mode==='wrong-operator'?'other':'operator'}),{status:mode==='revoked'?401:200}):new Response('safe-output');},
 });
 const res={headers:{},status(code){this.code=code;return this;},setHeader(key,value){this.headers[key]=value;},json(body){this.body=body;},send(body){this.body=body;},end(){}};
 await exports.default({method:'POST',cookies:{'__Host-sawa_session':token,'__Host-sawa_csrf':'csrf'},headers:{origin:mode==='bad-origin'?'https://evil.invalid':origin,...(mode==='missing-csrf'?{}:{'x-sawa-csrf':'csrf'})},query:{path:['runs']},async *[Symbol.asyncIterator](){}},res);
 assert.equal(res.code,mode==='cookie'?200:mode==='revoked'?401:403);
 assert.equal(seen.length,mode==='cookie'?2:['bad-origin','missing-csrf'].includes(mode)?0:1);
 if(mode==='cookie')for(const call of seen)assert.equal(call.options.headers.Authorization,'Bearer '+token);
 assert.ok(!JSON.stringify(res).includes(token)); assert.ok(!JSON.stringify(res).includes('server-only'));
});
