"use strict";
const statusEl = document.getElementById("status");
const listEl = document.getElementById("list");
const aliasEl = document.getElementById("aliases");
const subEl = document.getElementById("subcount");
const automatic = document.getElementById("automatic");

function send(message) {
  return new Promise(resolve => chrome.runtime.sendMessage(message, response =>
    resolve(chrome.runtime.lastError ? { ok: false, error: chrome.runtime.lastError.message }
      : response || { ok: false, error: "応答がありません。" })));
}

async function act(message) {
  const result = await send(message);
  if (result.ok) window.close();
  else statusEl.textContent = result.error || "入力できませんでした。入力欄をクリックしてください。";
}

function row(title, subtitle, action) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = "cred";
  const text = document.createElement("div");
  text.className = "meta";
  const name = document.createElement("div");
  name.className = "u";
  name.textContent = title;
  const detail = document.createElement("div");
  detail.className = "d";
  detail.textContent = subtitle;
  text.append(name, detail);
  const fill = document.createElement("span");
  fill.className = "fill";
  fill.textContent = "入力";
  item.append(text, fill);
  item.addEventListener("click", action);
  return item;
}

automatic.addEventListener("change", async () => {
  const result = await send({ cmd: "autofill_setting", enabled: automatic.checked });
  if (!result.ok) statusEl.textContent = result.error;
});

async function main() {
  const ping = await send({ cmd: "ping" });
  if (!ping.ok) {
    subEl.textContent = "未接続";
    statusEl.textContent = `接続できません: ${ping.error}`;
    return;
  }
  subEl.textContent = `${ping.count} 件`;
  const result = await send({ cmd: "popup" });
  if (!result.ok) { statusEl.textContent = result.error; return; }
  automatic.checked = result.autofill !== false;
  statusEl.textContent = `${result.domain} · 候補が1件なら自動入力します。`;
  for (const credential of result.credentials || []) {
    const item = row(credential.username || credential.domain,
      credential.domain + (credential.totp ? " · 認証コードあり" : ""),
      () => act({ cmd: "fill", username: credential.username, credentialDomain: credential.domain }));
    const container = document.createElement("div");
    container.className = "login-row";
    container.appendChild(item);
    if (credential.totp) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy-code";
      copy.textContent = "コードコピー";
      copy.addEventListener("click", async () => {
        try {
          const fresh = await send({ cmd: "copy_code", username: credential.username, credentialDomain: credential.domain });
          if (!fresh.ok || !fresh.totp?.code || fresh.totp.expires <= Date.now()/1000 + 1) throw new Error("有効な認証コードを取得できません。");
          await navigator.clipboard.writeText(fresh.totp.code);
          copy.textContent = "コピー済み";
        } catch (error) { statusEl.textContent = error.message; }
      });
      container.appendChild(copy);
    }
    listEl.appendChild(container);
  }
  for (const alias of result.allAliases || []) {
    aliasEl.appendChild(row(alias.address,
      (alias.label || "メールを非公開") + (result.assignedAlias === alias.address ? " · このサイトで使用中" : ""),
      () => act({ cmd: "fill_alias", address: alias.address })));
  }
}

main().catch(error => { statusEl.textContent = String(error.message || error); });
