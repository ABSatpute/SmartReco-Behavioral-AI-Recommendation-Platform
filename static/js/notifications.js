/* SmartReco "picks are ready" notification.
 * Watches for a NEW recommendation being generated in the background (behaviour-
 * triggered, gated by the agent trigger policy) and nudges the user to view it:
 *   - a pulsing badge on the header bell, and
 *   - a slide-in "concierge" card with the recommendation headline + CTA.
 * Anti-fatigue: each recommendation notifies at most once (tracked per user in
 * localStorage by recommendation id), never on a cold page load, and never
 * while the user is already on the recommendations page.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var baseUrl = ((script && script.getAttribute("data-app")) || "").replace(/\/+$/, "");
  var userId = (script && script.getAttribute("data-user-id")) || "";
  if (!userId) return;

  var SEEN_KEY = "smartreco_seen_rec_" + userId;
  var POLL_MS = 20000;

  function seenId() {
    try { return parseInt(localStorage.getItem(SEEN_KEY) || "0", 10) || 0; }
    catch (e) { return 0; }
  }
  function markSeen(id) {
    try { localStorage.setItem(SEEN_KEY, String(id)); } catch (e) { /* ignore */ }
  }

  function onRecommendationsPage() {
    return !!document.querySelector("[data-reco-page]");
  }

  function setBadge(recId) {
    var badge = document.querySelector("[data-reco-badge]");
    if (!badge) return;
    badge.textContent = "1";
    badge.hidden = false;
    badge.setAttribute("data-rec-id", String(recId));
  }
  function clearBadge() {
    var badge = document.querySelector("[data-reco-badge]");
    if (badge) badge.hidden = true;
  }
  function pulseBell() {
    var bell = document.querySelector("[data-reco-bell]");
    if (bell) bell.classList.add("has-new");
  }

  function showCard(data) {
    // build the concierge card once, reuse it
    var card = document.getElementById("reco-card");
    if (!card) {
      card = document.createElement("aside");
      card.id = "reco-card";
      card.className = "reco-card";
      card.setAttribute("role", "status");
      card.innerHTML =
        '<span class="reco-card-tag">Your picks are ready</span>' +
        '<p class="reco-card-summary"></p>' +
        '<span class="reco-card-fresh">Fresh — refreshed as you keep browsing</span>' +
        '<div class="reco-card-actions">' +
        '  <a class="btn btn-primary reco-card-cta" href="/recommendations">View my picks</a>' +
        '  <button type="button" class="btn-link reco-card-close">Not now</button>' +
        "</div>";
      document.body.appendChild(card);
      card.querySelector(".reco-card-cta").addEventListener("click", function () {
        markSeen(data.rec_id);
        clearBadge();
      });
      card.querySelector(".reco-card-close").addEventListener("click", function () {
        card.classList.remove("show");
        markSeen(data.rec_id);
        clearBadge();
      });
    }
    card.querySelector(".reco-card-summary").textContent =
      data.summary || "A fresh set of picks built from your recent browsing.";
    requestAnimationFrame(function () { card.classList.add("show"); });
  }

  var firstPoll = true;   // first successful status check of this page view
  var sawEmpty = false;   // we've seen a state with no recommendation yet

  function poll() {
    fetch(baseUrl + "/api/recommendations/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var wasFirst = firstPoll;
        firstPoll = false;
        if (!data || !data.ready || !data.rec_id) {
          sawEmpty = true; // remember the baseline: no picks yet
          return;
        }
        var current = seenId();
        // Cold load with a pre-existing recommendation: seed the "seen" marker
        // so we never spam someone who just landed. (If we already observed an
        // empty state, a recommendation appearing now IS new.)
        if (current === 0 && !sawEmpty) { markSeen(data.rec_id); return; }
        if (data.rec_id === current) return;

        markSeen(data.rec_id);
        if (wasFirst) {
          // Returning user with an unviewed recommendation on load: show the
          // passive badge only — the in-moment card is for live arrivals.
          setBadge(data.rec_id);
          pulseBell();
          return;
        }
        if (onRecommendationsPage()) return;
        setBadge(data.rec_id);
        pulseBell();
        showCard(data);
      })
      .catch(function () { /* silent — never let notifications break browsing */ });
  }

  // Seed the badge on load for returning users with an unseen recommendation.
  function seedBadge() {
    if (seenId() === 0 || onRecommendationsPage()) return;
    fetch(baseUrl + "/api/recommendations/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.ready && data.rec_id > seenId()) {
          setBadge(data.rec_id);
          pulseBell();
        }
      })
      .catch(function () { /* silent */ });
  }

  if (onRecommendationsPage()) clearBadge();
  seedBadge();
  poll();
  setInterval(poll, POLL_MS);
})();