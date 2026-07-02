import type { StateMachine, CheckStatus } from './types';

export const STATE_COLOR: Record<StateMachine, string> = {
  RISK_ON:  '#22c55e',
  MONITOR:  '#f59e0b',
  EXIT:      '#ef4444',
  ACCUM:    '#3b82f6',
  WARMUP:   '#6b7280',
};

export const STATE_LABEL: Record<StateMachine, string> = {
  RISK_ON:  'RISK ON',
  MONITOR:  'MONITOR',
  EXIT:      'EXIT',
  ACCUM:    'ACCUM',
  WARMUP:   'WARMUP',
};

export const STATUS_COLOR: Record<CheckStatus, string> = {
  green: '#22c55e',
  amber: '#f59e0b',
  red:   '#ef4444',
  gray:  '#6b7280',
};

export function hexAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function fmtPct(v: number, decimals = 1): string {
  return (v * 100).toFixed(decimals) + '%';
}

export function fmtPts(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + ' pts';
}

export function fmtDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

export function fmtDatetime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
}
