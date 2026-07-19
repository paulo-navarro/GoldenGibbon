// ── Chart-data sanitization (task 9.12) ─────────────────────────────────────
// Recharts misbehaves on NaN/Infinity (broken paths, axis-measure loops),
// and corrupted history has produced absurd values (e.g. a 10500% return
// from a degenerate backtest slice). Everything plotted goes through here.

/** Parse to a finite number, or null when the value is NaN/Infinity/garbage. */
export function finiteNumber(value: unknown): number | null {
  const n = typeof value === 'number' ? value : parseFloat(String(value));
  return Number.isFinite(n) ? n : null;
}

/**
 * Absolute percentage bound considered plausible for returns/PnL series.
 * Values beyond it are treated as data corruption, not performance.
 */
export const MAX_PLAUSIBLE_PERCENT = 1000;

/** True when the value is finite and within ±MAX_PLAUSIBLE_PERCENT. */
export function isPlausiblePercent(value: number): boolean {
  return Number.isFinite(value) && Math.abs(value) <= MAX_PLAUSIBLE_PERCENT;
}
