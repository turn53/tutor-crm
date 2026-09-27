const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname+'/ui.js', 'utf8');
const apiSource = source.slice(source.indexOf('async function api('), source.indexOf('async function load('));

function client(fetch) {
  let alarm, cleared = false, duration;
  const ctx = vm.createContext({fetch, AbortController, TypeError, token:'test-only',
    setTimeout(fn, ms) {alarm=fn; duration=ms; return 1},
    clearTimeout() {cleared=true},
  });
  vm.runInContext(apiSource, ctx);
  return {api:ctx.api, timeout:()=>alarm(), cleared:()=>cleared, duration:()=>duration};
}

test('successful reads retain CSRF header and release deadline', async()=>{
  const c=client(async(url, options)=>{
    assert.equal(url, '/api/data'); assert.equal(options.method, 'GET');
    assert.equal(options.headers['X-CRM-Token'], 'test-only');
    assert.ok(options.signal); return {ok:true,status:200,json:async()=>({ok:1})};
  });
  assert.equal((await c.api('data')).ok, 1);
  assert.equal(c.duration(), 20000); assert.ok(c.cleared());
});

test('a stalled read is aborted, not left pending forever', async()=>{
  const c=client((url, options)=>new Promise((resolve,reject)=>
    options.signal.addEventListener('abort',()=>reject(new Error('aborted')))));
  const pending=c.api('data'); c.timeout();
  await assert.rejects(pending,/не ответил вовремя/); assert.ok(c.cleared());
});

test('timeout also bounds a stalled JSON response body', async()=>{
  let bodyStarted;
  const started=new Promise(resolve=>{bodyStarted=resolve});
  const c=client(async(url,options)=>({ok:true,status:200,json:()=>new Promise((resolve,reject)=>
    {options.signal.addEventListener('abort',()=>reject(new Error('aborted')));bodyStarted()})}));
  const pending=c.api('data'); await started; c.timeout();
  await assert.rejects(pending,/не ответил вовремя/); assert.ok(c.cleared());
});

test('a write is sent once and timeout warns about uncertain outcome', async()=>{
  let calls=0;
  const c=client((url,options)=>{
    calls++; assert.equal(options.method,'POST'); assert.equal(options.body,'{"amount":100}');
    return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  });
  const pending=c.api('save/payments',{amount:100}); c.timeout();
  await assert.rejects(pending,/операция могла выполниться/);
  assert.equal(calls,1); assert.equal(c.duration(),45000); assert.ok(c.cleared());
});

test('gateway failures are useful even when response is not JSON',async()=>{
  const c=client(async()=>({ok:false,status:502,json:async()=>{throw Error('must not parse')}}));
  await assert.rejects(c.api('save/payments',{}),/операция могла выполниться/);
  assert.ok(c.cleared());
});

test('stale tab token explains how to keep unsaved work',async()=>{
  const c=client(async()=>({ok:false,status:403}));
  await assert.rejects(c.api('data'),/Скопируй несохранённый текст/);
});

test('validation errors remain visible, failed requests are not retried',async()=>{
  let calls=0;
  const c=client(async()=>{calls++;return {ok:false,status:400,json:async()=>({error:'Нужно имя'})}});
  await assert.rejects(c.api('save/students',{}),/Нужно имя/); assert.equal(calls,1);
});

test('network disconnect does not lose the uncertain-write warning',async()=>{
  const c=client(async()=>{throw new TypeError('fetch failed')});
  await assert.rejects(c.api('save/payments',{}),/операция могла выполниться/);
});

test('periodic refresh retries initial data load after connectivity returns',async()=>{
  let loads=0;
  const refreshSource=source.slice(source.indexOf('let refreshing=false;'),source.indexOf('load().then('));
  const ctx=vm.createContext({document:{hidden:false,querySelector:()=>null,activeElement:null},
    api:async()=>({running:false}),automation:null,db:undefined,page:'overview',
    load:async()=>{loads++;if(loads===1)throw Error('offline')},toast:()=>{}});
  vm.runInContext(refreshSource,ctx);
  await ctx.refreshAutomation(); await ctx.refreshAutomation();
  assert.equal(loads,2);
});
