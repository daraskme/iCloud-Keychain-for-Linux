(function () {
  "use strict";
  if (globalThis.__icpContentLoaded) return;
  globalThis.__icpContentLoaded = true;
  const edited = new WeakSet();
  const autoFilled = new WeakSet();
  let focusedField = null;
  let menuHost = null;

  const PASSWORD_SEL = 'input[type="password"]';
  // Username-ish inputs. Excludes password (handled separately) and obvious non-login types.
  const USERNAME_SEL =
    'input[type="text"], input[type="email"], input[type="tel"], input[type="username"], input:not([type])';
  const OTP_SEL = 'input[type="text"], input[type="tel"], input[type="number"], input:not([type])';
  const SEGMENT_SEL = 'input[maxlength="1"]';
  const FIELD_SEL = `${PASSWORD_SEL}, ${USERNAME_SEL}, input[type="number"]`;

  function visible(el) {
    if (!el || el.disabled || el.readOnly) return false;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  }

  const observedRoots = new WeakSet();
  function observeRoot(root) {
    if (observedRoots.has(root)) return;
    observedRoots.add(root);
    observer.observe(root, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["type", "autocomplete", "disabled", "readonly", "hidden", "style", "class"] });
  }

  function deepQueryAll(selector, root) {
    root = root || document;
    const out = [];
    const walk = (node) => {
      node.querySelectorAll(selector).forEach((el) => out.push(el));
      node.querySelectorAll("*").forEach((el) => {
        if (el.shadowRoot) { observeRoot(el.shadowRoot); walk(el.shadowRoot); }
      });
    };
    walk(root);
    return out;
  }

  function deepQuery(selector) {
    return deepQueryAll(selector)[0] || null;
  }

  function looksLikeUsername(el) {
    const hay = fieldWords(el);
    if (/search|coupon|promo|postal|captcha|firstname|lastname|fullname|displayname/.test(hay)) return false;
    if (/otp|verification|authcode|smscode|emailcode|認証|確認コード/.test(hay)) return false;
    if (/address|street|city|company|organization/.test(hay) && !/email/.test(hay)) return false;
    return /user|email|e-mail|login|account|identif|ユーザー|メール|ログイン/.test(hay)
      || el.type === "email" || el.type === "username";
  }

  const OTP_KEYWORDS = [
    "2facode", "approvalscode", "authcode", "authentication", "mfacode", "onetimecode",
    "onetimepassword", "otccode", "otcconfirmation", "otpcode", "secondfactor", "securitycode",
    "smscode", "totp", "totpcode", "twofa", "twofactor", "twofactorcode", "verificationcode",
    "verifycode",
  ];
  const OTP_NEVER = ["recovery", "backup", "smscode", "emailcode", "emailverificationcode"];
  const OTP_AMBIGUOUS = ["code", "otc", "otp", "2fa", "mfa", "pin", "token", "challenge"];
  const OTP_EXCLUDE = [
    "zip", "postal", "postcode", "coupon", "promo", "discount", "referral",
    "invite", "area", "country", "currency", "barcode", "encode", "decode", "search", "captcha",
  ];
  const OTP_TYPES = new Set(["text", "tel", "number"]);

  function fieldWords(el) {
    return `${el.name} ${el.id} ${el.autocomplete} ${el.placeholder || ""} ${
      el.getAttribute("aria-label") || ""} ${el.className} ${Array.from(el.labels || []).map(l => l.textContent).join(" ")}`.toLowerCase().replace(/[\s_-]/g, "");
  }

  function namedLikeOtp(el) {
    const words = fieldWords(el);
    if (OTP_NEVER.some((w) => words.includes(w))) return false;
    if ((el.autocomplete || "").toLowerCase().includes("one-time-code")) return true;
    if (!OTP_TYPES.has(el.type)) return false;
    if (OTP_EXCLUDE.some((w) => words.includes(w))) return false;
    if (OTP_KEYWORDS.some((w) => words.includes(w)) || /認証コード|確認コード|ワンタイム|セキュリティコード/.test(words)) return true;
    if (!OTP_AMBIGUOUS.some((w) => words.includes(w))) return false;
    return (el.maxLength >= 4 && el.maxLength <= 10) || el.inputMode === "numeric"
      || /0-9|\\d/.test(el.pattern || "");
  }

  function segmentGroup(el) {
    if (el.maxLength !== 1) return null;
    let parent = el.parentElement;
    for (let depth = 0; parent && depth < 4; depth++, parent = parent.parentElement) {
      const group = deepQueryAll(SEGMENT_SEL, parent).filter(f => visible(f) && f.form === el.form && OTP_TYPES.has(f.type));
      const context = `${parent.getAttribute("aria-label") || ""} ${parent.textContent || ""}`;
      if (group.length >= 4 && group.length <= 8 && group.includes(el) &&
          (group.some(namedLikeOtp) || /認証|確認コード|verification|one.time|security.code|totp|two.factor/i.test(context))) return group;
      if (parent === el.form) break;
    }
    return null;
  }

  function isOtpField(el) {
    return namedLikeOtp(el) || !!segmentGroup(el);
  }

  // Sites do render the password box as type="text" with the real one hidden (LinkedIn).
  function looksLikePassword(el) {
    if (el.type === "password") return true;
    if (!OTP_TYPES.has(el.type)) return false;
    const words = fieldWords(el);
    return words.includes("password")
      && !/passwordless|passwordhint|forgotpassword|resetpassword/.test(words);
  }

  function newPassword(el) {
    return /new-password/.test(el.autocomplete || "") || /newpassword|confirmpassword|repeatpassword|新しいパスワード|パスワード確認/.test(fieldWords(el));
  }

  // A page can carry several copies of the same sign-in form
  function within(el, selector) {
    const root = el?.form || el?.getRootNode() || document;
    return deepQueryAll(selector, root).filter(f => visible(f) && (!el || f.form === el.form));
  }

  function passwordField(anchor) {
    if (anchor && visible(anchor) && looksLikePassword(anchor) && !newPassword(anchor)) return anchor;
    return within(anchor, FIELD_SEL).find(f => looksLikePassword(f) && !newPassword(f)) || null;
  }

  function findUsernameField(pwField) {
    const all = within(pwField, USERNAME_SEL).filter(el => !looksLikePassword(el) && !isOtpField(el) && looksLikeUsername(el));
    // Prefer a field that precedes the password field in document order. Across shadow-root
    // boundaries compareDocumentPosition can't establish order, so fall back to any candidate.
    const before = all
      .filter((inp) => pwField.compareDocumentPosition(inp) & Node.DOCUMENT_POSITION_PRECEDING)
      .reverse();
    return before[0] || all[0] || null;
  }

  function setValue(el, value) {
    if (!el) return;
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  async function fill(cred, anchor, automatic = false) {
    if (anchor && isOtpField(anchor)) return fillCode(cred, anchor, automatic);
    const pw = passwordField(anchor);
    let userField = null;
    if (anchor && !looksLikePassword(anchor) && looksLikeUsername(anchor)) userField = anchor;
    else if (pw) userField = findUsernameField(pw);
    else userField = within(anchor, USERNAME_SEL).find(looksLikeUsername);
    let count = 0;
    if (userField && cred.username && (!automatic || canAuto(userField)) &&
        (userField.type !== "email" || cred.username.includes("@"))) {
      put(userField, cred.username, automatic); count++;
    }
    if (pw && cred.password && (!automatic || (canAuto(pw) && automaticForm(pw)))) { put(pw, cred.password, automatic); count++; }
    if (count) {
      send({ cmd: "remember", username: cred.username, credentialDomain: cred.domain });
      if (cache) cache.selected = { domain: cred.domain, username: cred.username };
    }
    if (cred.totp) {
      const otp = within(anchor || pw, OTP_SEL).find(isOtpField);
      if (otp) count += await fillCode(cred, otp, automatic);
    }
    return count;
  }

  function currentCode(cred) {
    if (!cred.totp) return Promise.resolve("");
    if (Date.now() / 1000 < cred.totp.expires - 1) return Promise.resolve(cred.totp.code);
    return send({ cmd: "totp", domain: location.hostname, username: cred.username, credentialDomain: cred.domain }).then((r) => {
      if (r?.ok && r.totp?.expires > Date.now() / 1000 + 1) { cred.totp = r.totp; return r.totp.code; }
      return "";
    });
  }

  function fillCode(cred, anchor, automatic = false) {
    const page = location.href;
    return currentCode(cred).then((code) => {
      if (!code || !anchor.isConnected || !visible(anchor) || location.href !== page) return 0;
      const group = segmentGroup(anchor);
      if (group) {
        if (code.length !== group.length || (automatic && group.some(f => !canAuto(f)))) return 0;
        group.forEach((el, i) => put(el, code[i], automatic));
        return group.length;
      }
      if ((anchor.maxLength > 0 && anchor.maxLength < code.length) || (automatic && !canAuto(anchor))) return 0;
      put(anchor, code, automatic);
      return 1;
    });
  }

  // Hide My Email aliases carry no password - always fill the username/email field, never
  // the password field, even when that's the anchor that was clicked/focused.
  function fillAlias(alias, anchor) {
    const pw = passwordField(anchor);
    const userField = anchor && looksLikeUsername(anchor) && !isOtpField(anchor) ? anchor :
      (pw ? findUsernameField(pw) : within(anchor, USERNAME_SEL).find(looksLikeUsername));
    if (userField) { put(userField, alias.address, false); return 1; }
    return 0;
  }

  function canAuto(field) {
    return field.isConnected && visible(field) && !field.value && !edited.has(field) && !autoFilled.has(field);
  }

  function put(field, value, automatic) {
    field.__applepwFilled = true;
    if (automatic) autoFilled.add(field);
    setValue(field, value);
  }

  // Unix epoch seconds (0 = unknown). Render as a coarse "N days/months/years ago" string;
  // "" when unknown so the line is omitted.
  function relTime(when) {
    if (!when) return "";
    const days = Math.floor(Date.now() / 1000 / 86400 - when / 86400);
    if (days < 0) return "";
    if (days < 1) return "today";
    if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
    const years = Math.floor(days / 365);
    return `${years} year${years === 1 ? "" : "s"} ago`;
  }

  let tickers = [];
  let ticker = null;

  function removeMenu() {
    if (ticker) { clearInterval(ticker); ticker = null; }
    tickers = [];
    if (menuHost) menuHost.remove();
    menuHost = null;
  }

  function refreshCode(cred) {
    if (cred.__refreshing) return;
    cred.__refreshing = true;
    send({ cmd: "totp", domain: location.hostname, username: cred.username, credentialDomain: cred.domain }).then((r) => {
      cred.__refreshing = false;
      if (r && r.ok && r.totp) cred.totp = r.totp;
    });
  }

  function codeLine(cred, style) {
    const el = document.createElement("div");
    Object.assign(el.style, style);
    const paint = () => {
      const left = Math.max(0, Math.round(cred.totp.expires - Date.now() / 1000));
      el.textContent = left ? `${cred.totp.code}  ${left}s` : "更新中…";
      if (!left) refreshCode(cred);
    };
    paint();
    tickers.push(paint);
    return el;
  }

  function subLine(text) {
    const el = document.createElement("div");
    el.textContent = text;
    Object.assign(el.style, { fontSize: "11px", color: "#888" });
    return el;
  }

  function menuRow(onPick) {
    const row = document.createElement("div");
    Object.assign(row.style, { padding: "8px 10px", cursor: "pointer", textAlign: "left" });
    row.addEventListener("mouseenter", () => (row.style.background = "#f1f5ff"));
    row.addEventListener("mouseleave", () => (row.style.background = "#fff"));
    row.addEventListener("mousedown", (e) => {
      if (!e.isTrusted) return;
      e.preventDefault();
      onPick();
      removeMenu();
    });
    return row;
  }

  function showMenu(anchor, creds, aliases, codes) {
    removeMenu();
    aliases = aliases || [];
    if (!creds.length && !aliases.length || !anchor.isConnected || !visible(anchor)) return;
    const rect = anchor.getBoundingClientRect();
    const menu = document.createElement("div");
    menuHost = document.createElement("div");
    menuHost.id = "__applepw_menu";
    // Keep account names/codes and picker controls out of the page's DOM queries.
    const shadow = menuHost.attachShadow({ mode: "closed" });
    Object.assign(menu.style, {
      position: "fixed", zIndex: 2147483647,
      top: `${rect.bottom + 2}px`, left: `${rect.left}px`,
      minWidth: `${Math.max(rect.width, 220)}px`,
      maxHeight: `${Math.max(160, window.innerHeight - rect.bottom - 12)}px`,
      overflowY: "auto",
      background: "#fff", color: "#111", textAlign: "left",
      border: "1px solid #c7c7c7", borderRadius: "8px",
      boxShadow: "0 6px 24px rgba(0,0,0,.18)", font: "13px -apple-system,system-ui,sans-serif",
    });
    function sectionHeader(text, extraStyle) {
      const el = document.createElement("div");
      el.textContent = text;
      Object.assign(el.style, { padding: "6px 10px", color: "#666", fontSize: "11px" }, extraStyle);
      menu.appendChild(el);
    }

    if (creds.length) {
      sectionHeader(codes ? "認証コードを入力" : "iCloud パスワード",
                    { borderBottom: "1px solid #eee" });
    }
    for (const cred of creds) {
      const row = menuRow(() => (codes ? fillCode(cred, anchor) : fill(cred, anchor)));
      if (codes) {
        row.appendChild(codeLine(cred, {
          fontSize: "17px", letterSpacing: ".12em", fontVariantNumeric: "tabular-nums",
        }));
      }
      const website = cred.domain || cred.title || "";
      const name = document.createElement("div");
      if (cred.username && website) {
        const user = document.createElement("span");
        user.textContent = cred.username;
        const site = document.createElement("span");
        site.textContent = ` (${website})`;
        Object.assign(site.style, { fontSize: "11px", color: "#999" });
        name.appendChild(user);
        name.appendChild(site);
      } else {
        name.textContent = cred.username || website || "(no title)";
      }
      row.appendChild(name);
      if (codes) {
        menu.appendChild(row);
        continue;
      }
      if (cred.totp) {
        row.appendChild(codeLine(cred, { fontSize: "11px", color: "#0071e3" }));
      }
      // last_used is a real use time; mdat is only the record's write time.
      const rel = relTime(cred.last_used || cred.mdat);
      if (rel) row.appendChild(subLine(`Last used ${rel}`));
      menu.appendChild(row);
    }

    if (aliases.length) {
      sectionHeader("メールを非公開", { borderTop: creds.length ? "1px solid #eee" : "" });
      for (const alias of aliases) {
        const row = menuRow(() => fillAlias(alias, anchor));
        const name = document.createElement("div");
        name.textContent = alias.address;
        row.appendChild(name);
        if (alias.label) row.appendChild(subLine(alias.label));
        menu.appendChild(row);
      }
    }
    shadow.appendChild(menu);
    document.body.appendChild(menuHost);
    if (tickers.length) ticker = setInterval(() => tickers.forEach((paint) => paint()), 1000);
  }

  // A username field's typed value narrows the list; a password field (or empty value)
  // never filters.
  function fieldQuery(field) {
    if (!field || looksLikePassword(field) || isOtpField(field)) return "";
    return (field.value || "").trim().toLowerCase();
  }

  function filterCreds(creds, field) {
    const q = fieldQuery(field);
    if (!q) return creds;
    return creds.filter(
      (c) =>
        (c.username || "").toLowerCase().includes(q) ||
        (c.title || c.domain || "").toLowerCase().includes(q));
  }

  function filterAliases(aliases, field) {
    const q = fieldQuery(field);
    if (!q) return aliases;
    return aliases.filter(
      (a) =>
        (a.address || "").toLowerCase().includes(q) ||
        (a.label || "").toLowerCase().includes(q));
  }

  const EMPTY_MATCHES = { credentials: [], aliases: [] };

  function send(msg) {
    return new Promise((resolve) => {
      if (!chrome.runtime || !chrome.runtime.id) return resolve(null);
      chrome.runtime.sendMessage(msg, (resp) => resolve(chrome.runtime.lastError ? null : resp));
    });
  }

  let cache = null;
  let cacheAt = 0;
  let pending = null;
  function getMatches() {
    if (cache && Date.now() - cacheAt < 5000) return Promise.resolve(cache);
    if (pending) return pending;
    pending = send({ cmd: "match", domain: location.hostname }).then((resp) => {
      cache = resp && resp.ok
        ? { credentials: resp.credentials || [], aliases: resp.aliases || [], selected: resp.selected,
            autofill: resp.autofill !== false, assignedAlias: resp.assignedAlias }
        : EMPTY_MATCHES;
      cacheAt = Date.now();
      pending = null;
      return cache;
    });
    return pending;
  }

  function attach(field) {
    if (field.__applepw) return;
    field.__applepw = true;
    const open = () => getMatches().then(({ credentials, aliases }) => {
      if (focusedField !== field || !field.isConnected || !visible(field)) return;
      if (isOtpField(field)) return showMenu(field, credentials.filter((c) => c.totp), [], true);
      showMenu(field, filterCreds(credentials, field), filterAliases(aliases, field));
    });
    field.addEventListener("focus", () => {
      focusedField = field;
      send({ cmd: "focused" });
      open();
    });
    field.addEventListener("click", open);
    field.addEventListener("input", (event) => {
      if (field.__applepwFilled) { field.__applepwFilled = false; return; }
      if (event.isTrusted) edited.add(field);
      open();
    });
  }

  function hostname(value) {
    try { return new URL(value.includes("://") ? value : `https://${value}`).hostname.replace(/^www\./, "").toLowerCase(); }
    catch (_) { return ""; }
  }

  function chooseCredential(data, anchor) {
    const host = hostname(location.hostname);
    let candidates = data.credentials.filter(c => {
      const saved = hostname(c.domain || "");
      return saved && (host === saved || host.endsWith(`.${saved}`));
    });
    const exact = candidates.filter(c => hostname(c.domain) === host);
    if (exact.length) candidates = exact;
    const pw = passwordField(anchor);
    const user = looksLikeUsername(anchor) && !isOtpField(anchor) ? anchor :
      pw ? findUsernameField(pw) : within(anchor, USERNAME_SEL).find(f => looksLikeUsername(f) && !isOtpField(f) && f.value);
    if (user?.value) {
      candidates = candidates.filter(c => c.username.toLowerCase() === user.value.trim().toLowerCase());
    } else if (data.selected) {
      const selected = candidates.filter(c => c.username === data.selected.username && c.domain === data.selected.domain);
      if (selected.length === 1) return selected[0];
    }
    return candidates.length === 1 ? candidates[0] : null;
  }

  function automaticForm(field) {
    if (newPassword(field)) return false;
    if (field.form) {
      try { if (new URL(field.form.action || location.href, location.href).origin !== location.origin) return false; }
      catch (_) { return false; }
    }
    if (looksLikePassword(field) && field.autocomplete !== "current-password") {
      if (within(field, FIELD_SEL).filter(looksLikePassword).length > 1) return false;
    }
    return true;
  }

  let autoRunning = false;
  async function autoFill(fields) {
    if (autoRunning || document.hidden || !isSecureContext) return;
    const targets = fields.filter(f => canAuto(f) && automaticForm(f) &&
      (looksLikePassword(f) || looksLikeUsername(f) || isOtpField(f)));
    if (!targets.length) return;
    autoRunning = true;
    const page = location.href;
    try {
      const data = await getMatches();
      if (!data.autofill || page !== location.href) return;
      for (const field of targets) {
        if (!canAuto(field)) continue;
        const alias = data.aliases.length === 1 ? data.aliases[0] : null;
        if (alias && data.assignedAlias === alias.address && field.type === "email" && !passwordField(field)) {
          put(field, alias.address, true);
          continue;
        }
        const credential = chooseCredential(data, field);
        if (credential) await fill(credential, field, true);
        if (canAuto(field) && alias && !isOtpField(field) && looksLikeUsername(field)) put(field, alias.address, true);
      }
    } finally { autoRunning = false; }
  }

  function scan() {
    const fields = deepQueryAll(FIELD_SEL).filter(visible);
    const passwords = fields.filter(looksLikePassword);
    passwords.forEach(attach);
    fields.forEach((el) => {
      if (looksLikePassword(el)) return;
      if (isOtpField(el) || looksLikeUsername(el)) attach(el);
    });
    autoFill(fields).catch(() => {});
  }

  // deepQueryAll walks the whole tree (incl. shadow roots), so coalesce the many mutations a
  // page emits into one scan per frame instead of scanning per record.
  let scanScheduled = false;
  function scheduleScan() {
    if (scanScheduled) return;
    scanScheduled = true;
    requestAnimationFrame(() => { scanScheduled = false; scan(); });
  }

  const observer = new MutationObserver(records => {
    if (records.some(r => r.target !== menuHost)) scheduleScan();
  });

  document.addEventListener("click", (e) => {
    // composedPath crosses shadow boundaries; e.target is retargeted to the shadow host, which
    // would make clicks on shadow-DOM fields (Reddit) look like outside-clicks and close the menu.
    const path = e.composedPath ? e.composedPath() : [e.target];
    for (const t of path) {
      if (t && t.id === "__applepw_menu") return;
      if (t && t.__applepw) return;
    }
    removeMenu();
  });

  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg && msg.cmd === "fill") {
      removeMenu();
      const anchor = focusedField?.isConnected ? focusedField :
        deepQueryAll(FIELD_SEL).filter(visible).find(isOtpField) || null;
      fill(msg.credential, anchor).then(count => respond({ ok: count > 0,
        error: count ? "" : "入力欄が見つからないか、有効な認証コードを取得できません。" }));
      return true;
    }
    if (msg?.cmd === "fill_alias") {
      const count = fillAlias(msg.alias, focusedField?.isConnected ? focusedField : null);
      removeMenu();
      respond({ ok: count > 0, error: count ? "" : "メールアドレスの入力欄をクリックしてください。" });
    }
    return false;
  });

  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true,
    attributeFilter: ["type", "autocomplete", "disabled", "readonly", "hidden", "style", "class"] });
  chrome.storage?.onChanged?.addListener(() => { cache = null; scheduleScan(); });
  document.addEventListener("visibilitychange", scheduleScan);
  document.addEventListener("keydown", event => { if (event.key === "Escape") removeMenu(); });
  document.addEventListener("scroll", () => { removeMenu(); scheduleScan(); }, true);
  setInterval(() => { if (!document.hidden) scheduleScan(); }, 2000);
  scan();
})();
