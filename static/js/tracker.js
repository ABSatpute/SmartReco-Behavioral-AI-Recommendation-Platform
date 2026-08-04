/* SmartReco behavioral tracker — batched, throttled, non-blocking. */
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

  function flush() {
    if (!buffer.length) return;
    var batch = buffer.splice(0, buffer.length);
    try {
      navigator.sendBeacon(endpoint, new Blob([JSON.stringify({ events: batch })], { type: "application/json" }));
    } catch (e) {
      try {
        fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ events: batch }),
          keepalive: true,
        }).catch(function () { /* silent */ });
      } catch (e2) { /* silent */ }
    }
  }

  function start() {
    if (flushTimer) return;
    flushTimer = setInterval(flush, BATCH_INTERVAL_MS);
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") flush();
    });
  }

  window.SmartRecoTracker = {
    track: push,
    flush: flush,
  };

  start();
})();
