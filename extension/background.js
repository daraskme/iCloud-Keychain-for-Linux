const HOST = "org.icp.native";

function nativeRequest(payload) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage(HOST, payload, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response || { ok: false, error: "no response" });
        }
      });
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.cmd === "match") {
    nativeRequest({ cmd: "match", domain: message.domain }).then(sendResponse);
    return true; // async
  }
  if (message && message.cmd === "ping") {
    nativeRequest({ cmd: "ping" }).then(sendResponse);
    return true;
  }
  sendResponse({ ok: false, error: "unknown message" });
  return false;
});
