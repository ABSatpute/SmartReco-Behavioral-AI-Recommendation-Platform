/* SmartReco behavioral tracker — batched, throttled, non-blocking.
 * Captures: page_view, product_view, product_click, search, add_to_cart.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var baseUrl = ((script && script.getAttribute("data-app")) || "").replace(/\/+$/, "");
  var endpoint = baseUrl + "/api/events/batch";

  var BATCH_INTERVAL_MS = 5000;
  var BATCH_SIZE = 20;
  var THROTTLE_MS = 10000;

  var buffer = [];
  var lastSent = {};
  var flushTimer = null;

  function throttleKey(eventType, entityType, entityId) {
    return [eventType, entityType, entityId].join("|");
  }

  function throttled(key) {
    var now = Date.now();
    if (lastSent[key] && now - lastSent[key] < THROTTLE_MS) return true;
    lastSent[key] = now;
    return false;
  }

  function push(event) {
    if (throttled(throttleKey(event.event_type, event.entity_type, event.entity_id))) {
      return;
    }
    buffer.push({
      event_type: event.event_type,
      entity_type: event.entity_type || null,
      entity_id: event.entity_id != null ? String(event.entity_id) : null,
      payload: event.payload || {},
      occurred_at: new Date().toISOString(),
    });
    if (buffer.length >= BATCH_SIZE) flush();
  }

  function send(batch) {
    try {
      navigator.sendBeacon(endpoint, new Blob([JSON.stringify({ events: batch })], { type: "application/json" }));
      return;
    } catch (e) { /* fall through to fetch */ }
    try {
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      }).catch(function () { /* silent */ });
    } catch (e2) { /* silent */ }
  }

  function flush() {
    if (!buffer.length) return;
    send(buffer.splice(0, buffer.length));
  }

  function trackClick(el, eventType) {
    var id = el.getAttribute("data-product-id");
    var slug = el.getAttribute("data-product-slug");
    push({ event_type: eventType, entity_type: "product", entity_id: id || slug });
  }

  function bindEvents() {
    document.addEventListener("click", function (e) {
      var card = e.target.closest ? e.target.closest("[data-product-id][data-product-slug]") : null;
      if (card) { trackClick(card, "product_click"); return; }
      var cart = e.target.closest ? e.target.closest("[data-add-to-cart]") : null;
      if (cart) {
        push({ event_type: "add_to_cart", entity_type: "product", entity_id: cart.getAttribute("data-add-to-cart") });
      }
    });

    var searchForm = document.querySelector("form[action$='/search']");
    if (searchForm) {
      searchForm.addEventListener("submit", function () {
        var input = searchForm.querySelector("input[name='q']");
        var q = input ? input.value.trim() : "";
        if (q) push({ event_type: "search", entity_type: "query", entity_id: q });
      });
    }
  }

  function trackPageView() {
    var detail = document.querySelector(".product-detail[data-product-id]");
    if (detail && detail.dataset && detail.dataset.productId) {
      push({ event_type: "product_view", entity_type: "product", entity_id: detail.dataset.productId });
    } else {
      push({ event_type: "page_view", entity_type: "page", entity_id: window.location.pathname });
    }
  }

  function start() {
    if (flushTimer) return;
    flushTimer = setInterval(flush, BATCH_INTERVAL_MS);
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") flush();
    });
    bindEvents();
    trackPageView();
  }

  window.SmartRecoTracker = {
    track: push,
    flush: flush,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
