import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs/promises";

const source = await fs.readFile(new URL("../extension/background.js", import.meta.url), "utf8");
const credential = {domain:"example.com",username:"alice",password:"synthetic-only",totp:{code:"123456",expires:9999999999}};
const alias = {address:"private@example.invalid",label:"Test",domain:""};
function storage(data = {}) {
  return {data, async get(keys) { if(keys===null) return {...data}; return Object.fromEntries((Array.isArray(keys)?keys:[keys]).filter(k=>k in data).map(k=>[k,data[k]])); },
    async set(values){Object.assign(data,values);},async remove(keys){for(const key of keys)delete data[key];}};
}
function worker(session = storage(), local = storage()) {
  const requests = [], fills = [];
  let listener, removed;
  const chrome = {runtime:{id:"test",getURL:p=>`chrome-extension://test/${p}`,lastError:null,
    sendNativeMessage(host, req, done){requests.push(structuredClone(req));let result;
      if(req.cmd==="match")result={ok:true,credentials:[credential],aliases:[]};
      else if(req.cmd==="aliases")result={ok:true,aliases:[alias]};
      else if(req.cmd==="totp")result={ok:true,totp:credential.totp};
      else result={ok:true,count:1}; done(structuredClone(result));},
    onMessage:{addListener:fn=>listener=fn}},storage:{session,local},tabs:{
      query:async()=>[{id:7,url:"https://example.com/login"}],
      sendMessage:async(id,payload,options)=>{fills.push({id,payload,options});return {ok:true};},
      onRemoved:{addListener:fn=>removed=fn}}};
  vm.runInNewContext(source,{chrome,URL,Date,Promise,Set});
  return {requests,fills,session,local,removed,
    send:(message,sender)=>new Promise(resolve=>listener(message,sender,resolve))};
}
const page = {id:"test",url:"https://example.com/login",origin:"https://example.com",tab:{id:7,url:"https://example.com/login"},frameId:0,documentId:"doc-1"};
const popup = {id:"test",url:"chrome-extension://test/popup.html"};

test("content cannot spoof a different site's domain",async()=>{
  const w=worker();assert.equal((await w.send({cmd:"match",domain:"bank.example"},page)).ok,true);
  assert.deepEqual(w.requests,[{cmd:"match",domain:"example.com"}]);
});
test("cross-origin and sandboxed frames receive no credentials",async()=>{
  const w=worker();for(const sender of [{...page,url:"https://unrelated.example/frame"},{...page,origin:"null"}])
    assert.equal((await w.send({cmd:"match"},sender)).ok,false);
  assert.equal(w.requests.length,0);
});
test("pages cannot enumerate all aliases or modify settings",async()=>{
  const w=worker();for(const cmd of ["popup","aliases","autofill_setting","fill_alias"])
    assert.equal((await w.send({cmd},page)).ok,false);
  assert.equal(w.requests.length,0);
});
test("account choice survives service worker restart in session storage only",async()=>{
  const session=storage(),local=storage();const first=worker(session,local);
  await first.send({cmd:"remember",credentialDomain:"example.com",username:"alice"},page);
  const second=worker(session,local);const result=await second.send({cmd:"match"},page);
  assert.equal(result.selected.username,"alice");assert.equal(result.autofill,true);
  assert.equal(JSON.stringify(session.data).includes("synthetic-only"),false);
  assert.deepEqual(local.data,{});
  await second.removed(7);assert.deepEqual(session.data,{});
});
test("expired account choices are ignored",async()=>{
  const w=worker(storage({"choice:7:https://example.com":{domain:"example.com",username:"alice",at:1}}));
  assert.equal((await w.send({cmd:"match"},page)).selected,null);
});
test("popup fill targets the focused document and fetches the credential again",async()=>{
  const w=worker();await w.send({cmd:"focused"},{...page,frameId:2,documentId:"doc-frame"});
  const result=await w.send({cmd:"fill",credentialDomain:"example.com",username:"alice",password:"forged"},popup);
  assert.equal(result.ok,true);assert.equal(w.fills[0].payload.credential.password,"synthetic-only");
  assert.equal(w.fills[0].options.documentId,"doc-frame");
});
test("explicit alias selection remembers only that site's assignment",async()=>{
  const w=worker();assert.equal((await w.send({cmd:"fill_alias",address:alias.address},popup)).ok,true);
  assert.equal(w.local.data["alias:https://example.com"],alias.address);
  const result=await w.send({cmd:"match"},page);
  assert.equal(result.assignedAlias,alias.address);assert.equal(result.aliases[0].address,alias.address);
});
test("code refresh is scoped to the actual page and exact saved login",async()=>{
  const w=worker();await w.send({cmd:"totp",domain:"evil.example",username:"alice",credentialDomain:"example.com"},page);
  assert.equal(w.requests[0].domain,"example.com");assert.equal(w.requests[0].credential_domain,"example.com");
});
