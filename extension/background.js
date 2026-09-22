"use strict";
const HOST = "org.icp.native";
const TTL = 15 * 60 * 1000;
const sessionStore = chrome.storage.session;

function nativeRequest(payload) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage(HOST, payload, (response) => {
        resolve(chrome.runtime.lastError
          ? { ok: false, error: chrome.runtime.lastError.message }
          : response || { ok: false, error: "Native host did not respond" });
      });
    } catch (error) { resolve({ ok: false, error: String(error) }); }
  });
}

function webURL(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url : null;
  } catch (_) { return null; }
}

function contentContext(sender) {
  const page = webURL(sender.url);
  const top = webURL(sender.tab?.url);
  if (!page || !top || page.origin !== top.origin || sender.origin === "null") return null;
  return { tabId: sender.tab.id, frameId: sender.frameId || 0,
    documentId: sender.documentId, origin: page.origin, domain: page.hostname };
}

async function matches(context) {
  const result = await nativeRequest({ cmd: "match", domain: context.domain });
  if (!result.ok) return result;
  const aliasKey = `alias:${context.origin}`;
  const settings = await chrome.storage.local.get([aliasKey, "autofillEnabled"]);
  if (settings[aliasKey]) {
    const all = await nativeRequest({ cmd: "aliases" });
    const assigned = (all.aliases || []).find(a => a.address === settings[aliasKey]);
    if (assigned) { result.aliases = [assigned]; result.assignedAlias = assigned.address; }
  }
  const key = `choice:${context.tabId}:${context.origin}`;
  const selected = (await sessionStore.get(key))[key];
  result.selected = selected && Date.now() - selected.at < TTL ? selected : null;
  result.autofill = settings.autofillEnabled !== false;
  return result;
}

async function remember(context, identity) {
  const key = `choice:${context.tabId}:${context.origin}`;
  await sessionStore.set({ [key]: { domain: identity.domain, username: identity.username, at: Date.now() } });
}

async function popupContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const page = webURL(tab?.url);
  if (!page) throw new Error("入力したいサイトを開いてください。");
  const focused = (await sessionStore.get(`focus:${tab.id}`))[`focus:${tab.id}`];
  if (focused && focused.origin === page.origin && Date.now() - focused.at < TTL) return focused;
  return { tabId: tab.id, frameId: 0, origin: page.origin, domain: page.hostname };
}

async function sendFill(context, payload) {
  const options = context.documentId ? { documentId: context.documentId } : { frameId: context.frameId };
  try {
    return await chrome.tabs.sendMessage(context.tabId, payload, options);
  } catch (_) {
    throw new Error("サイトを再読み込みし、入力欄をクリックしてから再実行してください。");
  }
}

async function handle(message, sender) {
  if (!message || sender.id !== chrome.runtime.id) return { ok: false, error: "Invalid sender" };
  if (sender.tab) {
    const context = contentContext(sender);
    if (!context) return { ok: false, error: "Frame origin does not match the page" };
    if (message.cmd === "match") return matches(context);
    if (message.cmd === "totp") return nativeRequest({ cmd: "totp", domain: context.domain,
      username: message.username, credential_domain: message.credentialDomain });
    if (message.cmd === "focused") {
      await sessionStore.set({ [`focus:${context.tabId}`]: { ...context, at: Date.now() } });
      return { ok: true };
    }
    if (message.cmd === "remember") {
      await remember(context, { domain: message.credentialDomain, username: message.username });
      return { ok: true };
    }
    return { ok: false, error: "Unsupported page request" };
  }
  if (sender.url !== chrome.runtime.getURL("popup.html")) return { ok: false, error: "Invalid extension page" };
  if (message.cmd === "ping") return nativeRequest({ cmd: "ping" });
  if (message.cmd === "autofill_setting") {
    await chrome.storage.local.set({ autofillEnabled: message.enabled === true });
    return { ok: true };
  }
  const context = await popupContext();
  if (message.cmd === "popup") {
    const result = await matches(context);
    const all = await nativeRequest({ cmd: "aliases" });
    return { ...result, domain: context.domain, allAliases: all.aliases || [] };
  }
  if (message.cmd === "fill_alias") {
    const all = await nativeRequest({ cmd: "aliases" });
    const alias = (all.aliases || []).find(a => a.address === message.address);
    if (!alias) return { ok: false, error: "有効なアドレスが見つかりません。同期してください。" };
    const result = await sendFill(context, { cmd: "fill_alias", alias });
    if (result?.ok) await chrome.storage.local.set({ [`alias:${context.origin}`]: alias.address });
    return result;
  }
  if (message.cmd === "fill" || message.cmd === "copy_code") {
    const result = await matches(context);
    const found = (result.credentials || []).filter(c => c.domain === message.credentialDomain && c.username === message.username);
    if (found.length !== 1) return { ok: false, error: "ログイン項目を特定できません。同期してください。" };
    const credential = found[0];
    if (message.cmd === "copy_code") return nativeRequest({ cmd: "totp", domain: context.domain,
      username: credential.username, credential_domain: credential.domain });
    const filled = await sendFill(context, { cmd: "fill", credential });
    if (filled?.ok) await remember(context, credential);
    return filled;
  }
  return { ok: false, error: "Unknown request" };
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(result => respond(result || { ok: false }),
    error => respond({ ok: false, error: String(error.message || error) }));
  return true;
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const items = await sessionStore.get(null);
  await sessionStore.remove(Object.keys(items).filter(key => key === `focus:${tabId}` || key.startsWith(`choice:${tabId}:`)));
});
