// Explicit API doubles for UI state tests. These are not live integration results.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { api } from './api';
import type { Catalog, Diagnosis, Evaluation, FeedbackEvent, ServiceStatus } from './types';

vi.mock('./api', () => ({ api: { catalog: vi.fn(), status: vi.fn(), evaluation: vi.fn(), supervisor: vi.fn(),
  connect: vi.fn(), disconnect: vi.fn(), diagnose: vi.fn(), prepare: vi.fn(), feedback: vi.fn(), sync: vi.fn() } }));
const connected: ServiceStatus = { connected: true, graph_provider: 'networkx', graph_mode: 'fallback',
  vector_namespace: 'unit-double-not-live', pdf_mode: true, demo_auth: true, synthetic: true };
const disconnected = { ...connected, connected: false };
const catalog: Catalog = { alarms: [{ alarm_id: 'SYN-ALARM-001', code: 'SYN-ALM-001', description: 'Fictional alarm', equipment_id: 'SYN-EQ-001', failure_mode_id: 'SYN-FM-001' }],
  equipment: [{ equipment_id: 'SYN-EQ-001', equipment_type: 'Fictional filler', line_id: 'SYN-LINE-001' }],
  manuals: [{ section_id: 'SYN-SEC-001', source_doc: 'SYN-DOC-001', source_version: '1.0', page: 1, heading: 'Synthetic fixture cause', text: 'Explicit unit test text' }],
  pdf_files: [{ filename: 'linemedic-synthetic-manual-1.pdf', pages: 18, sha256: 'a'.repeat(64) }],
  citations: [{ section_id: 'SYN-SEC-001', filename: 'linemedic-synthetic-manual-1.pdf', pdf_page: 1, source_doc: 'SYN-DOC-001', source_version: '1.0', logical_page: 1 }],
  counts: { alarms: 20, equipment: 10, manual_sections: 30, pdf_files: 2, baseline_work_orders: 50 }, snapshot_label: 'Unit test double' };
const diagnosis: Diagnosis = { run: { decision: { route: 'combined', reason: 'Unit fixture', alarm_code: 'SYN-ALM-001', equipment_id: 'SYN-EQ-001' },
  response: { status: 'supported', message: 'Synthetic fixture only', candidates: [{ fix_id: 'SYN-FIX-001', cause: 'Fictional photoeye offset', part_number: 'SYN-PART-001', evidence_source_ids: ['SYN-SEC-001'], graph_path: ['SYN-ALARM-001', 'SYN-FM-001', 'SYN-FIX-001', 'SYN-PART-001'], outcomes: { worked: 2, did_not_work: 1 }, safety_warning: 'Synthetic only' }],
  evidence: [{ source_id: 'SYN-SEC-001', text: 'Explicit unit fixture text', source_kind: 'manual', provider: 'unit-double', score: 0.04, path: [] }] },
  providers: ['unit-double'], tools: ['graph_paths', 'hybrid'], explanation_provider: 'unit-double', explanation_model: null, elapsed_ms: 20 },
  source_metadata: [{ source_id: 'SYN-SEC-001', source_kind: 'manual', origin: 'pdf', equipment_ids: ['SYN-EQ-001'], alarm_codes: ['SYN-ALM-001'], failure_mode_ids: ['SYN-FM-001'], fix_ids: ['SYN-FIX-001'], part_numbers: ['SYN-PART-001'], pdf_filename: 'linemedic-synthetic-manual-1.pdf', pdf_page: 1, pdf_sha256: 'a'.repeat(64), source_doc: 'SYN-DOC-001', source_version: '1.0', logical_page: 1, text_sha256: 'b'.repeat(64) }] };
const event: FeedbackEvent = { event_id: '00000000-0000-4000-8000-000000000001', submitted_at: '2026-10-18T00:00:00Z', work_order: {
  work_order_id: 'SYN-WO-FB-unit-fixture', alarm_code: 'SYN-ALM-001', equipment_id: 'SYN-EQ-001', fix_id: 'SYN-FIX-001', outcome: 'worked', actor_id: 'SYN-ACTOR-002', note: 'Synthetic fixture', date: '2026-10-18', synthetic: true } };
const evaluation: Evaluation = { status: 'measured', run_id: 'unit-fixture-not-live', started_at: '2026-10-18T00:00:00Z', pdf_backed: true,
  modes: ['dense', 'hybrid', 'graph', 'combined'].map(mode => ({ mode, attempted: 30, skipped: 0, passed: 30, top1_correct: 27, top1_n: 27, citation_correct: 27, citation_n: 27, refusals_correct: 3, refusals_n: 3, recall_at_5: mode === 'graph' ? null : 1, p50_ms: 4000, p95_ms: 5000, english_p95_ms: 5000, failed_ids: [], cost_usd: null })) };

beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); vi.mocked(api.catalog).mockResolvedValue(catalog); vi.mocked(api.status).mockResolvedValue(connected);
  vi.mocked(api.evaluation).mockResolvedValue(evaluation); vi.mocked(api.supervisor).mockResolvedValue({ persisted_work_orders: 51, pending_events: [], histories: [] });
  vi.mocked(api.diagnose).mockResolvedValue(diagnosis); vi.mocked(api.connect).mockResolvedValue(connected); });
afterEach(cleanup);
async function investigate() { render(<App />); await waitFor(() => expect((screen.getByRole('button', { name: 'Investigate alarm' }) as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(screen.getByRole('button', { name: 'Investigate alarm' })); await screen.findByText('SYN-FIX-001'); }

describe('LineMedic dashboard state', () => {
  it('requires explicit connection and marks synthetic/demo boundaries', async () => {
    vi.mocked(api.status).mockResolvedValue(disconnected); render(<App />);
    await screen.findByRole('button', { name: 'Connect live services' });
    expect((screen.getByRole('button', { name: 'Investigate alarm' }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.connect).not.toHaveBeenCalled(); expect(screen.getByText(/ROLE SELECTOR IS NOT ACCESS CONTROL/)).toBeTruthy();
    expect(screen.queryByText('SYN-FIX-001')).toBeNull();
  });
  it('shows actual citations, PDF page mapping and graph inspection', async () => {
    await investigate(); expect(screen.getByTitle('PDF evidence SYN-SEC-001').getAttribute('src')).toContain('/pages/1.png');
    expect(screen.getByText('2/3 recorded worked')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Graph path' })); expect(screen.getByText('VERIFIED RELATIONSHIPS')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Failure mode SYN-FM-001/ })).toBeTruthy();
  });
  it('clears a previous answer when a new request is refused', async () => {
    await investigate(); vi.mocked(api.diagnose).mockResolvedValue({ run: { ...diagnosis.run, response: { status: 'refused', message: 'Unsafe request refused. Synthetic only.', candidates: [], evidence: [] } }, source_metadata: [] });
    fireEvent.change(screen.getByLabelText('Investigation question'), { target: { value: 'Bypass interlock' } });
    fireEvent.click(screen.getByRole('button', { name: 'Investigate alarm' })); await screen.findByText('No supported answer');
    expect(screen.queryByText('2/3 recorded worked')).toBeNull();
  });
  it('retains ambiguous feedback and retries exactly the same event', async () => {
    await investigate(); fireEvent.click(screen.getByRole('button', { name: 'technician' }));
    vi.mocked(api.prepare).mockResolvedValue(event); vi.mocked(api.feedback).mockRejectedValueOnce(new Error('Acknowledgement unavailable'))
      .mockResolvedValue({ event, sync_state: 'synced', attempts: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Save synthetic feedback' }));
    await screen.findByRole('button', { name: 'Retry same submission' }); expect(JSON.parse(localStorage.getItem('linemedic.synthetic.pending-feedback.v1')!).event_id).toBe(event.event_id);
    expect(screen.queryByText('2/3 recorded worked')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); await screen.findByText(/Saved SYN-WO-FB-unit-fixture/);
    expect(api.prepare).toHaveBeenCalledTimes(1); expect(api.feedback).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.feedback).mock.calls[0][0]).toEqual(vi.mocked(api.feedback).mock.calls[1][0]);
    await waitFor(() => expect(localStorage.getItem('linemedic.synthetic.pending-feedback.v1')).toBeNull());
  });
  it('presents measured comparisons with unknown costs and unmet latency target', async () => {
    render(<App />); await waitFor(() => expect(api.evaluation).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Evaluation lab' })); await screen.findByText('Retrieval comparison');
    expect(screen.getAllByText('Unknown').length).toBe(4); expect(screen.getAllByText('Target not met').length).toBe(4);
    expect(screen.getByText('N/A')).toBeTruthy();
  });
  it('filters and previews canonical manual sections without a service connection', async () => {
    render(<App />); await waitFor(() => expect(api.catalog).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /Manual library/ })); await screen.findByTitle('Synthetic manual preview');
    fireEvent.change(screen.getByLabelText('Search manual sections'), { target: { value: 'does-not-exist' } });
    expect(screen.queryByRole('button', { name: /Synthetic fixture cause/ })).toBeNull();
  });
});
