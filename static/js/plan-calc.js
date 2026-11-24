// The Sell Planner's arithmetic: which items to sell, how many and where, to raise an amount of cash.
// Worked out in the page from /api/sell-candidates (backend/planner.py), so filters, markets, the
// undercut and the amount all answer instantly - and the live demo can plan too.
//
// Every copy you could sell gets a *keep score* - how much you'd rather hold on to it:
//   * trend: falling prices (after fees) lower it, rising ones raise it;
//   * stack: copies from a big stack (531 capsules) score lower than a one-off;
//   * value: expensive single items score higher, so they're the last to go.
// Copies are sold lowest score first until the amount is reached. Each item's copies go to its markets
// best price first, each market taking only about what it sells in 30 days, so later copies of the same
// item may fetch less. Near the end, cheap copies cover the last few pounds instead of selling something
// expensive that overshoots. Every copy is listed a little under the market's price (the undercut).
// Part of every item can be kept for later, in case its price goes up (``holdBack``, a share): the plan
// first sells only the rest of each item, spreading the amount over more of them, and touches what's
// kept back only when the amount can't be reached otherwise. (A single item can't be split, so it
// counts as kept back.)

/** Higher = keep. Falling items, big stacks and cheap copies score low, so they're sold first. */
export function keepScore(weekly, stack, unitValue) {
  const trend = weekly == null ? 1 : Math.min(Math.max(1 + weekly / 10, 0.4), 2.5);
  const stackFactor = 1 / (1 + Math.log10(Math.max(stack, 1)));
  const valueFactor = 1 + 0.25 * Math.log10(1 + Math.max(unitValue, 0));
  return trend * stackFactor * valueFactor;
}

/** Selling ``quantity`` copies: markets best price first, each taking about what it sells in 30 days
 *  (no sales figures: any number); if every market's sales are used up, the rest go at the lowest price. */
export function bulkPlan(options, quantity) {
  let remaining = quantity;
  const steps = [];
  for (const option of options) {
    if (remaining <= 0) break;
    const take = option.capacity == null ? remaining : Math.min(remaining, option.capacity);
    if (take > 0) {
      steps.push({ ...option, units: take });
      remaining -= take;
    }
  }
  if (remaining > 0 && options.length) {
    steps.push({ ...options[options.length - 1], units: remaining, capacity: null, overflow: true });
  }
  return steps;
}

/**
 * Everything that doesn't depend on the amount, for the rows that pass the item filters.
 *   data      /api/sell-candidates
 *   rows      the rows to plan with (already filtered)
 *   markets   Set of market keys you'll sell on
 *   undercut  one of data.undercuts (%)
 *   keep      Set of row ids never to sell
 * Returns { copies (in selling order), lots, max, excluded: {kept, locked, no_price} }.
 */
export function prepare(data, rows, { markets, undercut, keep = new Set(), holdBack = 0 }) {
  const at = Math.max(0, data.undercuts.indexOf(undercut));
  const labels = new Map(data.markets.map((m) => [m.key, m.label]));
  // Trends from cash-out markets only, unless you'll also sell where money can't be withdrawn.
  const cashOnly = !data.markets.some((m) => !m.cash_out && markets.has(m.key));
  const excluded = { kept: 0, locked: 0, no_price: 0 };
  const lots = new Map();
  for (const row of rows) {
    if (keep.has(row.id)) { excluded.kept += 1; continue; }
    if (row.locked) { excluded.locked += 1; continue; }
    const lot = data.lots[row.lot];
    let entry = lots.get(row.lot);
    if (!entry) {
      const options = (lot?.options || [])
        .filter((o) => markets.has(o.market) && o.prices[at])
        .map((o) => ({ market: o.market, label: labels.get(o.market) || o.market, url: o.url, wallet: o.wallet,
          capacity: o.capacity, listPrice: o.prices[at][0], net: o.prices[at][1] }))
        .sort((a, b) => b.net - a.net);
      entry = { key: row.lot, name: lot?.name || '', icon: lot?.icon || null,
        weekly: (cashOnly ? lot?.weekly_cash : lot?.weekly) ?? null, options, ids: [], units: 0 };
      lots.set(row.lot, entry);
    }
    if (!entry.options.length) { excluded.no_price += 1; continue; }
    entry.ids.push(row.id);
    entry.units += row.quantity;
  }
  const copies = [];
  let max = 0;
  for (const lot of lots.values()) {
    if (!lot.units) continue;
    lot.steps = bulkPlan(lot.options, lot.units);
    // One score per item (from its best price), so its copies go best-paying market first.
    lot.score = lot.steps.length ? keepScore(lot.weekly, lot.units, lot.steps[0].net) : Infinity;
    // How many may go before what's kept for later: "keep half" of 9 keeps 5 and sells 4 at first.
    lot.first = lot.units - Math.ceil(Math.min(Math.max(holdBack, 0), 1) * lot.units);
    let position = 0;
    for (const step of lot.steps) {
      for (let i = 0; i < step.units; i += 1) {
        copies.push({ score: lot.score, position, lot, step, kept: position >= lot.first ? 1 : 0 });
        max += step.net;
        position += 1;
      }
    }
  }
  // Every item's share before anything kept for later; then lowest keep score first (and an item's
  // best-paying copies before its later ones).
  copies.sort((a, b) => a.kept - b.kept || a.score - b.score || a.position - b.position);
  return { copies, lots, max, excluded };
}

/** The plan for ``target``: { steps, by_market, achieved, max, excluded }. */
export function choose(prepared, target) {
  const chosen = [];
  const later = [];
  let total = 0;
  for (const copy of prepared.copies) {
    if (total >= target) break;
    const gap = target - total;
    const net = copy.step.net;
    if (net > gap * 1.5 && net - gap > 1.0) { later.push(copy); continue; }   // would overshoot a lot
    chosen.push(copy);
    total += net;
  }
  if (total < target && later.length) {
    // Still short: the copy that covers the rest with the least left over, keep score breaking ties.
    const gap = target - total;
    const covering = later.filter((c) => c.step.net >= gap);
    // (Not kept for later beats kept, then the smallest that covers it, then the lowest keep score.)
    const better = (a, b) => b.kept < a.kept || (b.kept === a.kept && (b.step.net < a.step.net
      || (b.step.net === a.step.net && b.score < a.score)));
    const pick = covering.length
      ? covering.reduce((a, b) => (better(a, b) ? b : a))
      : later.reduce((a, b) => (b.step.net > a.step.net ? b : a));
    chosen.push(pick);
    total += pick.step.net;
    if (total < target) {   // every copy is needed
      for (const copy of [...later].sort((a, b) => a.kept - b.kept || a.score - b.score || a.position - b.position)) {
        if (copy === pick || total >= target) continue;
        chosen.push(copy);
        total += copy.step.net;
      }
    }
  }

  // Group the chosen copies: per item, how many on which market.
  const steps = new Map();
  const markets = new Map();
  for (const { lot, step, score } of chosen) {
    if (!steps.has(lot.key)) {
      steps.set(lot.key, { name: lot.name, ids: lot.ids, icon: lot.icon, units: 0, held: lot.units, proceeds: 0,
        markets: new Map(), score, weekly: lot.weekly, first: lot.first });
    }
    const entry = steps.get(lot.key);
    entry.units += 1;
    entry.proceeds += step.net;
    if (!entry.markets.has(step.market)) {
      entry.markets.set(step.market, { market: step.market, label: step.label, units: 0, net: step.net,
        capacity: step.capacity, listPrice: step.listPrice, url: step.url, wallet: step.wallet });
    }
    entry.markets.get(step.market).units += 1;
    if (!markets.has(step.market)) markets.set(step.market, { market: step.market, label: step.label, units: 0, proceeds: 0 });
    const bucket = markets.get(step.market);
    bucket.units += 1;
    bucket.proceeds += step.net;
  }
  const list = [...steps.values()].map((s) => {
    const out = { ...s, markets: [...s.markets.values()] };
    out.reasons = reasons(out);
    return out;
  }).sort((a, b) => a.score - b.score);
  return {
    steps: list,
    by_market: [...markets.values()].sort((a, b) => b.proceeds - a.proceeds),
    achieved: total,
    max: prepared.max,
    excluded: prepared.excluded,
  };
}

/** Short reasons, one per line, all shaped the same way: what it is, then any figures in brackets. */
export function reasons(step) {
  const out = [];
  const { weekly } = step;
  if (weekly == null) out.push('trend unknown (not enough price history)');
  else if (weekly <= -1) out.push(`falling (about ${Math.abs(weekly).toFixed(0)}% a week)`);
  else if (weekly < 1) out.push('steady (under 1% a week)');
  else if (weekly < 3) out.push(`rising slowly (about ${weekly.toFixed(0)}% a week)`);
  else {
    out.push(`rising (about ${weekly.toFixed(0)}% a week)`);
    out.push('sold only to reach the target');
  }
  const n = (v) => v.toLocaleString('en-GB');
  if (step.held > 1 && step.units < step.held) {
    out.push(`you have ${n(step.held)} (selling ${n(step.units)}, keeping ${n(step.held - step.units)})`);
  } else if (step.held > 1 && step.first != null && step.first < step.held) {
    out.push(`you have ${n(step.held)} (selling all of them: needed to reach the amount)`);
  } else if (step.held >= 5) {
    out.push(`you have ${n(step.held)}`);
  }
  const quick = step.markets.filter((m) => m.capacity && m.capacity >= m.units);
  if (quick.length) out.push(`sells steadily on ${quick[0].label}`);
  return out;
}
