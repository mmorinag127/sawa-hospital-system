const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript');
const root = path.join(__dirname, '../src');
function compile(input, bindings = {}) {
  const exports = {};
  const code = ts.transpileModule(input, {compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS}}).outputText;
  vm.compileFunction(code, ['exports', ...Object.keys(bindings)])(exports, ...Object.values(bindings));
  return exports;
}
const helpers = compile(fs.readFileSync(path.join(root, 'services/orderFacilityRead.ts'), 'utf8'));
const source = fs.readFileSync(path.join(root, 'pages/orders/[id]/workflow-v2.tsx'), 'utf8');
const ast = ts.createSourceFile('page.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let expression;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'loadFacilityTemplateStatus') expression = node.initializer.getText(ast);
  ts.forEachChild(node, visit);
}
visit(ast);
const saved = {facility_id:'F',week_start:'2026-10-25',week_end:'2026-10-31',template_version_id:'exact'};
test('saved version and explicit pending week use distinct canonical contracts', () => {
  const own = helpers.facilityReadSelection('O','F',saved.week_start,saved.week_end,saved,true);
  assert.equal(own.url, '/orders/O/workflow-v2/facility-config');
  const pending = helpers.facilityReadSelection('O','F','2026-11-01','2026-11-07',saved,true);
  assert.equal(pending.url, '/facilities/F/resolved?effective_date=2026-11-01');
  assert.throws(() => helpers.facilityReadSelection('O','F',saved.week_start,saved.week_end,{...saved,template_version_id:null},true));
  assert.throws(() => helpers.facilityReadConfig(own, {source:'canonical_json',facility_id:'F',resolved_config:{}}));
  assert.throws(() => helpers.facilityReadConfig(own, {source:'canonical_json',facility_id:'F',order_id:'O',...saved,template_version_id:'replacement',config:{}}));
  for(const change of [{facility_id:'OTHER'},{effective_date:'2026-10-25'},{version:null},{config:null}]) {
    assert.throws(() => helpers.facilityReadConfig(pending, {source:'canonical_json',facility_id:'F',effective_date:'2026-11-01',version:{version_id:'next'},config:{},...change}));
  }
  assert.match(source, /\}, \[facilityConfigReadKey\]\)/);
  assert.match(source, /facilityConfigLoadedKey === facilityConfigReadKey/);
});
function harness({dirty=false, pending=false} = {}) {
  const state = {draft:['operator-edit'],status:{}};
  const context = {...helpers, orderId:'O', workflow:saved, workflowContextConfirmed:true,
    effectiveWeekStart:pending?'2026-11-01':saved.week_start,effectiveWeekEnd:pending?'2026-11-07':saved.week_end,
    facilityConfigReadKey:'key',facilityConfigReadKeyRef:{current:'key'},facilityReadGenerationRef:{current:0},quadAliveRef:{current:true},
    capturePageOwner:()=>1,ownsPageInput:()=>true,canAdoptReadEditorInput:()=>!dirty,
    normalizeFacilityTemplateColumns:x=>x,formatApiError:e=>e.message,
    apiClient:{get:async url=>{state.url=url;return {data:pending?{source:'canonical_json',facility_id:'F',effective_date:'2026-11-01',version:{version_id:'next'},config:{fax_template:{columns:['new']}}}:{source:'canonical_json',order_id:'O',...saved,config:{fax_template:{columns:['saved']}}}};}}};
  for (const [setter, key] of Object.entries({setFacilityTemplateStatus:'status',setFacilityResolvedConfig:'config',setFacilityConfigLoadedKey:'loaded',setFacilityTemplateColumns:'columns',setFacilityTemplateColumnDraft:'draft',setFacilityTemplateSwapLeft:'left',setFacilityTemplateSwapRight:'right',setFacilityTemplateMessage:'message'})) context[setter]=value=>{state[key]=typeof value==='function'?value(state[key]):value;};
  return {state,context,run:compile(`exports.run=${expression}`,context).run};
}
for(const pending of [false,true]) test(`actual loader adopts ${pending?'new week':'saved version'} config without overwriting dirty columns`,async()=>{
  const h=harness({dirty:true,pending});await h.run('F');
  assert.deepEqual(h.state.draft,['operator-edit']);
  assert.deepEqual(h.state.columns,[pending?'new':'saved']);
  assert.equal(h.state.loaded,'key');
});
for(const failed of [false,true]) test(`actual loader rejects stale week ${failed?'error':'success'}`,async()=>{
  const h=harness();let resolve,reject;
  h.context.apiClient.get=()=>new Promise((a,b)=>{resolve=a;reject=b;});
  const request=h.run('F');h.context.facilityConfigReadKeyRef.current='new-week';
  if(failed)reject(Error('old error'));else resolve({data:{source:'canonical_json',order_id:'O',...saved,config:{}}});
  await request;assert.equal(h.state.config,undefined);assert.equal(h.state.loaded,undefined);
  assert.deepEqual(h.state.draft,['operator-edit']);assert.equal(h.state.status.error,'');
  assert.equal(h.state.status.loading,true);
});

test('same facility different week reads again and rejects the first response after the second succeeds',async()=>{
  const h=harness();let resolve;
  h.context.apiClient.get=()=>new Promise(r=>{resolve=r;});
  const old=h.run('F');
  h.context.facilityConfigReadKey='new-week';h.context.facilityConfigReadKeyRef.current='new-week';
  h.context.effectiveWeekStart='2026-11-01';h.context.effectiveWeekEnd='2026-11-07';
  h.context.apiClient.get=async url=>{
    assert.equal(url,'/facilities/F/resolved?effective_date=2026-11-01');
    return {data:{source:'canonical_json',facility_id:'F',effective_date:'2026-11-01',version:{version_id:'next'},config:{fax_template:{columns:['next']}}}};
  };
  await compile(`exports.run=${expression}`,h.context).run('F');
  resolve({data:{source:'canonical_json',order_id:'O',...saved,config:{fax_template:{columns:['old']}}}});
  await old;assert.deepEqual(h.state.columns,['next']);assert.equal(h.state.loaded,'new-week');
  assert.equal(h.state.status.loading,false);
});
