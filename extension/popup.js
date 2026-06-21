const statusEl = document.getElementById("status");
const listEl = document.getElementById("list");
const subEl = document.getElementById("subcount");

function activeDomain() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      try {
        resolve(tabs[0] ? new URL(tabs[0].url).hostname : "");
      } catch (_) {
        resolve("");
      }
    });
  });
}

function send(msg) {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage(msg, (r) => resolve(r || { ok: false, error: "no response" })));
}

function fillOnPage(cred) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    chrome.scripting.executeScript({
      target: { tabId: tabs[0].id },
      func: (c) => {
        const set = (el, v) => {
          if (!el || v == null) return;
          const proto = el.tagName === "TEXTAREA"
            ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        };
        const eligible = (el) =>
          el && !el.disabled && !el.readOnly && el.offsetParent !== null;
        const looksLikeUsername = (el) => {
          const hay = `${el.name} ${el.id} ${el.autocomplete} ${el.placeholder || ""} ${
            el.getAttribute("aria-label") || ""}`.toLowerCase();
          return /user|email|e-mail|login|account|identif|phone|tel\b/.test(hay)
            || el.type === "email" || el.type === "username";
        };
        const pw = Array.from(document.querySelectorAll('input[type="password"]')).find(eligible)
          || null;
        const cands = Array.from(document.querySelectorAll(
          'input[type="text"], input[type="email"], input[type="tel"], input[type="username"], input:not([type])'))
          .filter(eligible);
        let user = null;
        if (pw) {
          const before = cands
            .filter((el) => pw.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING)
            .reverse();
          user = before.find(looksLikeUsername) || before[0] || null;
        } else {
          const active = document.activeElement;
          user = (eligible(active) && cands.includes(active) && looksLikeUsername(active))
            ? active
            : (cands.find(looksLikeUsername) || (cands.length === 1 ? cands[0] : null));
        }
        set(user, c.username);
        set(pw, c.password);
      },
      args: [cred],
    });
  });
}

function avatarText(c) {
  const s = (c.username || c.title || c.domain || "?").trim();
  return s.charAt(0) || "?";
}

async function main() {
  const ping = await send({ cmd: "ping" });
  if (!ping.ok) {
    subEl.textContent = "Not connected";
    statusEl.innerHTML = `<span class="err">Host unavailable: ${ping.error || "?"}</span>`;
    return;
  }
  const domain = await activeDomain();
  const resp = await send({ cmd: "match", domain });
  const creds = resp.ok ? resp.credentials : [];

  subEl.textContent = `${ping.count.toLocaleString()} saved logins`;
  const n = creds.length;
  statusEl.textContent = domain
    ? `${n} ${n === 1 ? "login" : "logins"} for ${domain}`
    : `${ping.count} saved | open a site to autofill`;

  listEl.innerHTML = "";
  for (const c of creds) {
    const div = document.createElement("div");
    div.className = "cred";
    div.innerHTML =
      `<div class="avatar"></div>` +
      `<div class="meta"><div class="u"></div><div class="d"></div></div>` +
      `<div class="fill">Fill -></div>`;
    div.querySelector(".avatar").textContent = avatarText(c);
    div.querySelector(".u").textContent = c.username || "(no username)";
    div.querySelector(".d").textContent = c.title || c.domain;
    div.addEventListener("click", () => { fillOnPage(c); window.close(); });
    listEl.appendChild(div);
  }
}

main();
