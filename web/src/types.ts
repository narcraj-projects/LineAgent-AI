export type Role = 'operator' | 'technician' | 'supervisor';
export type Mode = 'auto' | 'dense' | 'hybrid' | 'graph' | 'combined';
export type View = 'workspace' | 'manuals' | 'evaluation' | 'supervisor';
export interface Alarm { alarm_id: string; code: string; description: string; equipment_id: string; failure_mode_id: string }
export interface Equipment { equipment_id: string; equipment_type: string; line_id: string }
export interface Manual { section_id: string; source_doc: string; source_version: string; page: number; heading: string; text: string }
export interface PDFFile { filename: string; pages: number; sha256: string }
export interface Citation { section_id: string; filename: string; pdf_page: number; source_doc: string; source_version: string; logical_page: number }
export interface Catalog { alarms: Alarm[]; equipment: Equipment[]; manuals: Manual[]; pdf_files: PDFFile[]; citations: Citation[]; counts: Record<string, number>; snapshot_label: string }
export interface ServiceStatus { connected: boolean; graph_provider: string | null; graph_mode: string | null; vector_namespace: string | null; pdf_mode: boolean; demo_auth: true; synthetic: true }
export interface Outcome { worked: number; did_not_work: number }
export interface Candidate { fix_id: string; cause: string; part_number: string | null; evidence_source_ids: string[]; graph_path: string[]; outcomes: Outcome; safety_warning: string }
export interface Evidence { source_id: string; text: string; source_kind: 'manual' | 'work_order'; provider: string; score: number | null; path: string[] }
export interface SourceMetadata { source_id: string; source_kind: string; origin: string; equipment_ids: string[]; alarm_codes: string[]; failure_mode_ids: string[]; fix_ids: string[]; part_numbers: string[]; pdf_filename: string | null; pdf_page: number | null; pdf_sha256: string | null; source_doc: string | null; source_version: string | null; logical_page: number | null; text_sha256: string }
export interface Run { decision: { route: string; reason: string; alarm_code: string | null; equipment_id: string | null }; response: { status: 'supported' | 'refused'; message: string; candidates: Candidate[]; evidence: Evidence[] }; providers: string[]; tools: string[]; explanation_provider: string | null; explanation_model: string | null; elapsed_ms: number }
export interface Diagnosis { run: Run; source_metadata: SourceMetadata[] }
export interface FeedbackEvent { event_id: string; submitted_at: string; work_order: { work_order_id: string; alarm_code: string; equipment_id: string; fix_id: string; outcome: 'worked' | 'did_not_work'; actor_id: string; note: string; date: string; synthetic: true } }
export interface Receipt { event: FeedbackEvent; sync_state: 'pending' | 'synced'; attempts: number }
export interface SupervisorReport { persisted_work_orders: number; pending_events: Receipt[]; histories: { equipment_id: string; fix_id: string; outcomes: Outcome }[] }
export interface EvaluationMode { mode: string; attempted: number; skipped: number; passed: number; top1_correct: number; top1_n: number; citation_correct: number; citation_n: number; refusals_correct: number; refusals_n: number; recall_at_5: number | null; p50_ms: number | null; p95_ms: number | null; english_p95_ms: number | null; failed_ids: string[]; cost_usd: number | null }
export interface Evaluation { status: 'measured' | 'not_run'; run_id: string | null; started_at: string | null; pdf_backed: boolean; modes: EvaluationMode[] }
