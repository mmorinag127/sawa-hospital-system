const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
test('authenticated drawing proxy preserves question identity and source response protections',async()=>{
 const source=fs.readFileSync(require('node:path').resolve(__dirname,'../src/pages/api/assistant-trial/[[...path]].ts'),'utf8');const exports={},seen=[];
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(code,{exports,process:{env:{SAWA_ASSISTANT_TRIAL_ENABLED:'true',SAWA_TRIAL_WEB_TOKEN:'synthetic-web-token',API_PROXY_TARGET:'http://127.0.0.1:9000',SAWA_TRIAL_OPERATOR_ACCOUNT:'synthetic-operator'}},Buffer,URL,URLSearchParams,AbortSignal,fetch:async(url)=>{seen.push(url);return seen.length===1?new Response(JSON.stringify({account:'synthetic-operator'})):new Response('<svg/>',{headers:{'content-type':'image/svg+xml','cache-control':'no-store','x-content-type-options':'nosniff','content-security-policy':"sandbox; default-src 'none'"}});}});
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
  vm.runInNewContext(code,{exports,process:{env:{SAWA_ASSISTANT_TRIAL_ENABLED:'true',SAWA_TRIAL_WEB_TOKEN:'synthetic-web-token',API_PROXY_TARGET:'http://127.0.0.1:9000',SAWA_TRIAL_OPERATOR_ACCOUNT:'synthetic-operator'}},Buffer,URL,URLSearchParams,AbortSignal:{timeout(ms){timeouts.push(ms);return undefined}},fetch:async(url)=>{seen.push(url);return seen.length===1?new Response(JSON.stringify({account:'synthetic-operator'})):new Response('{}',{headers:{'content-type':'application/json'}});}});
  const res={status(n){this.code=n;return this},setHeader(){},send(){},json(){},end(){}};
  await exports.default({method,headers:{authorization:'Bearer synthetic-user-token'},query:{path:segments},async *[Symbol.asyncIterator](){}},res);
  assert.equal(res.code,200);assert.deepEqual(timeouts,[10000,expected]);
 }
});
