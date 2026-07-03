import type { SignalData } from './types';
import { STATE_COLOR, STATE_LABEL, STATE_DARK_TEXT, fmtDate, fmtPct } from './theme';

function setText(id: string, text: string): HTMLElement {
  const el = document.getElementById(id)!;
  el.textContent = text;
  return el;
}

export function renderStatePanel(data: SignalData): void {
  const state = data.state;
  const color = STATE_COLOR[state.machine];
  const label = STATE_LABEL[state.machine];

  const badge = document.getElementById('state-badge')!;
  badge.textContent = label;
  badge.style.backgroundColor = color;
  badge.style.color = STATE_DARK_TEXT.has(state.machine) ? '#000' : '#fff';

  setText('last-session', 'Session: ' + fmtDate(data.last_session));

  const nameEl = setText('state-name', label);
  nameEl.style.color = color;

  setText('state-since', 'Since ' + fmtDate(state.since));

  const posPct = Math.round(state.suggested_size * 100);
  const posEl = setText('position-size', posPct + '% position');
  posEl.style.color = posPct > 0 ? STATE_COLOR.RISK_ON : STATE_COLOR.EXIT;

  setText('today-price', '$' + data.today.close.toFixed(2));

  const ret = data.today.ret;
  const dayRetEl = setText('day-return', (ret >= 0 ? '+' : '') + fmtPct(ret, 2) + ' today');
  dayRetEl.style.color = ret >= 0 ? STATE_COLOR.RISK_ON : STATE_COLOR.EXIT;

  if (data.state.accum_overlay) {
    document.getElementById('accum-overlay')!.style.display = '';
  }
  if (data.data_stale) {
    document.getElementById('stale-warning')!.style.display = '';
  }

  // "Modes split" badge: shown when Mode A and Mode B disagree
  const splitBanner = document.getElementById('modes-split-banner');
  if (splitBanner) {
    const a = data.state.arm_mode_a ?? false;
    const b = data.state.arm_mode_b ?? false;
    if (a !== b) {
      splitBanner.textContent = a
        ? 'Intraday selling is negative (Mode A), but not unusually extreme vs. the past year (Mode B inactive) — lower conviction'
        : 'Intraday selling is historically extreme vs. the past year (Mode B), but hasn\'t crossed the absolute zero line (Mode A inactive) — watch closely';
      splitBanner.style.display = '';
    } else {
      splitBanner.style.display = 'none';
    }
  }

  // Short-permission indicator
  renderShortPermission(data);
}

function renderShortPermission(data: SignalData): void {
  const container = document.getElementById('short-permission');
  if (!container) return;

  const sp = data.state.short_permission;
  if (!sp) {
    container.style.display = 'none';
    return;
  }

  container.style.display = '';
  const conditions: Array<{ key: keyof typeof sp; label: string }> = [
    { key: 'ma50_vol', label: 'Close < 50-DMA (vol > 1.5×)' },
    { key: 'id20_neg', label: 'id20 < 0' },
    { key: 'on20_neg', label: 'on20 < 0' },
  ];

  let html = '<div class="short-perm-label">Short conditions</div><div class="short-perm-grid">';
  for (const c of conditions) {
    const pass = sp[c.key as keyof typeof sp];
    const icon = pass ? '✓' : '✗';
    const cls = pass ? 'short-cond pass' : 'short-cond fail';
    html += `<span class="${cls}">${icon} ${c.label}</span>`;
  }
  html += '</div>';

  if (sp.all) {
    html += '<div class="short-perm-all">ALL MET — short permitted per ruleset</div>';
  } else {
    // Show nearest actionable info
    const today = data.today;
    if (today.ma50 != null && today.close != null && today.close >= today.ma50) {
      const gap = ((today.close - today.ma50) / today.close * 100).toFixed(1);
      html += `<div class="short-perm-gap">Nearest line: MA50 $${today.ma50.toFixed(0)} (${gap}% above close)</div>`;
    }
  }

  container.innerHTML = html;
}

export function renderChecklist(data: SignalData): void {
  const container = document.getElementById('checklist')!;
  container.textContent = '';

  for (const item of data.checklist) {
    let display: string;
    if (item.value === null) {
      display = 'n/a';
    } else if (item.fmt === 'pct0') {
      display = fmtPct(item.value, 0);
    } else if (item.fmt === 'pct1') {
      display = fmtPct(item.value, 1);
    } else if (item.fmt === 'z') {
      display = (item.value >= 0 ? '+' : '') + item.value.toFixed(2) + 'σ';
    } else if (item.fmt === 'bp') {
      // basis points: value in percentage-point units (e.g. 0.15 = 15bp)
      const bp = Math.round(item.value * 100);
      display = (bp >= 0 ? '+' : '') + bp + 'bp';
    } else {
      display = (item.value >= 0 ? '+' : '') + item.value.toFixed(2);
    }

    const statusColor = item.status === 'green' ? '#22c55e'
      : item.status === 'amber' ? '#f59e0b'
      : item.status === 'red' ? '#ef4444'
      : '#6b7280';

    const row = document.createElement('div');
    row.className = 'checklist-row';

    const dot = document.createElement('span');
    dot.className = 'check-dot';
    dot.style.background = statusColor;

    const lbl = document.createElement('span');
    lbl.className = 'check-label';
    lbl.textContent = item.label;

    const val = document.createElement('span');
    val.className = 'check-value';
    val.style.color = statusColor;
    val.textContent = display;

    const note = document.createElement('span');
    note.className = 'check-note';
    note.textContent = item.note;

    row.appendChild(dot);
    row.appendChild(lbl);
    row.appendChild(val);
    row.appendChild(note);
    container.appendChild(row);
  }

  renderMetricRows(data);
}

function renderMetricRows(data: SignalData): void {
  const grid = document.getElementById('metric-grid')!;
  grid.textContent = '';
  const today = data.today;

  const vixSlopeStr = today.vix_slope != null
    ? (today.vix_slope >= 0 ? '+' : '') + today.vix_slope.toFixed(2) + ' (VIX3M−VIX)'
    : 'n/a';

  const metrics: Array<[string, string]> = [
    ['RSI 14',      today.rsi14.toFixed(1)],
    ['MA20',        '$' + today.ma20.toFixed(0)],
    ['MA50',        '$' + today.ma50.toFixed(0)],
    ['RV20',        fmtPct(today.rv20, 0)],
    ['RV20 p90',    today.rv20_p90 != null ? fmtPct(today.rv20_p90, 0) : 'n/a'],
    ['Turb',        today.turb.toFixed(2)],
    ['Dist-20',     today.dist20 + 'd'],
    ['VIX Slope',   vixSlopeStr],
  ];

  for (const [k, v] of metrics) {
    const item = document.createElement('div');
    item.className = 'metric-item';

    const key = document.createElement('span');
    key.className = 'metric-key';
    key.textContent = k;

    const valEl = document.createElement('span');
    valEl.className = 'metric-val';
    valEl.textContent = v;

    item.appendChild(key);
    item.appendChild(valEl);
    grid.appendChild(item);
  }
}

export function renderTradesTable(data: SignalData): void {
  const tbody = document.getElementById('trades-tbody')!;
  tbody.textContent = '';

  const history = data.history ?? [];

  if (history.length === 0) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 4;
    td.textContent = 'No history this year';
    td.style.cssText = 'text-align:center;color:#6b7280';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const entry of [...history].reverse()) {
    const color = STATE_COLOR[entry.state];
    const label = STATE_LABEL[entry.state];
    const tr = document.createElement('tr');

    const dateTd = document.createElement('td');
    dateTd.textContent = fmtDate(entry.date);
    tr.appendChild(dateTd);

    const stateTd = document.createElement('td');
    const chip = document.createElement('span');
    chip.textContent = label;
    chip.style.cssText = [
      `background:${color}`,
      `color:${STATE_DARK_TEXT.has(entry.state) ? '#000' : '#fff'}`,
      'font-size:0.75rem',
      'font-weight:700',
      'padding:0.15em 0.55em',
      'border-radius:4px',
      'letter-spacing:0.04em',
      'white-space:nowrap',
    ].join(';');
    stateTd.appendChild(chip);
    tr.appendChild(stateTd);

    const priceTd = document.createElement('td');
    priceTd.textContent = entry.price != null ? '$' + entry.price.toFixed(2) : '—';
    tr.appendChild(priceTd);

    const reasonTd = document.createElement('td');
    reasonTd.textContent = entry.reason;
    tr.appendChild(reasonTd);

    tbody.appendChild(tr);
  }
}
