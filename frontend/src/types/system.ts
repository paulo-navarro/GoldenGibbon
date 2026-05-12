// ── System Types ─────────────────────────────────────────────────────────────
// Matches: api/routes/system.py → HealthResponse, LogsResponse

/** Health check response (GET /api/system/health). */
export interface HealthResponse {
  status: string;
  db: boolean;
  redis: boolean;
  celery_worker: boolean;
  celery_beat: boolean;
  environment: string;
  timestamp: string;
}

/** Log tail response (GET /api/system/logs). */
export interface LogsResponse {
  file: string;
  total_count: number;
  offset: number;
  limit: number;
  lines: string[];
}
