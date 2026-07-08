import {
  Chart, LineController, LineElement, PointElement,
  LinearScale, CategoryScale, Legend, Tooltip, Filler,
  type ChartDataset, type Plugin,
  type Chart as ChartType,
} from 'chart.js';
import type { SignalData, Band, StateMachine } from './types';
import { STATE_COLOR, hexAlpha } from './theme';

Chart.register(
  LineController, LineElement, PointElement,
  LinearScale, CategoryScale, Legend, Tooltip, Filler,
);

export type Range = '5D' | '1M' | '3M' | '6M' | 'YTD';

// ── Band overlay plugin ────────────────────────────────────────────────────

interface BandEntry { startIdx: number; endIdx: number; state: StateMachine }

function buildBandEntries(dates: string[], bands: Band[]): BandEntry[] {
  if (dates.length === 0) return [];
  const first = dates[0];
  const last  = dates[dates.length - 1];
  const entries: BandEntry[] = [];

  for (const b of bands) {
    const bandEnd = b.end ?? last;
    if (bandEnd < first || b.start > last) continue; // fully outside visible window

    const clampedStart = b.start < first ? first : b.start;
    const clampedEnd   = bandEnd  > last  ? last  : bandEnd;

    let si = dates.indexOf(clampedStart);
    if (si === -1) si = dates.findIndex(d => d >= clampedStart);
    if (si === -1) continue;

    let ei = dates.indexOf(clampedEnd);
    if (ei === -1) {
      for (let i = dates.length - 1; i >= 0; i--) {
        if (dates[i] <= clampedEnd) { ei = i; break; }
      }
    }
    if (ei === -1 || ei < si) continue;

    entries.push({ startIdx: si, endIdx: ei, state: b.state });
  }
  return entries;
}

function makeBandPlugin(entriesRef: { value: BandEntry[] }): Plugin<'line'> {
  return {
    id: 'regimeBands',
    beforeDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const { top, bottom } = chart.chartArea;
      const xScale = chart.scales['x'];
      ctx.save();
      for (const e of entriesRef.value) {
        const x1 = xScale.getPixelForValue(e.startIdx);
        const x2 = xScale.getPixelForValue(e.endIdx);
        ctx.fillStyle = hexAlpha(STATE_COLOR[e.state], 0.18);
        ctx.fillRect(x1, top, x2 - x1, bottom - top);
      }
      ctx.restore();
    },
  };
}

// ── Date-range helpers ─────────────────────────────────────────────────────

function getRangeStartIdx(dates: string[], range: Range): number {
  if (dates.length === 0) return 0;
  if (range === 'YTD') {
    const year = dates[dates.length - 1].slice(0, 4);
    const idx = dates.findIndex(d => d.startsWith(year));
    return idx === -1 ? 0 : idx;
  }
  const tradingDays: Record<string, number> = { '5D': 5, '1M': 21, '3M': 63, '6M': 126 };
  return Math.max(0, dates.length - (tradingDays[range] ?? dates.length));
}

// ── Label formatting: auto-adapt granularity to window size ───────────────

function sparseLabels(dates: string[]): string[] {
  const n = dates.length;
  if (n === 0) return [];
  if (n <= 7) return dates.map(d => d.slice(5));              // 5D: show MM-DD every day
  if (n <= 30) {
    const step = Math.ceil(n / 5);
    return dates.map((d, i) => (i % step === 0 ? d.slice(5) : ''));  // 1M: MM-DD ~5 labels
  }
  const step = Math.ceil(n / 12);
  return dates.map((d, i) => (i % step === 0 ? d.slice(0, 7) : '')); // 3M+: YYYY-MM
}

// ── Apply a date range to an existing chart ───────────────────────────────

function applyRange(
  chart: ChartType,
  allDates: string[],
  allSeries: (number | null)[][],
  entriesRef: { value: BandEntry[] },
  datesRef: { value: string[] },
  bands: Band[],
  range: Range,
  normalizeFirst = false,
): void {
  const startIdx = getRangeStartIdx(allDates, range);
  const sliced = allDates.slice(startIdx);

  entriesRef.value = buildBandEntries(sliced, bands);
  datesRef.value = sliced;
  chart.data.labels = sparseLabels(sliced);

  chart.data.datasets.forEach((ds, i) => {
    const raw = (allSeries[i] ?? []).slice(startIdx) as (number | null)[];
    if (normalizeFirst) {
      const base = raw.find(v => v !== null) as number | undefined;
      ds.data = (base != null ? raw.map(v => v !== null ? v / base : null) : raw) as number[];
    } else {
      ds.data = raw as number[];
    }
  });

  chart.update('none');
}

// ── Signal chart (id20 / on20) ─────────────────────────────────────────────

export function initSignalChart(data: SignalData): (range: Range) => void {
  const canvas = document.getElementById('signal-chart') as HTMLCanvasElement;
  const { dates, id20, on20 } = data.series;
  const asia_on20 = data.series.asia_on20 ?? [];

  const allSeries: (number | null)[][] = [
    id20 as number[],
    on20 as number[],
  ];
  if (asia_on20.some(v => v !== null)) {
    allSeries.push(asia_on20 as number[]);
  }

  const entriesRef = { value: [] as BandEntry[] };
  const datesRef   = { value: dates };

  const datasets: ChartDataset<'line'>[] = [
    {
      label: 'id20 (intraday stream)',
      data: [],
      borderColor: '#38bdf8',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    },
    {
      label: 'on20 (overnight stream)',
      data: [],
      borderColor: '#fb923c',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    },
  ];

  if (allSeries.length > 2) {
    datasets.push({
      label: 'asia_on20 (Asia overnight)',
      data: [],
      borderColor: '#a78bfa',
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.3,
    } as ChartDataset<'line'>);
  }

  const chart = new Chart(canvas, {
    type: 'line',
    plugins: [makeBandPlugin(entriesRef)],
    data: { labels: [], datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          ticks: { color: '#9ca3af', maxRotation: 0 },
          grid: { color: '#1f2937' },
        },
        y: {
          ticks: {
            color: '#9ca3af',
            callback: (v) => (Number(v) * 100).toFixed(0) + '%',
          },
          grid: { color: '#1f2937' },
        },
      },
      plugins: {
        legend: { labels: { color: '#d1d5db' } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y ?? 0;
              return (ctx.dataset.label ?? '') + ': ' + (v * 100).toFixed(2) + '%';
            },
            title: (items) => datesRef.value[items[0].dataIndex] ?? '',
          },
        },
      },
    },
  });

  return (range: Range) =>
    applyRange(chart, dates, allSeries, entriesRef, datesRef, data.bands, range);
}

// ── Equity chart ───────────────────────────────────────────────────────────

export function initEquityChart(data: SignalData): (range: Range) => void {
  const canvas = document.getElementById('equity-chart') as HTMLCanvasElement;
  const { dates, equity_strategy, equity_bh } = data.series;

  const allSeries: (number | null)[][] = [
    equity_strategy as number[],
    equity_bh as number[],
  ];

  const entriesRef = { value: [] as BandEntry[] };
  const datesRef   = { value: dates };

  const datasets: ChartDataset<'line'>[] = [
    {
      label: 'Strategy',
      data: [],
      borderColor: '#22c55e',
      backgroundColor: hexAlpha('#22c55e', 0.08),
      fill: true,
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.2,
    },
    {
      label: 'Buy & Hold',
      data: [],
      borderColor: '#6b7280',
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.2,
    },
  ];

  const chart = new Chart(canvas, {
    type: 'line',
    plugins: [makeBandPlugin(entriesRef)],
    data: { labels: [], datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          ticks: { color: '#9ca3af', maxRotation: 0 },
          grid: { color: '#1f2937' },
        },
        y: {
          ticks: {
            color: '#9ca3af',
            callback: (v) => ((Number(v) - 1) * 100).toFixed(0) + '%',
          },
          grid: { color: '#1f2937' },
        },
      },
      plugins: {
        legend: { labels: { color: '#d1d5db' } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y ?? 1;
              return (ctx.dataset.label ?? '') + ': ' + ((v - 1) * 100).toFixed(1) + '%';
            },
            title: (items) => datesRef.value[items[0].dataIndex] ?? '',
          },
        },
      },
    },
  });

  // normalizeFirst=true: each range view starts both series at 0% return
  return (range: Range) =>
    applyRange(chart, dates, allSeries, entriesRef, datesRef, data.bands, range, true);
}
