import {
  Chart, LineController, LineElement, PointElement,
  LinearScale, CategoryScale, Legend, Tooltip, Filler,
  type ChartDataset, type Plugin,
} from 'chart.js';
import type { SignalData, Band, StateMachine } from './types';
import { STATE_COLOR, hexAlpha } from './theme';

Chart.register(
  LineController, LineElement, PointElement,
  LinearScale, CategoryScale, Legend, Tooltip, Filler,
);

// ── Band overlay plugin ────────────────────────────────────────────────────

interface BandEntry { startIdx: number; endIdx: number; state: StateMachine }

function buildBandEntries(dates: string[], bands: Band[]): BandEntry[] {
  const idxOf = (d: string): number => {
    const i = dates.indexOf(d);
    return i === -1 ? 0 : i;
  };
  return bands.map(b => ({
    startIdx: idxOf(b.start),
    endIdx:   b.end ? Math.min(idxOf(b.end), dates.length - 1) : dates.length - 1,
    state:    b.state,
  }));
}

function makeBandPlugin(entries: BandEntry[]): Plugin<'line'> {
  return {
    id: 'regimeBands',
    beforeDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const { top, bottom } = chart.chartArea;
      const xScale = chart.scales['x'];
      ctx.save();
      for (const e of entries) {
        const x1 = xScale.getPixelForValue(e.startIdx);
        const x2 = xScale.getPixelForValue(e.endIdx);
        ctx.fillStyle = hexAlpha(STATE_COLOR[e.state], 0.18);
        ctx.fillRect(x1, top, x2 - x1, bottom - top);
      }
      ctx.restore();
    },
  };
}

// ── Thin the date labels so they don't overlap ─────────────────────────────

function sparseLabels(dates: string[], maxLabels = 12): string[] {
  const step = Math.ceil(dates.length / maxLabels);
  return dates.map((d, i) => (i % step === 0 ? d.slice(0, 7) : ''));
}

// ── Signal chart (id20 / on20) ─────────────────────────────────────────────

export function initSignalChart(data: SignalData): void {
  const canvas = document.getElementById('signal-chart') as HTMLCanvasElement;
  const { dates, id20, on20 } = data.series;
  const asia_on20 = data.series.asia_on20 ?? [];
  const entries = buildBandEntries(dates, data.bands);

  const datasets: ChartDataset<'line'>[] = [
    {
      label: 'id20 (intraday stream)',
      data: id20 as number[],
      borderColor: '#38bdf8',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    },
    {
      label: 'on20 (overnight stream)',
      data: on20 as number[],
      borderColor: '#fb923c',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    },
  ];

  // Optional asia_on20 overlay — only added when TSM/EWY data is available
  if (asia_on20.some(v => v !== null)) {
    datasets.push({
      label: 'asia_on20 (Asia overnight)',
      data: asia_on20 as number[],
      borderColor: '#a78bfa',
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.3,
    } as ChartDataset<'line'>);
  }

  new Chart(canvas, {
    type: 'line',
    plugins: [makeBandPlugin(entries)],
    data: {
      labels: sparseLabels(dates),
      datasets,
    },
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
            title: (items) => dates[items[0].dataIndex] ?? '',
          },
        },
      },
    },
  });
}

// ── Equity chart ───────────────────────────────────────────────────────────

export function initEquityChart(data: SignalData): void {
  const canvas = document.getElementById('equity-chart') as HTMLCanvasElement;
  const { dates, equity_strategy, equity_bh } = data.series;
  const entries = buildBandEntries(dates, data.bands);

  const datasets: ChartDataset<'line'>[] = [
    {
      label: 'Strategy',
      data: equity_strategy as number[],
      borderColor: '#22c55e',
      backgroundColor: hexAlpha('#22c55e', 0.08),
      fill: true,
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.2,
    },
    {
      label: 'Buy & Hold',
      data: equity_bh as number[],
      borderColor: '#6b7280',
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.2,
    },
  ];

  new Chart(canvas, {
    type: 'line',
    plugins: [makeBandPlugin(entries)],
    data: {
      labels: sparseLabels(dates),
      datasets,
    },
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
            title: (items) => dates[items[0].dataIndex] ?? '',
          },
        },
      },
    },
  });
}
