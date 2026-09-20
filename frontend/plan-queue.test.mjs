import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createLatestPlanQueue} from './src/plan-queue.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
  const runs=[],results=[],errors=[],busy=[];
  let concurrent=0,maxConcurrent=0;
  const q=createLatestPlanQueue({onResult:v=>results.push(v),onError:e=>errors.push(e.message),onBusy:v=>busy.push(v)});
  function run(key){return()=>new Promise((resolve,reject)=>{
    concurrent++;maxConcurrent=Math.max(maxConcurrent,concurrent);
    runs.push({key,resolve:v=>{concurrent--;resolve(v);},reject:e=>{concurrent--;reject(e);}});
  });}
  return {q,run,runs,results,errors,busy,max:()=>maxConcurrent};
}

test('finish active calculation, show its time, then run only latest pending time',async()=>{
  const h=harness();
  h.q.submit('t0',h.run('t0'));h.q.submit('t15',h.run('t15'));h.q.submit('t30',h.run('t30'));
  assert.deepEqual(h.runs.map(x=>x.key),['t0']);
  h.runs[0].resolve('t0 result');await tick();
  assert.deepEqual(h.results,['t0 result']);assert.deepEqual(h.runs.map(x=>x.key),['t0','t30']);
  h.runs[1].resolve('t30 result');await tick();
  assert.deepEqual(h.results,['t0 result','t30 result']);assert.equal(h.max(),1);
  assert.deepEqual(h.busy,[true,true,false]);
});

for(const failure of [false,true])test('changed mission/parameters hide stale '+(failure?'errors':'results')+' without overlapping requests',async()=>{
  const h=harness();h.q.submit('old',h.run('old'));h.q.submit('old-pending',h.run('old-pending'));
  h.q.invalidate();h.q.submit('new',h.run('new'));
  assert.equal(h.runs.length,1);
  failure?h.runs[0].reject(new Error('stale')):h.runs[0].resolve('stale');await tick();
  assert.deepEqual(h.results,[]);assert.deepEqual(h.errors,[]);
  assert.deepEqual(h.runs.map(x=>x.key),['old','new']);
  h.runs[1].resolve('new result');await tick();assert.deepEqual(h.results,['new result']);assert.equal(h.max(),1);
});

test('same active input deduplicates and replaces queued different input',async()=>{
  const h=harness();h.q.submit('a',h.run('a'));h.q.submit('b',h.run('b'));h.q.submit('a',h.run('duplicate'));
  h.runs[0].resolve('a');await tick();assert.equal(h.runs.length,1);assert.deepEqual(h.results,['a']);
});

test('turning auto off clears pending, but an active result remains usable',async()=>{
  const h=harness();h.q.submit('a',h.run('a'));h.q.submit('b',h.run('b'));h.q.clearPending();
  h.runs[0].resolve('a');await tick();assert.equal(h.runs.length,1);assert.deepEqual(h.results,['a']);
});

test('unmount suppresses callbacks and drops pending requests',async()=>{
  const h=harness();h.q.submit('a',h.run('a'));h.q.submit('b',h.run('b'));h.q.close();h.q.submit('c',h.run('c'));
  h.runs[0].resolve('a');await tick();assert.equal(h.runs.length,1);assert.deepEqual(h.results,[]);assert.deepEqual(h.busy,[true]);
});

test('current request failure is visible and does not block a newer request',async()=>{
  const h=harness();h.q.submit('a',h.run('a'));h.q.submit('b',h.run('b'));
  h.runs[0].reject(new Error('connection failed'));await tick();assert.deepEqual(h.errors,['connection failed']);
  h.runs[1].resolve('b');await tick();assert.deepEqual(h.results,['b']);assert.equal(h.max(),1);
});
