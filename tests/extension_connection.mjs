// Actual Chrome extension messaging, with an isolated profile and synthetic vault only.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";

const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "icp-connection-test-"));
const extension = path.join(temporary, "extension");
const extensionSource = process.env.ICP_EXTENSION_SOURCE || new URL("../extension", import.meta.url);
if (process.env.ICP_EXTENSION_SOURCE) {
  for (const file of await fs.readdir(extensionSource, {withFileTypes:true})) {
    assert.equal(file.isSymbolicLink(), false, `Installed extension must contain real files: ${file.name}`);
  }
}
await fs.cp(extensionSource, extension, { recursive: true });
const manifest = JSON.parse(await fs.readFile(path.join(extension, "manifest.json"), "utf8"));
delete manifest.background.scripts;
delete manifest.browser_specific_settings;
await fs.writeFile(path.join(extension, "manifest.json"), JSON.stringify(manifest));
const credential = { domain: "127.0.0.1", username: "synthetic@example.invalid", password: "SyntheticOnly!" };
const background = await fs.readFile(path.join(extension, "background.js"), "utf8");
await fs.writeFile(path.join(extension, "background.js"), background.replace("function nativeRequest(payload)", "function unusedNativeRequest(payload)") + `
async function nativeRequest(payload) {
  if (payload.cmd === 'match') return {ok:true,credentials:[${JSON.stringify(credential)}],aliases:[]};
  if (payload.cmd === 'aliases') return {ok:true,aliases:[]};
  return {ok:true,count:1};
}
`);
if (process.env.ICP_TEST_SYMLINKS === "1") {
  const assets = path.join(temporary, "assets");
  await fs.rename(extension, assets);
  await fs.mkdir(extension);
  for (const name of await fs.readdir(assets)) await fs.symlink(path.join(assets, name), path.join(extension, name));
}
const form = '<form><input autocomplete="username"><input type="password" autocomplete="current-password"></form>';
const server = http.createServer((req, res) => {
  res.setHeader("Content-Type", "text/html");
  const body = req.url === "/frame" ? '<iframe src="/login"></iframe>' : form;
  res.end(`<!doctype html><html><body>${body}</body></html>`);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}/login`;
const browser = spawn(process.env.CHROME_BIN || "google-chrome", ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-pipe", "--enable-unsafe-extension-debugging", `--user-data-dir=${temporary}/profile`, "about:blank"], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
browser.stderr.on("data", () => {});
let serial = 0, buffer = "";
const waiting = new Map();
browser.stdio[4].on("data", chunk => {
  buffer += chunk.toString();
  let end;
  while ((end = buffer.indexOf("\0")) !== -1) {
    const message = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
    const request = waiting.get(message.id);
    if (!request) continue;
    waiting.delete(message.id); clearTimeout(request.timer);
    if (message.error) request.reject(new Error(JSON.stringify(message.error)));
    else request.resolve(message.result);
  }
});
function call(method, params = {}, sessionId) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    waiting.set(id, { resolve, reject, timer });
    browser.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
  });
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function evaluate(session, expression) {
  const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, session);
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function openPage(address) {
  const { targetId } = await call("Target.createTarget", { url: address });
  const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
  await call("Page.enable", {}, sessionId);
  await call("Page.bringToFront", {}, sessionId);
  for (let i = 0; i < 100; i++) {
    if (await evaluate(sessionId, "document.readyState === 'complete' && !!document.querySelector('input,iframe')")) return sessionId;
    await sleep(20);
  }
  throw new Error("Test page did not load");
}
try {
  // This tab predates extension installation, so it has no content-script listener.
  const page = await openPage(url);
  const { id } = await call("Extensions.loadUnpacked", { path: extension });
  async function connectWorker(previousId) {
    let worker;
    for (let i = 0; i < 100; i++) {
      worker = (await call("Target.getTargets")).targetInfos.find(t => t.type === "service_worker" && t.targetId !== previousId && t.url.startsWith(`chrome-extension://${id}/`));
      if (worker) break;
      await sleep(50);
    }
    assert.ok(worker, "Extension service worker started: " + JSON.stringify((await call("Target.getTargets")).targetInfos.map(t=>({type:t.type,id:t.targetId,url:t.url}))));
    const { sessionId } = await call("Target.attachToTarget", { targetId: worker.targetId, flatten: true });
    for (let i = 0; i < 100; i++) {
      if (await evaluate(sessionId, "typeof chrome !== 'undefined' && typeof handle === 'function'")) break;
      await sleep(20);
    }
    return {session:sessionId,target:worker.targetId};
  }
  let worker = await connectWorker(), sw = worker.session;
  await evaluate(sw, "chrome.storage.local.set({autofillEnabled:false})");
  const fill = () => evaluate(sw, `handle({cmd:'fill',credentialDomain:'127.0.0.1',username:'synthetic@example.invalid'}, {id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')}).catch(error=>({ok:false,error:error.message}))`);
  const values = session => evaluate(session, "Array.from(document.querySelectorAll('input')).map(f=>f.value)");
  const first = await fill();
  assert.equal(first.ok, true, `Tab opened before extension loading: ${first.error}`);
  assert.deepEqual(await values(page), [credential.username, credential.password]);
  console.log("PASS inject into a tab opened before the extension was installed");
  await evaluate(page, "document.querySelector('input').focus()");
  await sleep(100);
  await call("Page.reload", {}, page); await sleep(300);
  assert.deepEqual(await values(page), ["", ""]);
  const declarative = await evaluate(sw, `(async()=>{const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    try{return await chrome.tabs.sendMessage(tab.id,{cmd:'ready',expectedOrigin:new URL(tab.url).origin},{frameId:0});}
    catch(error){return {ok:false,error:error.message};}})()`);
  assert.equal(declarative.ok, true, `Automatic content-script loading: ${declarative.error}`);
  const afterReload = await fill();
  assert.equal(afterReload.ok, true, `Stale document after reload: ${afterReload.error}`);
  assert.deepEqual(await values(page), [credential.username, credential.password]);
  console.log("PASS discard stale focus after page reload");
  await evaluate(sw, "chrome.storage.local.set({autofillEnabled:true})");
  const automaticPage = await openPage(url);
  for (let i=0; i<100; i++) {
    if ((await values(automaticPage))[1] === credential.password) break;
    await sleep(20);
  }
  assert.deepEqual(await values(automaticPage), [credential.username, credential.password]);
  console.log("PASS automatic filling in a newly opened page without popup interaction");
  await evaluate(sw, "chrome.storage.local.set({autofillEnabled:false})");
  await call("Page.bringToFront", {}, page);
  await evaluate(page, "document.querySelectorAll('input').forEach(field=>field.value='')");
  // Replace the extension while leaving the existing document and old script context intact.
  await call("Extensions.uninstall", {id});
  await call("Extensions.loadUnpacked", {path:extension});
  worker = await connectWorker(worker.target); sw = worker.session;
  await evaluate(sw, "chrome.storage.local.set({autofillEnabled:false})");
  await call("Page.bringToFront", {}, page);
  const afterUpdate = await fill();
  assert.equal(afterUpdate.ok, true, `Existing tab after extension replacement: ${afterUpdate.error}`);
  assert.deepEqual(await values(page), [credential.username, credential.password]);
  console.log("PASS reconnect an existing tab after extension replacement without reloading the site");
  const framed = await openPage(url.replace('/login', '/frame'));
  await evaluate(framed, "document.querySelector('iframe').contentDocument.querySelector('input').focus()");
  await sleep(100);
  assert.equal((await fill()).ok, true);
  assert.deepEqual(await evaluate(framed, "Array.from(document.querySelector('iframe').contentDocument.querySelectorAll('input')).map(f=>f.value)"), [credential.username,credential.password]);
  console.log("PASS target the focused same-origin iframe");
  // Do not deliver credentials if navigation changes the site's origin during a request.
  await evaluate(sw, `globalThis.savedNativeRequest=nativeRequest;nativeRequest=async payload=>{
    if(payload.cmd==='match') { const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      await chrome.tabs.update(tab.id,{url:${JSON.stringify(url.replace('127.0.0.1','localhost'))}});
      await new Promise(resolve=>setTimeout(resolve,300)); }
    return savedNativeRequest(payload);
  }`);
  assert.equal((await fill()).ok, false);
  assert.deepEqual(await values(framed), ["", ""]);
  console.log("PASS navigation to another origin never receives credentials");
  console.log("6 real extension connection scenarios passed");
} finally {
  try { await call("Browser.close"); } catch (_) {}
  browser.kill("SIGTERM"); server.close();
  for (const request of waiting.values()) clearTimeout(request.timer);
  await fs.rm(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
