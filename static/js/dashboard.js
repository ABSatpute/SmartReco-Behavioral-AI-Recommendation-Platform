/* SmartReco admin dashboard — lightweight SVG-free charts rendered from JSON,
 * refreshed every 20s from /admin/api/dashboard. No external chart library.
 */
(function () {
  "use strict";

  var PALETTE = ["#4f46e5", "#06b6d4", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#64748b"];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmtInt(n) {
    if (n === null || n === undefined) return "\u2014";
    return Number(n).toLocaleString("en-US");
  }

  function fmtVal(n, format) {
    if (n === null || n === undefined) return "\u2014";
    if (format === "pct") return n + "%";
    if (format === "decimal") return Number(n).toLocaleString("en-US");
    return fmtInt(n);
  }

  function resolve(path, data) {
    return path.split(".").reduce(function (o, k) {
      return o == null ? o : o[k];
    }, data);
  }

  function emptyState(container) {
    container.textContent = "";
    container.appendChild(el("div", "chart-empty", "No data yet"));
  }

  function barChart(container, labels, counts) {
    var max = Math.max.apply(null, counts.concat([1]));
    var wrap = el("div", "hbar-wrap");
    counts.forEach(function (c, i) {
      var col = el("div", "hbar-col");
      var bar = el("div", "hbar");
      bar.title = labels[i] + ": " + c;
      bar.style.height = Math.max(2, (c / max) * 100) + "%";
      col.appendChild(bar);
      var lbl = el("div", "hbar-label", i % 2 === 0 || i === counts.length - 1 ? labels[i] : "");
      col.appendChild(lbl);
      wrap.appendChild(col);
    });
    container.textContent = "";
    wrap.appendChild(el("div", "chart-tip", "peak " + max));
    container.appendChild(wrap);
  }

  function stackedChart(container, labels, ok, fail, skip) {
    skip = skip || [];
    if (!ok.length && !fail.length && !skip.length) { emptyState(container); return; }
    var max = Math.max.apply(null, ok.concat(fail).concat(skip).concat([1]));
    var wrap = el("div", "hbar-wrap");
    labels.forEach(function (l, i) {
      var col = el("div", "hbar-col");
      var stack = el("div", "hbar-stack");
      var okHeight = Math.max(0, (ok[i] / max) * 100);
      var failHeight = Math.max(0, (fail[i] / max) * 100);
      var skipHeight = Math.max(0, (skip[i] / max) * 100);
      var okSeg = el("div", "hbar hbar-ok");
      okSeg.title = l + ": " + ok[i] + " ok";
      okSeg.style.height = okHeight + "%";
      var skipSeg = el("div", "hbar hbar-skip");
      skipSeg.title = l + ": " + (skip[i] || 0) + " skipped";
      skipSeg.style.height = skipHeight + "%";
      var failSeg = el("div", "hbar hbar-fail");
      failSeg.title = l + ": " + fail[i] + " failed";
      failSeg.style.height = failHeight + "%";
      stack.appendChild(okSeg);
      stack.appendChild(skipSeg);
      stack.appendChild(failSeg);
      col.appendChild(stack);
      col.appendChild(el("div", "hbar-label", i % 2 === 0 || i === labels.length - 1 ? l : ""));
      wrap.appendChild(col);
    });
    container.textContent = "";
    wrap.appendChild(el("div", "bar-legend", "\u25A0 ok  \u25A0 skipped  \u25A0 failed"));
    container.appendChild(wrap);
  }

  function donutChart(container, items) {
    var parts = (items || []).filter(function (it) { return it.value > 0; });
    var total = parts.reduce(function (s, it) { return s + it.value; }, 0);
    if (total === 0) { emptyState(container); return; }

    var segments = [];
    var start = 0;
    parts.forEach(function (it, i) {
      var from = i === 0 ? 0 : start / total * 100;
      var to = (start + it.value) / total * 100;
      segments.push(PALETTE[i % PALETTE.length] + " " + from + "% " + to + "%");
      start += it.value;
    });

    var row = el("div", "donut-row");
    var donut = el("div", "donut");
    donut.style.background = "conic-gradient(" + segments.join(",") + ")";
    var hole = el("div", "donut-hole");
    hole.textContent = fmtInt(total);
    donut.appendChild(hole);
    row.appendChild(donut);

    var legend = el("ul", "donut-legend");
    parts.forEach(function (it, i) {
      var li = el("li");
      var dot = el("span", "dot");
      dot.style.background = PALETTE[i % PALETTE.length];
      li.appendChild(dot);
      li.appendChild(el("span", "", it.name));
      li.appendChild(el("span", "donut-val", fmtInt(it.value)));
      legend.appendChild(li);
    });
    row.appendChild(legend);
    container.textContent = "";
    container.appendChild(row);
  }

  function hbarChart(container, items, fmt) {
    var list = items || [];
    if (list.length === 0) { emptyState(container); return; }
    var max = Math.max.apply(null, list.map(function (it) { return it.value; }).concat([1]));
    container.textContent = "";
    list.forEach(function (it) {
      var rowEl = el("div", "hrow");
      var label = el("div", "hrow-label", it.name);
      label.title = it.name;
      var track = el("div", "hrow-track");
      var fill = el("div", "hrow-fill");
      fill.style.width = Math.max(2, (it.value / max) * 100) + "%";
      fill.title = it.value;
      track.appendChild(fill);
      var val = el("div", "hrow-value", fmt ? fmtVal(it.value, fmt) : fmtInt(it.value));
      rowEl.appendChild(label);
      rowEl.appendChild(track);
      rowEl.appendChild(val);
      container.appendChild(rowEl);
    });
  }

  var HOUR_LABELS = [];
  for (var h = 0; h < 24; h++) HOUR_LABELS.push(String(h).padStart(2, "0") + "h");

  function renderAll(data) {
    document.querySelectorAll("[data-kpi]").forEach(function (node) {
      var v = resolve(node.getAttribute("data-kpi"), data);
      var target = node.querySelector(".kpi-value") || node;
      target.textContent = fmtVal(v, node.getAttribute("data-format"));
    });
    document.querySelectorAll("[data-sub-path]").forEach(function (node) {
      var v = resolve(node.getAttribute("data-sub-path"), data);
      if (v === null || v === undefined) { node.style.display = "none"; return; }
      node.style.display = "";
      node.textContent =
        (node.getAttribute("data-sub-prefix") || "") +
        fmtVal(v, node.getAttribute("data-sub-format") || "int") +
        (node.getAttribute("data-sub-suffix") || "");
    });

    if (data && data.series && data.mixes) {
      var charts = {
        eventsSeries: function (c) { barChart(c, data.series.events.labels, data.series.events.counts); },
        usersSeries: function (c) { barChart(c, data.series.users.labels, data.series.users.counts); },
        recoSeries: function (c) { barChart(c, data.series.reco.labels, data.series.reco.counts); },
        runsSeries: function (c) { stackedChart(c, data.series.runs.labels, data.series.runs.ok, data.series.runs.fail, data.series.runs.skip); },
        eventMix: function (c) { donutChart(c, data.mixes.event_mix); },
        hourHist: function (c) { barChart(c, HOUR_LABELS, data.mixes.hour_hist); },
        topCatsViewed: function (c) { hbarChart(c, data.mixes.top_cats_viewed); },
        topProducts: function (c) { hbarChart(c, data.mixes.top_products); },
        catalogCats: function (c) { hbarChart(c, data.mixes.catalog_cats); }
      };

      document.querySelectorAll("[data-chart]").forEach(function (node) {
        var fn = charts[node.getAttribute("data-chart")];
        if (fn) fn(node);
      });
    }

    var insights = document.querySelector("[data-insights]");
    if (insights) {
      insights.textContent = "";
      var list = data.insights || [];
      if (list.length === 0) {
        insights.appendChild(el("li", "", "Insufficient data for insights yet \u2014 keep browsing!"));
      }
      list.forEach(function (text) {
        insights.appendChild(el("li", "", text));
      });
    }
  }

  function refresh() {
    fetch("/admin/api/dashboard", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderAll(data);
        var stamp = document.querySelector("[data-updated]");
        if (stamp) stamp.textContent = new Date().toLocaleTimeString();
      })
      .catch(function () { /* keep last snapshot on transient errors */ });
  }

  var raw = document.getElementById("dashboard-data");
  if (raw) {
    try {
      renderAll(JSON.parse(raw.textContent));
    } catch (e) {
      renderAll({});
    }
    setInterval(refresh, 20000);
  }
})();