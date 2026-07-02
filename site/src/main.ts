import './styles.css';
import type { SignalData } from './types';
import { renderStatePanel, renderChecklist, renderTradesTable } from './state';
import { initSignalChart, initEquityChart } from './charts';
import { fmtDatetime } from './theme';

async function init(): Promise<void> {
  let data: SignalData;
  try {
    const res = await fetch('./data/signals.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json() as SignalData;
  } catch (err) {
    const el = document.getElementById('load-error')!;
    el.textContent = 'Failed to load signal data. ' + String(err);
    el.style.display = '';
    return;
  }

  renderStatePanel(data);
  renderChecklist(data);
  initSignalChart(data);
  initEquityChart(data);
  renderTradesTable(data);

  const footerEl = document.getElementById('last-run')!;
  footerEl.textContent = 'Updated ' + fmtDatetime(data.generated_utc);

  const methodLink = document.getElementById('method-link') as HTMLAnchorElement;
  methodLink.href = './methodology.html';
}

init().catch(console.error);
