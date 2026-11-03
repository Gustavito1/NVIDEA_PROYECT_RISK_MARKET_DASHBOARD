/* Informe NVDA — navegacion, charts y micro-interacciones */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("report-data").textContent);
  var GRID = "rgba(163,201,141,0.09)";
  var INK = "#e9f1e4";
  var MUTED = "#8fa088";
  var LIME = "#c8f06a";
  var GREEN = "#4fd98a";
  var RED = "#ff5f5f";
  var AMBER = "#ffb020";
  var baseLayout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "IBM Plex Mono, monospace", size: 10.5, color: MUTED },
    margin: { l: 56, r: 20, t: 26, b: 40 },
    xaxis: { gridcolor: GRID, zeroline: false, linecolor: GRID },
    yaxis: { gridcolor: GRID, zeroline: false, linecolor: GRID },
    legend: { orientation: "h", y: 1.12, x: 0, font: { size: 10 } },
    hovermode: "x unified",
    hoverlabel: { bgcolor: "#101610", bordercolor: "rgba(163,201,141,0.3)",
                  font: { family: "IBM Plex Mono, monospace", size: 11 } }
  };
  var config = { responsive: true, displaylogo: false,
                 modeBarButtonsToRemove: ["lasso2d", "select2d"] };
  var RANGE_BUTTONS = [["1D", "1d"], ["5D", "5d"], ["1M", "1m"], ["3M", "3m"],
                       ["6M", "6m"], ["1Y", "1y"], ["ALL", "all"]];

  function mount(id, traces, layout) {
    var el = document.getElementById(id);
    if (!el) return;
    if (!window.Plotly) {
      el.innerHTML = '<div class="chart-fallback" style="display:flex">' +
        "Grafico no disponible sin conexion (Plotly no cargado).</div>";
      return;
    }
    var lay = Object.assign({}, baseLayout, layout || {});
    if (layout && layout.yaxis) lay.yaxis = Object.assign({}, baseLayout.yaxis, layout.yaxis);
    if (layout && layout.xaxis) lay.xaxis = Object.assign({}, baseLayout.xaxis, layout.xaxis);
    var p = Plotly.newPlot(el, traces, lay, config);
    if (p && typeof p.then === "function") {
      p.then(function () { Plotly.Plots.resize(el); });
    } else {
      Plotly.Plots.resize(el);
    }
  }

  function signalMarkers(bt) {
    var bx = [], by = [], sx = [], sy = [];
    for (var i = 0; i < bt.dates.length; i++) {
      if (bt.signal[i] === "BUY") { bx.push(bt.dates[i]); by.push(bt.close[i]); }
      else if (bt.signal[i] === "SELL") { sx.push(bt.dates[i]); sy.push(bt.close[i]); }
    }
    return [
      { x: bx, y: by, type: "scatter", mode: "markers", name: "Senal BUY",
        marker: { color: GREEN, size: 9, symbol: "triangle-up", line: { width: 0 } } },
      { x: sx, y: sy, type: "scatter", mode: "markers", name: "Senal SELL",
        marker: { color: RED, size: 9, symbol: "triangle-down", line: { width: 0 } } }
    ];
  }

  function positionShapes(bt) {
    var shapes = [], prev = 0, start = 0;
    for (var i = 0; i <= bt.position_ls.length; i++) {
      var cur = i < bt.position_ls.length ? bt.position_ls[i] : 0;
      if (cur !== prev) {
        if (prev !== 0) {
          shapes.push({ type: "rect", xref: "x", yref: "paper",
            x0: bt.dates[start], x1: bt.dates[i - 1],
            y0: 0, y1: 1, line: { width: 0 }, layer: "below",
            fillcolor: prev > 0 ? "rgba(79,217,138,0.10)" : "rgba(255,95,95,0.10)" });
        }
        prev = cur; start = i;
      }
    }
    return shapes;
  }

  var renderers = {
    fin: function () {
      var e = DATA.equity;
      mount("chart-equity", [
        { x: e.dates, y: e.long, name: "Day trading long-only", type: "scatter",
          mode: "lines", line: { color: LIME, width: 1.8 } },
        { x: e.dates, y: e.ls, name: "Long/Short (hipotetica)", type: "scatter",
          mode: "lines", line: { color: AMBER, width: 1.1, dash: "dot" } },
        { x: e.dates, y: e.bh, name: "Buy & Hold NVDA", type: "scatter", mode: "lines",
          line: { color: MUTED, width: 1.1 } }
      ], { yaxis: { title: "Equity (base 1)" },
           xaxis: { rangeselector: { buttons: RANGE_BUTTONS,
                                    bgcolor: "rgba(255,255,255,0.04)",
                                    activecolor: "rgba(118,185,0,0.25)" } },
           height: 440 });
      mount("chart-drawdown", [
        { x: e.dates, y: e.drawdown, name: "Drawdown", type: "scatter", mode: "lines",
          fill: "tozeroy", line: { color: RED, width: 1.2 },
          fillcolor: "rgba(255,95,95,0.16)" }
      ], { yaxis: { title: "Drawdown", tickformat: ".0%" }, height: 260 });
    },
    descriptivo: function () {
      var c = DATA.candles;
      mount("chart-candles", [
        { x: c.dates, open: c.open, high: c.high, low: c.low, close: c.close,
          type: "candlestick", name: "NVDA 1H",
          increasing: { line: { color: GREEN, width: 1 }, fillcolor: "rgba(79,217,138,0.85)" },
          decreasing: { line: { color: RED, width: 1 }, fillcolor: "rgba(255,95,95,0.85)" },
          yaxis: "y" },
        { x: c.dates, y: c.sma20, name: "SMA 20", type: "scatter", mode: "lines",
          line: { color: "#7dd3fc", width: 1 } },
        { x: c.dates, y: c.sma50, name: "SMA 50", type: "scatter", mode: "lines",
          line: { color: "#d8b4fe", width: 1 } },
        { x: c.dates, y: c.vwap, name: "VWAP", type: "scatter", mode: "lines",
          line: { color: LIME, width: 1, dash: "dot" } },
        { x: c.dates, y: c.vol_ratio, type: "bar", name: "Volumen relativo",
          yaxis: "y2",
          marker: { color: c.up.map(function (u) {
            return u ? "rgba(79,217,138,0.45)" : "rgba(255,95,95,0.45)";
          }) } }
      ], {
        yaxis: { domain: [0.34, 1], title: "Precio (USD)" },
        yaxis2: { domain: [0, 0.22], title: "Vol. rel. (x media 20b)",
                  showgrid: false, zeroline: true, zerolinecolor: GRID },
        height: 560,
        xaxis: { rangeslider: { visible: false },
                 rangeselector: { buttons: RANGE_BUTTONS,
                                  bgcolor: "rgba(255,255,255,0.04)",
                                  activecolor: "rgba(118,185,0,0.25)" } }
      });
    },
    noticias: function () {
      var nb = DATA.news_bar;
      mount("chart-news", [
        { z: nb.vals, x: nb.cols, y: nb.rows, type: "heatmap",
          colorscale: [[0, "#0e130e"], [0.45, "#2f6b1f"], [1, "#76b900"]],
          showscale: false,
          text: nb.vals.map(function (row) {
            return row.map(function (v) { return v.toLocaleString(); });
          }),
          texttemplate: "%{text}", textfont: { size: 11, family: "IBM Plex Mono" },
          hovertemplate: "%{y} · impacto %{x}: %{z} noticias<extra></extra>" }
      ], { height: 300, margin: { l: 84, r: 24, t: 18, b: 44 },
           xaxis: { title: "Nivel de impacto" }, yaxis: { title: "" } });
      var sy = DATA.sent_year;
      var colors = sy.avg.map(function (v) { return v >= 0 ? GREEN : RED; });
      mount("chart-sent-year", [
        { x: sy.years.map(String), y: sy.avg, name: "Sentimiento promedio", type: "bar",
          marker: { color: colors },
          text: sy.avg.map(function (v) { return v.toFixed(3); }),
          textposition: "outside", textfont: { size: 9 } },
        { x: sy.years.map(String), y: sy.n, name: "Nº noticias", type: "scatter",
          mode: "lines+markers", yaxis: "y2",
          line: { color: MUTED, width: 1.2, dash: "dot" }, marker: { size: 5 } }
      ], { yaxis: { title: "Sentimiento medio (-1 a +1)", zeroline: true,
                    zerolinecolor: GRID },
           yaxis2: { title: "Nº noticias", overlaying: "y", side: "right",
                     showgrid: false, type: "log" },
           height: 320, margin: { l: 58, r: 64, t: 26, b: 38 } });
    },
    prescriptivo: function () {
      var b = DATA.bt7;
      if (!b.dates.length) return;
      var traces = [
        { x: b.dates, y: b.close, type: "scatter", mode: "lines", name: "Precio NVDA",
          line: { color: INK, width: 1.4 } }
      ].concat(signalMarkers(b)).concat([
        { x: b.dates, y: b.equity, type: "scatter", mode: "lines",
          name: "Equity long-only (base 1)", yaxis: "y2",
          line: { color: LIME, width: 1.3, dash: "dot" } }
      ]);
      mount("chart-bt7", traces, {
        shapes: positionShapes(b),
        yaxis: { title: "Precio (USD)", domain: [0.32, 1] },
        yaxis2: { title: "Equity (base 1)", overlaying: "y", side: "right",
                  showgrid: false, domain: [0, 0.24] },
        height: 380
      });
    },
    sox: function () {
      var sd = DATA.sox_daily, sr = DATA.sox_roll;
      mount("chart-sox", [
        { x: sd.dates, y: sd.nvda, name: "NVDA (normalizado)", type: "scatter",
          mode: "lines", line: { color: LIME, width: 1.6 } },
        { x: sd.dates, y: sd.sox, name: "SOXQ (normalizado)", type: "scatter",
          mode: "lines", line: { color: "#7dd3fc", width: 1.4 } }
      ], { yaxis: { title: "Base 1.0 (cierres diarios)" },
           xaxis: { rangeselector: { buttons: RANGE_BUTTONS,
                                    bgcolor: "rgba(255,255,255,0.04)",
                                    activecolor: "rgba(118,185,0,0.25)" } },
           height: 340 });
      mount("chart-beta", [
        { x: sr.dates, y: sr.beta, name: "Beta 60 barras", type: "scatter",
          mode: "lines", line: { color: AMBER, width: 1.4 } },
        { x: sr.dates, y: sr.corr, name: "Correlacion 60 barras", type: "scatter",
          mode: "lines", line: { color: "#7dd3fc", width: 1.2 } },
        { x: sr.dates, y: sr.ratio_z, name: "SOX ratio z", type: "scatter",
          mode: "lines", line: { color: MUTED, width: 1, dash: "dot" } }
      ], { yaxis: { title: "Valor" },
           xaxis: { rangeselector: { buttons: RANGE_BUTTONS,
                                    bgcolor: "rgba(255,255,255,0.04)",
                                    activecolor: "rgba(118,185,0,0.25)" } },
           height: 340 });
    }
  };

  var done = {};
  var reducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function activate(name) {
    document.querySelectorAll(".nav button").forEach(function (b) {
      var isActive = b.dataset.tab === name;
      b.classList.toggle("active", isActive);
      b.setAttribute("aria-selected", isActive ? "true" : "false");
      b.tabIndex = isActive ? 0 : -1; /* roving tabindex dentro del tablist */
    });
    document.querySelectorAll(".tab").forEach(function (t) {
      t.classList.toggle("active", t.id === "tab-" + name);
    });
    if (!done[name] && renderers[name]) { done[name] = true; renderers[name](); }
    window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
  }

  document.querySelectorAll(".nav button").forEach(function (b) {
    b.addEventListener("click", function () { activate(b.dataset.tab); });
  });
  document.addEventListener("keydown", function (ev) {
    /* Las flechas solo navegan pestañas cuando el foco esta en el tablist
       (no secuestran el scroll ni el teclado en tablas/graficos). */
    var nav = ev.target.closest && ev.target.closest(".nav");
    if (!nav) return;
    var order = Array.from(document.querySelectorAll(".nav button"));
    var idx = order.findIndex(function (b) { return b.classList.contains("active"); });
    if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
      activate(order[Math.min(idx + 1, order.length - 1)].dataset.tab); ev.preventDefault();
    } else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
      activate(order[Math.max(idx - 1, 0)].dataset.tab); ev.preventDefault();
    }
  });

  window.addEventListener("resize", function () {
    if (window.Plotly) document.querySelectorAll(".js-plotly-plot").forEach(function (el) {
      Plotly.Plots.resize(el);
    });
  });

  activate("fin");
})();
