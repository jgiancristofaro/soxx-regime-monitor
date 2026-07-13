import './styles.css';
import type { SignalData } from './types';
import { renderStatePanel, renderChecklist, renderTradesTable } from './state';
import { initSignalChart, initEquityChart, type Range } from './charts';
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

  const setSignalRange = initSignalChart(data);
  const setEquityRange = initEquityChart(data);
  setSignalRange('YTD');
  setEquityRange('YTD');

  const btns = document.querySelectorAll<HTMLButtonElement>('#range-btns .range-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const range = (btn.dataset.range ?? 'YTD') as Range;
      setSignalRange(range);
      setEquityRange(range);
    });
  });

  renderTradesTable(data);

  const footerEl = document.getElementById('last-run')!;
  footerEl.textContent = 'Updated ' + fmtDatetime(data.generated_utc);

  const methodLink = document.getElementById('method-link') as HTMLAnchorElement;
  methodLink.href = './methodology.html';

  await loadPreview(data.last_session);
}

interface PreviewData {
  date: string;
  snapshot_et: string;
  late: boolean;
  skipped: string | null;
  projected_state: string | null;
  projected_action: string;
  action_class: string;
  moc_eligible: boolean;
  settled?: boolean;
  settled_action?: string;
}

async function loadPreview(lastSession: string): Promise<void> {
  let preview: PreviewData;
  try {
    const res = await fetch('./data/preview.json');
    if (!res.ok) return;
    preview = await res.json() as PreviewData;
  } catch {
    return;
  }
  renderPreviewBanner(preview, lastSession);
}

function renderPreviewBanner(preview: PreviewData, lastSession: string): void {
  const el = document.getElementById('preview-banner')!;
  el.className = 'preview-banner';
  el.style.display = 'none';
  el.textContent = '';

  if (!preview.date) return;

  const isActive = preview.date > lastSession && !preview.settled;
  const isSettledDiff = preview.date === lastSession && preview.settled &&
    preview.settled_action !== preview.projected_action;

  function bold(text: string): HTMLElement {
    const s = document.createElement('strong');
    s.textContent = text;
    return s;
  }
  function text(t: string): Text { return document.createTextNode(t); }

  if (isActive) {
    el.appendChild(bold(`${preview.late ? 'LATE ' : ''}PREVIEW`));
    el.appendChild(text(` — ${preview.snapshot_et} ET snapshot. Projected action: `));
    el.appendChild(bold(preview.projected_action));
    el.appendChild(text(` (${preview.action_class}).`));
    if (preview.moc_eligible) el.appendChild(text(' MOC-eligible.'));
    if (preview.late) {
      el.appendChild(text(' '));
      el.appendChild(bold('[LATE — MOC window closed; next-open protocol]'));
    }
    el.appendChild(text(' Settles ~6pm ET. Not a recorded signal.'));
    el.classList.add(preview.late ? 'preview-late' : 'preview-active');
    el.style.display = '';
  } else if (isSettledDiff) {
    el.appendChild(text(`Preview at ${preview.snapshot_et} projected `));
    el.appendChild(bold(preview.projected_action));
    el.appendChild(text('; settled close produced '));
    el.appendChild(bold(preview.settled_action ?? 'NONE'));
    el.appendChild(text('. Preview log updated.'));
    el.classList.add('preview-settled-diff');
    el.style.display = '';
  }
}

init().catch(console.error);
