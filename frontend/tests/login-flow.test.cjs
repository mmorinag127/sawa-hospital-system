const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {resolve,reject,promise};};
function page({search='?next=%2Fschool-lunch%2Fcooking-arrangements%3Fjob_id%3Dreported%26mode%3Dreview',stored='/hospital',status=200,identity={role:'operator'},pending}={}){
 const calls=[],states=[],refs=[],effects=[],cleanups=[],storage=new Map([['auth_next',stored]]);let si=0,ri=0,callback,onLogout,tree;
 const window={location:{search,origin:'https://portal.invalid',replace:target=>calls.push(['navigate',target])},sessionStorage:{getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},google:{accounts:{id:{initialize:config=>callback=config.callback,renderButton(){}}}}};
 const react={useState:initial=>{const i=si++;if(!(i in states))states[i]=initial;return [states[i],v=>states[i]=v];},useRef:initial=>{const i=ri++;return refs[i]||(refs[i]={current:initial});},useEffect:fn=>effects.push(fn)};
 const jsx=(type,props)=>({type,props:props||{}});
 function load(relative){const module={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src',relative),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,{module,exports:module.exports,window,URL,URLSearchParams,AbortController,Error,process:{env:{NEXT_PUBLIC_GOOGLE_CLIENT_ID:'synthetic-client'}},fetch:async(url,opts)=>{if(url==='/api/auth/config')return {ok:true,json:async()=>({google_client_id:'synthetic-client'})};calls.push(['request',url,opts]);return pending?pending.promise:{ok:status===200,status,json:async()=>identity};},require:name=>{
  if(name==='react')return react;if(name==='react/jsx-runtime')return {jsx,jsxs:jsx};
  if(name.endsWith('/apiClient')||name==='./apiClient')return {setBearerToken:token=>calls.push(['store',token]),getStoredAuthHeader:()=>''};
  if(name.endsWith('/browserSession'))return {watchBrowserLogout:fn=>{onLogout=fn;return ()=>onLogout=null;}};
  if(name.endsWith('/loginDestination')||name==='./loginDestination')return load('services/loginDestination.ts');
  if(name.endsWith('/systemNavigation'))return load('services/systemNavigation.ts');
  return {default:name};
 }});return module.exports;}
 const Component=load('pages/login.tsx').default;
 function visit(n,fn){if(!n||typeof n!=='object')return;if(Array.isArray(n))return n.forEach(x=>visit(x,fn));fn(n);visit(n.props?.children,fn);}
 function render(){si=0;ri=0;effects.length=0;tree=Component();visit(tree,n=>{if(n.props.ref)n.props.ref.current={};});return tree;}
 render();for(const fn of effects){const cleanup=fn();if(cleanup)cleanups.push(cleanup);}
 return {calls,storage,states,login:()=>callback({credential:'synthetic-google-token'}),logout:()=>onLogout?.(),unmount:()=>cleanups.forEach(fn=>fn()),alerts:()=>{render();const out=[];visit(tree,n=>{if(n.props.role==='alert')out.push(n.props.children);});return out;}};
}
test('actual Google callback establishes session before storing and returning to exact review',async()=>{const h=page();h.login();await flush();assert.deepEqual(h.calls.map(c=>c[0]),['request','store','navigate']);assert.equal(h.calls[0][1],'/school-lunch/api/backend/shared-auth/me');assert.equal(h.calls[2][1],'/school-lunch/cooking-arrangements?job_id=reported&mode=review');assert.equal(h.storage.has('auth_next'),false);});
test('stored destination is used when no explicit next exists',async()=>{const h=page({search:'',stored:'/shift/planning?week=2026-09-21'});h.login();await flush();assert.equal(h.calls[0][1],'/api/portal/auth/me?system=shift');assert.equal(h.calls.at(-1)[1],'/shift/planning?week=2026-09-21');});
test('plain login has legitimate portal default',async()=>{const h=page({search:'',stored:null});h.login();await flush();assert.equal(h.calls.at(-1)[1],'/');});
for(const status of [401,403,503])test(`login handshake ${status} does not store or navigate and retains target`,async()=>{const h=page({status});h.login();await flush();assert.equal(h.calls.filter(c=>c[0]==='store'||c[0]==='navigate').length,0);assert.ok(h.alerts().length);assert.equal(h.storage.get('auth_next'),'/hospital');});
test('malformed role response stays closed',async()=>{const h=page({identity:{role:'viewer'}});h.login();await flush();assert.equal(h.calls.length,1);assert.ok(h.alerts().length);});
test('unsafe explicit URL blocks even valid credential before any auth request',async()=>{const h=page({search:'?next=https://evil.invalid'});h.login();await flush();assert.equal(h.calls.length,0);assert.match(h.alerts()[0],/復帰先/);});
test('repeated Google callbacks cannot race two handshakes',async()=>{const pending=deferred(),h=page({pending});h.login();h.login();assert.equal(h.calls.length,1);pending.resolve({ok:true,json:async()=>({role:'operator'})});await flush();assert.equal(h.calls.filter(c=>c[0]==='navigate').length,1);});
for(const event of ['logout','unmount'])test(`late successful login after ${event} cannot resurrect credentials`,async()=>{const pending=deferred(),h=page({pending});h.login();const signal=h.calls[0][2].signal;h[event]();assert.equal(signal.aborted,true);pending.resolve({ok:true,json:async()=>({role:'operator'})});await flush();assert.equal(h.calls.filter(c=>c[0]==='store'||c[0]==='navigate').length,0);});
