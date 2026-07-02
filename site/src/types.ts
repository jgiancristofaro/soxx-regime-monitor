export type StateMachine = 'RISK_ON' | 'MONITOR' | 'EXIT' | 'ACCUM' | 'WARMUP';
export type TradeAction = 'EXIT' | 'REENTER';
export type CheckStatus = 'green' | 'amber' | 'red' | 'gray';

export interface StateInfo {
  machine: StateMachine;
  accum_overlay: boolean;
  since: string;
  position_multiplier: number;
  suggested_size: number;
  short_permitted: boolean;
}

export interface TodaySignals {
  close: number;
  ret: number;
  id20: number;
  on20: number;
  ret20: number;
  ma20: number;
  ma50: number;
  ma200: number;
  rv10: number;
  rv20: number;
  turb: number;
  ar1: number;
  rsi14: number;
  dist20: number;
  vrp: number;
  iv30_asof: string;
}

export interface Band {
  state: StateMachine;
  start: string;
  end: string | null;
}

export interface Trade {
  date: string;
  price: number;
  action: TradeAction;
  reason: string;
}

export interface HistoryEntry {
  date: string;
  state: StateMachine;
  price: number | null;
  reason: string;
}

export interface ChecklistItem {
  id: string;
  label: string;
  value: number;
  fmt: string;
  status: CheckStatus;
  note: string;
}

export interface Series {
  dates: string[];
  close: (number | null)[];
  ma20: (number | null)[];
  id20: (number | null)[];
  on20: (number | null)[];
  rv20: (number | null)[];
  equity_strategy: (number | null)[];
  equity_bh: (number | null)[];
  smh_close?: (number | null)[];
}

export interface Event {
  date: string;
  label: string;
  type: string;
}

export interface SignalData {
  last_session: string;
  data_stale: boolean;
  state: StateInfo;
  today: TodaySignals;
  bands: Band[];
  trades: Trade[];
  history: HistoryEntry[];
  checklist: ChecklistItem[];
  series: Series;
  events: Event[];
  generated_utc: string;
}
