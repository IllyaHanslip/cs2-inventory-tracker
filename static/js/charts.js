// Chart.js line charts styled for the dark dashboard.
//
// Colours come from a validated categorical palette (dark-surface steps) and are
// tied to the *entity*, never to its position: the valuation line is always blue,
// Steam always orange, and so on, so toggling or filtering never repaints a series.

import { esc, longDate, money, shortDate } from './util.js';

const PALETTE = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9', '#e66767', '#008300'];
const FIXED_SLOTS = { valuation: 0, steam: 1, skinport: 2, csfloat: 3, marketcsgo: 4, waxpeer: 5 };
const FIRST_MANUAL_SLOT = 6;
const FOLD_COLOR = '#8a919c';
// A market that doesn't count under "Values from: Cash-out markets only" (Steam Wallet money).
const MUTED_COLOR = '#4d535e';

/** Stable colour per market key. Manual quote markets take the remaining slots in name order. */
export function seriesColor(key, manualKeys = []) {
  if (key in FIXED_SLOTS) return PALETTE[FIXED_SLOTS[key]];
  const index = manualKeys.indexOf(key);
  return index >= 0 && FIRST_MANUAL_SLOT + index < PALETTE.length ? PALETTE[FIRST_MANUAL_SLOT + index] : FOLD_COLOR;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Vertical crosshair that follows the hovered index.
const crosshair = {
  id: 'crosshair',
  afterDatasetsDraw(chart) {
    const active = chart.tooltip && chart.tooltip.getActiveElements();
    if (!active || !active.length) return;
    const { ctx, chartArea } = chart;
    const x = active[0].element.x;
    ctx.save();
    ctx.strokeStyle = cssVar('--axis');
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

export function chartsAvailable() {
  return typeof window.Chart === 'function';
}

/**
 * Render (or re-render) a line chart.
 * @param {Chart|null} existing  previous instance to destroy
 * @param {HTMLCanvasElement} canvas
 * @param {{labels: string[], series: {key: string, label: string, values: (number|null)[], fill?: boolean,
 *          muted?: boolean, leaders?: (string|null)[], leaderLabels?: object}[],
 *          currency: string, manualKeys?: string[]}} config
 *  muted: drawn grey (a market that doesn't count). leaders: for the best line, which market was
 *  best at each point - the line is then striped blue and that market's colour.
 */
export function renderLineChart(existing, canvas, { labels, series, currency, manualKeys = [] }) {
  if (existing) existing.destroy();
  if (!chartsAvailable()) return null;

  const surface = cssVar('--surface-1');
  const multi = series.length > 1;
  const peak = Math.max(0, ...series.flatMap((s) => s.values.filter((v) => v !== null)));
  const tickFormat = axisFormatter(currency, peak);
  const datasets = series.flatMap((s) => {
    const color = s.muted ? MUTED_COLOR : seriesColor(s.key, manualKeys);
    const line = {
      label: s.label,
      data: s.values,
      borderColor: color,
      backgroundColor: s.fill
        ? (context) => {
          const { chartArea, ctx } = context.chart;
          if (!chartArea) return 'transparent';
          const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          gradient.addColorStop(0, `${color}38`);
          gradient.addColorStop(1, `${color}00`);
          return gradient;
        }
        : color,
      fill: s.fill ? 'start' : false,
      borderWidth: s.key === 'valuation' && multi ? 2.5 : 2,
      pointRadius: 0,
      pointHitRadius: 12,
      pointHoverRadius: 4,
      pointHoverBorderWidth: 2,
      pointHoverBorderColor: surface,
      pointHoverBackgroundColor: color,
      tension: 0.2,
      spanGaps: true,
      borderDash: s.muted ? [4, 4] : [],
      leaders: s.leaders || null,
      leaderLabels: s.leaderLabels || {},
    };
    if (!s.leaders) return [line];
    // Stripes: the same line drawn again on top, dashed, each dash in the colour of the market
    // that was best at that point (so where the best market changes, the stripes change colour).
    const stripes = {
      ...line,
      backgroundColor: 'transparent',
      fill: false,
      borderColor: color,
      borderDash: [7, 7],
      segment: {
        borderColor: (ctx) => {
          const market = s.leaders[ctx.p0DataIndex];
          return market ? seriesColor(market, manualKeys) : 'rgba(0,0,0,0)';
        },
      },
      pointHitRadius: 0,
      pointHoverRadius: 0,
      stripeOverlay: true,
      order: -2,          // drawn on top of everything (lower order = drawn later)
    };
    line.order = -1;      // the best line itself above the market lines it follows
    return [line, stripes];
  });

  return new window.Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 4 } },
      plugins: {
        legend: {
          display: multi,
          position: 'top',
          align: 'start',
          labels: {
            color: cssVar('--text-secondary'),
            usePointStyle: true,
            pointStyle: 'line',
            boxWidth: 18,
            padding: 14,
            font: { size: 12 },
            filter: (item, data) => !data.datasets[item.datasetIndex].stripeOverlay,
          },
          // Clicking a line's name hides it (and the stripes drawn over it).
          onClick: (event, item, legend) => {
            const chart = legend.chart;
            const shown = chart.isDatasetVisible(item.datasetIndex);
            chart.data.datasets.forEach((d, i) => {
              if (i === item.datasetIndex || (d.stripeOverlay && i === item.datasetIndex + 1)) chart.setDatasetVisibility(i, !shown);
            });
            chart.update();
          },
        },
        tooltip: {
          backgroundColor: cssVar('--surface-3'),
          borderColor: 'rgba(255,255,255,0.12)',
          borderWidth: 1,
          titleColor: cssVar('--text-primary'),
          bodyColor: cssVar('--text-secondary'),
          padding: 10,
          boxPadding: 4,
          usePointStyle: true,
          filter: (item) => !item.dataset.stripeOverlay,
          callbacks: {
            title: (items) => longDate(items[0].label),
            label: (ctx) => {
              const leader = ctx.dataset.leaders?.[ctx.dataIndex];
              const on = leader ? ` (${ctx.dataset.leaderLabels[leader] || leader})` : '';
              return ` ${ctx.dataset.label}: ${money(ctx.parsed.y, currency)}${on}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: cssVar('--axis') },
          ticks: {
            color: cssVar('--text-muted'),
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 7,
            callback: (value, index) => shortDate(labels[index]),
          },
        },
        y: {
          grid: { color: cssVar('--grid'), drawTicks: false },
          border: { display: false },
          ticks: {
            color: cssVar('--text-muted'),
            maxTicksLimit: 5,
            padding: 8,
            callback: (value) => tickFormat(value),
          },
        },
      },
    },
    plugins: [crosshair],
  });
}

/** Axis labels precise enough that neighbouring ticks never read the same. */
function axisFormatter(currency, peak) {
  if (peak >= 100000) return (v) => money(v, currency, { compact: true });
  const digits = peak >= 100 ? 0 : peak >= 1 ? 2 : 3;
  let nf;
  try {
    nf = new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: digits, maximumFractionDigits: digits });
  } catch {
    nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: digits });
  }
  return (v) => nf.format(v);
}

/** Accessible table view of the same data, newest first. */
export function dataTableHtml(labels, series, currency, sortId = 'chart-data') {
  if (!labels.length) return '<p class="muted">No data yet.</p>';
  const head = series.map((s, i) => `<th class="num" data-sort="num" data-key="s${i}">${esc(s.label)}</th>`).join('');
  const rows = [];
  for (let i = labels.length - 1, n = 0; i >= 0; i -= 1, n += 1) {
    const cells = series.map((s) => `<td class="num" data-v="${s.values[i] ?? ''}">${money(s.values[i], currency)}</td>`).join('');
    rows.push(`<tr data-i="${n}"><td data-v="${esc(labels[i])}">${esc(longDate(labels[i]))}</td>${cells}</tr>`);
  }
  return `<table class="data" data-sort-id="${esc(sortId)}"><thead><tr><th data-sort="text" data-key="date">Date</th>${head}</tr></thead>
    <tbody>${rows.join('')}</tbody></table>`;
}

// Portfolio-mix groups: fixed order, so each keeps its colour whatever else is shown. The same
// validated dark-surface steps as the markets (checked with the dataviz validator: CVD dE >= 8.4
// between neighbours, contrast >= 3:1). "Other" uses the violet slot, not grey: grey next to the
// pink failed the colour-blindness check.
export const MIX_COLORS = {
  knives: PALETTE[0], skins: PALETTE[1], stickers: PALETTE[2], slabs: PALETTE[3], containers: PALETTE[4], other: PALETTE[5],
};

/** Stacked bars of how a total splits into groups over time. series: [{key, label, values}] */
export function renderStackedBars(existing, canvas, { labels, series, currency }) {
  if (existing) existing.destroy();
  if (!chartsAvailable()) return null;
  const surface = cssVar('--surface-1');
  const totals = labels.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] || 0), 0));
  const tickFormat = axisFormatter(currency, Math.max(0, ...totals));
  const datasets = series.map((s, index) => ({
    label: s.label,
    data: s.values,
    backgroundColor: MIX_COLORS[s.key] || FOLD_COLOR,
    borderColor: surface,                       // 2px surface gap between stacked segments
    borderWidth: { top: 2 },
    borderSkipped: 'bottom',
    // Only the top of each stack is rounded; inner joins stay square.
    borderRadius: index === series.length - 1 ? { topLeft: 4, topRight: 4 } : 0,
    maxBarThickness: 28,
    categoryPercentage: 0.9,
    barPercentage: 1,
  }));
  return new window.Chart(canvas, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 4 } },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'start',
          labels: { color: cssVar('--text-secondary'), boxWidth: 12, boxHeight: 12, padding: 12, font: { size: 12 } },
        },
        tooltip: {
          backgroundColor: cssVar('--surface-3'),
          borderColor: 'rgba(255,255,255,0.12)',
          borderWidth: 1,
          titleColor: cssVar('--text-primary'),
          bodyColor: cssVar('--text-secondary'),
          footerColor: cssVar('--text-primary'),
          padding: 10,
          boxPadding: 4,
          callbacks: {
            title: (items) => longDate(items[0].label),
            label: (ctx) => ` ${ctx.dataset.label}: ${money(ctx.parsed.y, currency)}`,
            footer: (items) => `Total: ${money(totals[items[0].dataIndex], currency)}`,
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { color: cssVar('--axis') },
          ticks: { color: cssVar('--text-muted'), maxRotation: 0, autoSkip: true, maxTicksLimit: 7,
            callback: (value, index) => shortDate(labels[index]) },
        },
        y: {
          stacked: true,
          grid: { color: cssVar('--grid'), drawTicks: false },
          border: { display: false },
          ticks: { color: cssVar('--text-muted'), maxTicksLimit: 5, padding: 8, callback: (value) => tickFormat(value) },
        },
      },
    },
  });
}
