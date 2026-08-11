/* SmartReco toasts + one-shot flash messages.
 * toast(message, type) is the shared UI used by cart.js and other scripts;
 * showFlash() reads the server-set smartreco_flash cookie and clears it.
 */
(function () {
  "use strict";

  var toastEl = null;

  function toast(message, type) {
    if (!message) return;
    type = type || "success";
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message;
    toastEl.className = "toast toast-" + type + " show";
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      toastEl.classList.remove("show");
    }, 3000);
  }

  function decodeFlashValue(payload) {
    // base64url -> standard base64, then decode UTF-8 bytes
    var b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    var raw = atob(b64);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return new TextDecoder("utf-8").decode(bytes);
  }

  function showFlash() {
    var parts = document.cookie.split(";");
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].split("=");
      if (kv[0].trim() !== "smartreco_flash") continue;
      var payload = kv.slice(1).join("=");
      // strip the server-side HMAC signature suffix (payload.signature)
      var dot = payload.lastIndexOf(".");
      if (dot > -1) payload = payload.slice(0, dot);
      try {
        var data = JSON.parse(decodeFlashValue(payload));
        if (data && data.message) toast(data.message, data.type || "success");
      } catch (e) { /* ignore malformed */ }
      // clear it so it shows only once
      document.cookie = "smartreco_flash=; Max-Age=0; path=/";
    }
  }

  window.SmartRecoToast = { toast: toast };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", showFlash);
  } else {
    showFlash();
  }
})();
