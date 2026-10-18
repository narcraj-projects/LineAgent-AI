import type { Catalog, Diagnosis, Evaluation, ExplorerGraph, FeedbackEvent, Mode, Receipt, Role, ServiceStatus, SupervisorReport } from './types';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120_000);
  try {
    const response = await fetch(`/api${path}`, { ...options, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...options.headers } });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The request failed validation.');
    return body as T;
  } catch (error) {
    if (error instanceof TypeError) throw new Error('Cannot reach the local API. Start the FastAPI server and try again.');
    throw error;
  } finally { clearTimeout(timeout); }
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const api = {
  catalog: () => request<Catalog>('/catalog'), status: () => request<ServiceStatus>('/status'),
  evaluation: () => request<Evaluation>('/evaluation'), supervisor: () => request<SupervisorReport>('/supervisor'),
  explore: (alarm: string) => request<ExplorerGraph>(`/graph/explore?alarm_code=${encodeURIComponent(alarm)}`),
  connect: (graph_backend: string) => post<ServiceStatus>('/connection', { graph_backend }),
  disconnect: () => request<ServiceStatus>('/connection', { method: 'DELETE' }),
  diagnose: (query: string, alarm_code: string, mode: Mode) => post<Diagnosis>('/diagnosis', { query, alarm_code: alarm_code || null, mode }),
  prepare: (alarm_code: string, fix_id: string, outcome: string, role: Role) => post<FeedbackEvent>('/feedback/prepare', { alarm_code, fix_id, outcome, role }),
  feedback: (event: FeedbackEvent, role: Role) => post<Receipt>('/feedback', { role, event_json: JSON.stringify(event) }),
  sync: (role: Role) => post<{ receipts: Receipt[] }>('/graph/retry', { role }),
};
