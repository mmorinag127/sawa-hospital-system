const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
function load(name,globals={}){const module={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/services',name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,{module,exports:module.exports,URL,URLSearchParams,Error,...globals,require:dep=>dep==='./apiClient'?{getStoredAuthHeader:()=>globals.authorization||''}:load(dep.replace('./',''),globals)});return module.exports;}
const {validateLoginDestination:valid,resolveLoginDestination:resolve,loginUrlFor}=load('loginDestination');
test('explicit next including review ID/date/query/hash wins over stale stored target',()=>{
 const target='/school-lunch/cooking-arrangements?job_id=8618aec17a1545399ffd105938b32408&mode=review#source';
 assert.equal(resolve('?'+new URLSearchParams({next:target}),'/hospital'),target);
 assert.equal(new URL(loginUrlFor(target),'https://sawa.invalid').searchParams.get('next'),target);
 assert.equal(resolve('',target),target);assert.equal(resolve('',null),'/');
});
for(const input of ['https://evil.invalid','//evil.invalid','/\\evil.invalid','javascript:alert(1)',' /school-lunch','/\n/evil.invalid','/%2f%2fevil.invalid','/%5cevil.invalid','/%252f%252fevil.invalid','/login','/logout','/auth/handoff','/school-lunch/auth/handoff','/api/portal/users','/school-lunch/api/backend/shared-auth/logout','/_next/test','/%61pi/test','/%'])test(`unsafe return rejected ${JSON.stringify(input)}`,()=>assert.equal(valid(input),null));
test('invalid explicit return never falls back to old stored destination',()=>{
 assert.throws(()=>resolve('?next=https://evil.invalid','/shift'),/復帰先/);assert.throws(()=>resolve('?next=/shift&next=/hospital',null),/復帰先/);
});
for(const target of ['/school-lunch/work-schedules?facility_id=8&service_date=2026-07-01','/shift/planning?week=2026-09-21','/hospital','/orders/ORD123?tab=review','/admin/users','/'])test(`internal work path retained ${target}`,()=>assert.equal(valid(target),target));
function scenario(status=200,identity={role:'operator'},authorization='Bearer synthetic-test'){
 const calls=[],navigation=[],alerts=[],stored=new Map();
 const api=load('systemNavigation',{authorization,fetch:async(url,options)=>{calls.push({url,options});if(status==='network')throw Error('internal secret');return {ok:status===200,status,json:async()=>identity};},window:{sessionStorage:{setItem:(k,v)=>stored.set(k,v)},location:{assign:value=>navigation.push(value)},alert:value=>alerts.push(value)}});
 return {api,calls,navigation,alerts,stored};
}
test('school session handshake is the sole target before original URL return',async()=>{
 const h=scenario(),target='/school-lunch/cooking-arrangements?job_id=reported&mode=review';
 assert.equal(await h.api.prepareSystemDestination(target,'Bearer synthetic-test'),target);
 assert.equal(h.calls[0].url,'/school-lunch/api/backend/shared-auth/me');assert.equal(h.calls[0].options.headers.Authorization,'Bearer synthetic-test');assert.equal(h.calls[0].options.redirect,'error');assert.equal(h.calls[0].options.credentials,'same-origin');
});
for(const [target,url]of [['/shift/planning?week=x','/api/portal/auth/me?system=shift'],['/hospital','/api/portal/auth/me?system=hospital'],['/','/api/portal/auth/me']])test(`other system registered user checked ${target}`,async()=>{const h=scenario();await h.api.prepareSystemDestination(target,'Bearer synthetic-test');assert.equal(h.calls[0].url,url);});
for(const status of [401,403,500,503,'network'])test(`failed handshake does not navigate ${status}`,async()=>{const h=scenario(status);await assert.rejects(h.api.prepareSystemDestination('/school-lunch/staff','Bearer synthetic-test'));assert.deepEqual(h.navigation,[]);});
for(const identity of [null,{}, {role:'viewer'},{role:'admin',auth_disabled:true}])test(`malformed or disabled identity rejected ${JSON.stringify(identity)}`,async()=>{const h=scenario(200,identity);await assert.rejects(h.api.prepareSystemDestination('/school-lunch','Bearer synthetic-test'));});
test('entry with no token remembers validated exact work location',async()=>{const h=scenario(200,{role:'operator'},'');const target='/school-lunch/route-diagrams?facility_id=8';await h.api.enterSchoolLunch({preventDefault(){}},target);assert.equal(h.calls.length,0);assert.equal(h.stored.get('auth_next'),target);assert.equal(new URL(h.navigation[0],'https://sawa.invalid').searchParams.get('next'),target);});
test('entry denied or network error stays in place with visible reason',async()=>{for(const status of [403,'network']){const h=scenario(status);await h.api.enterSchoolLunch({preventDefault(){}});assert.equal(h.navigation.length,0);assert.equal(h.alerts.length,1);assert.ok(!h.alerts[0].includes('secret'));}});
test('invalid destination never sends credentials',async()=>{const h=scenario();await assert.rejects(h.api.prepareSystemDestination('//evil.invalid','Bearer synthetic-test'));assert.equal(h.calls.length,0);});
