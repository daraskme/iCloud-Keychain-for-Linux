// Real browser DOM tests with synthetic native-host responses only.
// CHROME_BIN=google-chrome node tests/browser_autofill.mjs
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";

const source = await fs.readFile(new URL("../extension/content.js", import.meta.url), "utf8");
const profile = await fs.mkdtemp(path.join(os.tmpdir(), "icp-browser-test-"));
const server = http.createServer((req, res) => { res.setHeader("Content-Type", "text/html"); res.end("<!doctype html><html><body></body></html>"); });
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}/`;
const browser = spawn(process.env.CHROME_BIN || "google-chrome", ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
let errors = "";
browser.stderr.on("data", data => { errors = (errors + data).slice(-4000); });
let socket;
let serial = 0;
const waiting = new Map();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function call(method, params = {}, sessionId) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    waiting.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}

try {
  let port;
  for (let i = 0; i < 200; i++) {
    try { port = (await fs.readFile(path.join(profile, "DevToolsActivePort"), "utf8")).trim().split("\n"); break; }
    catch (_) { if (browser.exitCode !== null) throw new Error(errors); await sleep(50); }
  }
  if (!port) throw new Error(`Browser failed to start: ${errors}`);
  socket = new WebSocket(`ws://127.0.0.1:${port[0]}${port[1]}`);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const data = JSON.parse(event.data);
    const pending = waiting.get(data.id);
    if (!pending) return;
    waiting.delete(data.id); clearTimeout(pending.timer);
    if (data.error) pending.reject(new Error(JSON.stringify(data.error))); else pending.resolve(data.result);
  };
  const credential = { domain: "127.0.0.1", username: "test@example.invalid", password: "SyntheticOnly!", totp: { code: "123456", expires: Date.now()/1000 + 300, period: 30 } };
  async function page(html, config = {}, setup = "") {
    const { targetId } = await call("Target.createTarget", { url });
    const { sessionId } = await call("Target.attachToTarget", { targetId, flatten: true });
    await call("Page.enable", {}, sessionId);
    await call("Page.bringToFront", {}, sessionId);
    async function evaluate(expression) {
      const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    }
    for (let i = 0; i < 100; i++) { if (await evaluate("location.hostname === '127.0.0.1' && document.readyState === 'complete'")) break; await sleep(20); }
    const data = { credentials: [credential], aliases: [], autofill: true, ...config };
    await evaluate(`document.body.innerHTML = ${JSON.stringify(html)}; globalThis.fixture = ${JSON.stringify(data)};
      globalThis.messages=[]; globalThis.chrome={runtime:{id:'synthetic',lastError:null,
      sendMessage(message, done){ messages.push(message); queueMicrotask(()=> {
        if(message.cmd==='match') done({ok:true,...structuredClone(fixture)});
        else if(message.cmd==='totp') done(fixture.fresh || {ok:false});
        else { if(message.cmd==='remember') fixture.selected={domain:message.credentialDomain,username:message.username}; done({ok:true}); }
      }); }, onMessage:{addListener(listener){globalThis.receive=listener;}}},storage:{onChanged:{addListener(listener){globalThis.storageChanged=listener;}}}};
      ${setup}`);
    await evaluate(source.replace("autoFill(fields).catch(() => {});", "autoFill(fields).catch(error => { globalThis.autofillError = String(error.stack); });"));
    await sleep(200);
    return { evaluate, close: () => call("Target.closeTarget", { targetId }) };
  }
  async function check(name, html, config, verify, setup = "") {
    const p = await page(html, config, setup);
    try { await verify(p); console.log(`PASS ${name}`); }
    catch (error) {
      console.error(name, await p.evaluate("({hidden:document.hidden,secure:isSecureContext,error:globalThis.autofillError,messages,fields:Array.from(document.querySelectorAll('input')).map(f=>({visible:f.checkVisibility(),rect:f.getBoundingClientRect().toJSON(),attached:!!f.__applepw}))})"));
      throw error;
    } finally { await p.close(); }
  }
  const values = p => p.evaluate("Array.from(document.querySelectorAll('input')).map(f=>f.value)");
  await check("username and current password", '<form><input autocomplete="username"><input type="password" autocomplete="current-password"></form>', {}, async p => assert.deepEqual(await values(p), [credential.username, credential.password]));
  await check("username-first then dynamic password", '<input type="email" autocomplete="username">', {}, async p => {
    assert.deepEqual(await values(p), [credential.username]);
    await p.evaluate(`document.body.innerHTML='<input type="password" autocomplete="current-password">'`); await sleep(200);
    assert.deepEqual(await values(p), [credential.password]);
  });
  await check("single verification code", '<label>認証コード<input inputmode="numeric" maxlength="6"></label>', {}, async p => assert.deepEqual(await values(p), ["123456"]));
  await check("userOtp is a code, not a username", '<input id="userOtp" inputmode="numeric" maxlength="6">', {}, async p => assert.deepEqual(await values(p), ["123456"]));
  await check("do not confuse email-delivered codes with stored TOTP", '<input name="emailVerificationCode" maxlength="6">', {}, async p => assert.deepEqual(await values(p), [""]));
  const segments = '<fieldset><legend>認証コード</legend>' + '<input maxlength="1" inputmode="numeric">'.repeat(6) + '</fieldset>';
  await check("six separate code boxes", segments, {}, async p => assert.equal((await values(p)).join(""), "123456"));
  await check("never truncate an eight-digit code", segments, { credentials: [{ ...credential, totp: { ...credential.totp, code: "12345678" } }] }, async p => assert.equal((await values(p)).join(""), ""));
  await check("preserve existing user input", '<form><input autocomplete="username" value="other@example.invalid"><input type="password" value="already typed"></form>', {}, async p => assert.deepEqual(await values(p), ["other@example.invalid", "already typed"]));
  await check("never fill registration passwords", '<form><input type="email"><input type="password"><input type="password"></form>', {}, async p => assert.deepEqual((await values(p)).slice(1), ["", ""]));
  await check("ignore search coupon postal and hidden inputs", '<input name="search"><input name="couponcode" maxlength="6"><input name="postalcode" maxlength="6"><input type="password" style="display:none"><input type="password" style="position:absolute;left:-10000px">', {}, async p => assert.deepEqual(await values(p), ["", "", "", "", ""]));
  const second = { ...credential, username: "second@example.invalid", password: "SecondSynthetic!", totp: { ...credential.totp, code: "654321" } };
  await check("multiple accounts wait for choice; remember for code step", '<form><input autocomplete="username"><input type="password"></form>', { credentials: [credential, second] }, async p => {
    assert.deepEqual(await values(p), ["", ""]);
    await p.evaluate(`new Promise(resolve=>receive({cmd:'fill',credential:fixture.credentials[1]}, {}, resolve))`);
    assert.deepEqual(await values(p), [second.username, second.password]);
    await p.evaluate(`document.body.innerHTML='<input autocomplete="one-time-code">'`); await sleep(200);
    assert.deepEqual(await values(p), ["654321"]);
  });
  await check("failed refresh never fills an expired code", '<input autocomplete="one-time-code">', { credentials: [{ ...credential, totp: { ...credential.totp, expires: 1 } }] }, async p => assert.deepEqual(await values(p), [""]));
  await check("refresh expired code before filling", '<input autocomplete="one-time-code">', { credentials: [{ ...credential, totp: { ...credential.totp, expires: 1 } }], fresh: { ok:true, totp: { code:"987654", expires:Date.now()/1000+300, period:30 } } }, async p => assert.deepEqual(await values(p), ["987654"]));
  await check("assigned private email", '<input type="email">', { credentials: [], aliases: [{ address:"private@example.invalid" }], assignedAlias:"private@example.invalid" }, async p => assert.deepEqual(await values(p), ["private@example.invalid"]));
  await check("autofill switch off", '<input autocomplete="username"><input type="password">', { autofill:false }, async p => assert.deepEqual(await values(p), ["", ""]));
  await check("cross-origin form action", '<form action="https://elsewhere.example/submit"><input autocomplete="username"><input type="password"></form>', {}, async p => assert.deepEqual(await values(p), ["", ""]));
  await check("shadow DOM and composed input events", '<div id="host"></div>', {}, async p => {
    assert.deepEqual(await p.evaluate("Array.from(document.querySelector('#host').shadowRoot.querySelectorAll('input')).map(f=>f.value)"), [credential.username,credential.password]);
    assert.equal(await p.evaluate("globalThis.inputEvents"), 2);
  }, `globalThis.inputEvents=0;document.addEventListener('input',()=>inputEvents++);document.querySelector('#host').attachShadow({mode:'open'}).innerHTML='<form><input autocomplete="username"><input type="password"></form>';`);
  await check("manual popup fill on OTP-only page", '<input autocomplete="one-time-code">', {autofill:false}, async p => {
    const result = await p.evaluate("new Promise(resolve=>receive({cmd:'fill',credential:fixture.credentials[0]}, {}, resolve))");
    assert.equal(result.ok,true); assert.deepEqual(await values(p), ["123456"]);
  });
  console.log("18 browser scenarios passed");
} finally {
  if (socket?.readyState === WebSocket.OPEN) { try { await call("Browser.close"); } catch (_) {} socket.close(); }
  browser.kill("SIGTERM");
  server.close();
  for (const pending of waiting.values()) clearTimeout(pending.timer);
  await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
