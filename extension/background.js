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

async function connectedPage(context) {
  // A cached documentId becomes invalid on navigation, even when the URL is unchanged.
  // Probe Chrome's current documents without sending any vault data to them.
  const tab = await chrome.tabs.get(context.tabId);
  if (webURL(tab.url)?.origin !== context.origin) throw new Error("ページが移動しました。拡張機能を開き直してください。");
  let frames;
  try {
    frames = await chrome.scripting.executeScript({
      target: { tabId: context.tabId, allFrames: true },
      func: () => {
        let field = document.activeElement;
        while (field?.shadowRoot?.activeElement) field = field.shadowRoot.activeElement;
        let focused = !!field?.matches("input,textarea,[contenteditable='true']");
        try {
          for (let frame = window; frame !== frame.top; frame = frame.parent) {
            if (frame.parent.document.activeElement !== frame.frameElement) focused = false;
          }
        } catch (_) { focused = false; }
        return { origin: window.origin, focused };
      },
    });
  } catch (error) {
    throw new Error("ページに接続できません。拡張機能の「サイトへのアクセス」を確認してください。", { cause: error });
  }
  const top = frames.find(f => f.frameId === 0);
  if (top?.result?.origin !== context.origin) throw new Error("ページが移動しました。拡張機能を開き直してください。");
  const sameOrigin = frames.filter(f => f.result?.origin === context.origin && f.documentId);
  const current = sameOrigin.find(f => f.result.focused) ||
    sameOrigin.find(f => f.documentId === context.documentId) || top;
  if (!current?.documentId) throw new Error("入力するページを確認できません。拡張機能を開き直してください。");
  const fresh = { ...context, frameId: current.frameId, documentId: current.documentId };
  const options = { documentId: fresh.documentId };
  const ready = () => chrome.tabs.sendMessage(fresh.tabId, { cmd: "ready", expectedOrigin: fresh.origin }, options);
  let response;
  try { response = await ready(); } catch (_) { /* The listener can be absent after extension updates. */ }
  if (!response?.ok) {
    await chrome.scripting.executeScript({ target: { tabId: fresh.tabId, documentIds: [fresh.documentId] }, files: ["content.js"] });
    response = await ready();
  }
  if (!response?.ok) throw new Error("ページへの接続を復旧できませんでした。拡張機能を再読み込みしてください。");
  return fresh;
}

async function sendFill(context, payload) {
  const fresh = await connectedPage(context);
  const tab = await chrome.tabs.get(fresh.tabId);
  if (webURL(tab.url)?.origin !== fresh.origin) throw new Error("ページが移動しました。拡張機能を開き直してください。");
  // Pin the secret-bearing message to the document we just checked, never to a reused frame ID.
  return chrome.tabs.sendMessage(fresh.tabId, { ...payload, expectedOrigin: fresh.origin }, { documentId: fresh.documentId });
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
