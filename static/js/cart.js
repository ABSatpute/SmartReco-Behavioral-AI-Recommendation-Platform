/* SmartReco cart — live badge, add-to-cart, qty steppers, remove, checkout.
 * The header badge is populated from /api/cart on every page load.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var baseUrl = ((script && script.getAttribute("data-app")) || "").replace(/\/+$/, "");

  function api(path, method, body) {
    var opts = { method: method, credentials: "same-origin", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(baseUrl + path, opts).then(function (r) { return r.json(); });
  }

  var toastEl = null;
  function toast(message, type) {
    var fn = (window.SmartRecoToast && window.SmartRecoToast.toast) || null;
    if (fn) { fn(message, type); return; }
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message;
    toastEl.className = "toast toast-" + (type || "success") + " show";
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () { toastEl.classList.remove("show"); }, 3000);
  }

  function setBadge(count) {
    var badges = document.querySelectorAll("[data-cart-count]");
    for (var i = 0; i < badges.length; i++) badges[i].textContent = count;
  }

  function refreshBadge() {
    api("/api/cart", "GET").then(function (data) {
      if (data && data.ok) setBadge(data.count || 0);
    }).catch(function () { /* silent */ });
  }

  function addToCart(productId, qty) {
    api("/api/cart/add", "POST", { product_id: Number(productId), quantity: qty || 1 })
      .then(function (data) {
        if (data && data.ok) {
          setBadge(data.count);
          toast("Item added to cart successfully", "success");
        } else if (data && data.error) {
          toast(data.error, "error");
        }
      })
      .catch(function () { toast("Could not add item to cart", "error"); });
  }

  function updateQty(productId, delta) {
    var line = document.querySelector('[data-cart-line="' + productId + '"]');
    var valueEl = document.querySelector('[data-qty-value="' + productId + '"]');
    var current = valueEl ? parseInt(valueEl.textContent, 10) : 1;
    var next = current + delta;
    if (next < 1) return;
    api("/api/cart/update", "POST", { product_id: Number(productId), quantity: next })
      .then(function (data) {
        if (data && data.ok) { setBadge(data.count); window.location.reload(); }
      })
      .catch(function () { /* silent */ });
  }

  function removeItem(productId) {
    api("/api/cart/remove", "POST", { product_id: Number(productId) })
      .then(function (data) {
        if (data && data.ok) { setBadge(data.count); window.location.reload(); }
      })
      .catch(function () { /* silent */ });
  }

  function checkout() {
    api("/api/cart/checkout", "POST")
      .then(function (data) {
        if (data && data.ok) { toast("Order placed — thanks for shopping!"); window.location.reload(); }
      })
      .catch(function () { toast("Checkout failed"); });
  }

  function bind() {
    document.addEventListener("click", function (e) {
      var addBtn = e.target.closest ? e.target.closest("[data-add-to-cart]") : null;
      if (addBtn) { addToCart(addBtn.getAttribute("data-add-to-cart")); return; }

      var chg = e.target.closest ? e.target.closest("[data-cart-change]") : null;
      if (chg) {
        updateQty(chg.getAttribute("data-product-id"), parseInt(chg.getAttribute("data-cart-change"), 10));
        return;
      }

      var rm = e.target.closest ? e.target.closest("[data-cart-remove]") : null;
      if (rm) { removeItem(rm.getAttribute("data-cart-remove")); return; }

      var co = e.target.closest ? e.target.closest("[data-cart-checkout]") : null;
      if (co) { checkout(); return; }
    });
  }

  bind();
  refreshBadge();
})();
