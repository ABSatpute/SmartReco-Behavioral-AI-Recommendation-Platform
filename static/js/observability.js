/* SmartReco agent observability — lightweight CSS charts rendered from the
 * JSON snapshot embedded in #observability-data. No external libraries.
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
    if (n === null || n === undefined || isNaN(Number(n))) return "\u2014";
    return Number(n).toLocaleString("en-US");
  }

  function emptyState(container) {
    container.textContent = "";
    container.appendChild(el("div", "chart-empty", "No data yet"));
  }

  function barChart(container, labels, counts) {
    counts = counts || [];
    if (!counts.length) { emptyState(container); return; }
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
    wrap.appendChild(el("div", "chart-tip", "peak " + fmtInt(max)));
    container.appendChild(wrap);
  }

  function stackedChart(container, labels, ok, fail) {
    ok = ok || [];
    fail = fail || [];
    if (!ok.length && !fail.length) { emptyState(container); return; }
    var max = Math.max.apply(null, ok.concat(fail).concat([1]));
    var wrap = el("div", "hbar-wrap");
    labels.forEach(function (l, i) {
      var col = el("div", "hbar-col");
      var stack = el("div", "hbar-stack");
      var okSeg = el("div", "hbar hbar-ok");
      okSeg.title = l + ": " + (ok[i] || 0) + " ok";
      okSeg.style.height = Math.max(0, (ok[i] / max) * 100) + "%";
      var failSeg = el("div", "hbar hbar-fail");
      failSeg.title = l + ": " + (fail[i] || 0) + " failed";
      failSeg.style.height = Math.max(0, (fail[i] / max) * 100) + "%";
      stack.appendChild(okSeg);
      stack.appendChild(failSeg);
      col.appendChild(stack);
      col.appendChild(el("div", "hbar-label", i % 2 === 0 || i === labels.length - 1 ? l : ""));
      wrap.appendChild(col);
    });
    container.textContent = "";
    wrap.appendChild(el("div", "bar-legend", "\u25A0 ok  \u25A0 failed"));
    container.appendChild(wrap);
  }

  function donutChart(container, items) {
    var parts = (items || []).filter(function (it) { return it.value > 0; });
    if (!parts.length) { emptyState(container); return; }
    var total = parts.reduce(function (s, it) { return s + it.value; }, 0);
    var segments = [];
    var start = 0;
    parts.forEach(function (it, i) {
      var from = i === 0 ? 0 : (start / total) * 100;
      var to = ((start + it.value) / total) * 100;
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
    if (!list.length) { emptyState(container); return; }
    var max = Math.max.apply(null, list.map(function (it) { return it.value; }).concat([1]));
    container.textContent = "";
    list.forEach(function (it) {
      var rowEl = el("div", "hrow");
      var label = el("div", "hrow-label", it.name);
      label.title = it.name;
      var track = el("div", "hrow-track");
      var fill = el("div", "hrow-fill");
      fill.style.width = Math.max(2, (it.value / max) * 100) + "%";
      fill.title = fmt ? "$" + Number(it.value).toFixed(4) : fmtInt(it.value);
      track.appendChild(fill);
      var val = el("div", "hrow-value", fmt ? "$" + Number(it.value).toFixed(4) : fmtInt(it.value));
      rowEl.appendChild(label);
      rowEl.appendChild(track);
      rowEl.appendChild(val);
      container.appendChild(rowEl);
    });
  }

  function renderInsights(container, items) {
    if (!container) return;
    container.textContent = "";
    var list = items || [];
    if (!list.length) {
      container.appendChild(el("li", "", "No runs in this window to summarize."));
      return;
    }
    list.forEach(function (text) {
      container.appendChild(el("li", "", text));
    });
  }

  function renderAll(data) {
    if (!data) return;
    var charts = {
      runsSeries: function (c) { stackedChart(c, data.series.runs.labels, data.series.runs.ok, data.series.runs.fail); },
      tokensSeries: function (c) { barChart(c, data.series.tokens.labels, data.series.tokens.counts); },
      costSeries: function (c) { barChart(c, data.series.cost.labels, data.series.cost.counts); },
      nodeLatency: function (c) { hbarChart(c, data.nodes.latency); },
      nodeTokens: function (c) { hbarChart(c, data.nodes.tokens); },
      nodeFailures: function (c) { hbarChart(c, data.nodes.failures); },
      errorMix: function (c) { hbarChart(c, data.error_mix); },
      triggerMix: function (c) { donutChart(c, data.trigger_mix); },
      eventMix: function (c) { donutChart(c, data.event_mix); }
    };
    document.querySelectorAll("[data-chart]").forEach(function (node) {
      var fn = charts[node.getAttribute("data-chart")];
      if (fn) fn(node);
    });
    renderInsights(document.querySelector("[data-insights]"), data.insights);
  }

  var raw = document.getElementById("observability-data");
  if (raw) {
    try {
      renderAll(JSON.parse(raw.textContent));
    } catch (e) {
      renderAll(null);
    }
  }
})();