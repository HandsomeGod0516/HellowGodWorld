import { Message, MessageRole, UsageSummary, WsEvent } from '../types';
import { webClient } from '../services/webClient';
import { normalizeFinalContent } from '../utils/finalContent';

export const HISTORY_GET_METHOD = 'history.get';
export const HISTORY_MESSAGE_EVENT = 'history.message';

/** 助手側僅恢復這些事件；使用者訊息無 event_type，單獨保留 */
const ALLOWED_ASSISTANT_EVENT_TYPES = new Set([
  'chat.final',
  'chat.tool_call',
  'chat.tool_result',
  'chat.usage_summary',
]);

/** 後端約定：最後一幀 `history.message` 使用 `payload.status: done`（相容舊版 `payload.content: done`） */
const HISTORY_RESTORE_DONE_CONTENT = 'done';
/** 流式 chunk 之間的兜底：正常情況由 `done` / `page_complete` 等結束幀關閉；僅當缺少明確結束標記時使用 */
const HISTORY_RESTORE_IDLE_MS = 500;

export interface HistoryToolReplayItem {
  kind: 'tool_call' | 'tool_result';
  at: string;
  payload: Record<string, unknown>;
}

type HistoryTimelineEntry =
  | { kind: 'message'; message: Message }
  | { kind: 'tool_call'; at: string; payload: Record<string, unknown> }
  | { kind: 'tool_result'; at: string; payload: Record<string, unknown> }
  | { kind: 'usage_summary'; at: string; usage: UsageSummary };

interface BeginHistoryRestoreOptions {
  sessionId: string;
  onReady: (messages: Message[], totalPages: number | null) => void;
  /** 與訊息同一時間線順序，用於恢復 ToolGroupDisplay */
  onToolReplay?: (items: HistoryToolReplayItem[]) => void;
  /** 無訊息且無工具回放時呼叫；`totalPages` 來自流中最後一幀（若有） */
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
}

export interface HistoryRestoreHandle {
  generation: number;
  dispose: () => void;
}

let restoreGeneration = 0;
let activeRestore: HistoryRestoreHandle | null = null;

/** 分頁拉取與全量恢復互斥，避免 chunk 串臺 */
let activePageFetchDispose: (() => void) | null = null;

function disposeActivePageFetch(): void {
  activePageFetchDispose?.();
  activePageFetchDispose = null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function pickFirstString(input: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = input[key];
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    }
  }
  return undefined;
}

function normalizeHistoryRole(rawRole: unknown): MessageRole {
  if (typeof rawRole !== 'string') return 'assistant';
  const role = rawRole.trim().toLowerCase();
  if (role === 'user' || role === 'human') return 'user';
  if (role === 'assistant' || role === 'ai' || role === 'bot') return 'assistant';
  if (role === 'system') return 'system';
  if (role === 'tool' || role === 'tool_call' || role === 'tool_result') return 'tool';
  return 'assistant';
}

function isHistoryRestoreDoneContent(rawContent: unknown): boolean {
  if (typeof rawContent !== 'string') {
    return false;
  }
  return rawContent.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT;
}

function isHistoryRestoreDonePayload(payload: Record<string, unknown>): boolean {
  const rawStatus = payload.status;
  if (typeof rawStatus === 'string' && rawStatus.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT) {
    return true;
  }
  return isHistoryRestoreDoneContent(payload.content);
}

function extractHistoryMessagePayload(payload: Record<string, unknown>): unknown {
  if ('message' in payload) {
    return payload.message;
  }
  return payload.content;
}

function normalizeHistoryContent(
  rawContent: unknown,
  onError?: (message: string) => void
): Record<string, unknown> | null {
  if (isHistoryRestoreDoneContent(rawContent)) {
    return null;
  }
  if (isRecord(rawContent)) {
    return rawContent;
  }
  if (typeof rawContent !== 'string') {
    return null;
  }
  try {
    const parsed = JSON.parse(rawContent);
    if (isRecord(parsed)) {
      return parsed;
    }
    return null;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    onError?.(`history.message.content parse failed: ${detail}`);
    return null;
  }
}

function recordTimestampIso(record: Record<string, unknown>): string {
  const ts = record.timestamp;
  if (typeof ts === 'number' && Number.isFinite(ts)) {
    const millis = ts > 1_000_000_000_000 ? ts : ts * 1000;
    const d = new Date(millis);
    if (!Number.isNaN(d.getTime())) {
      return d.toISOString();
    }
  }
  if (typeof ts === 'string') {
    const parsed = Date.parse(ts);
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }
  return new Date().toISOString();
}

const _HISTORY_RECORD_META_KEYS = new Set([
  'id', 'role', 'request_id', 'channel_id', 'timestamp', 'event_type', 'event_payload',
]);

/** 合併 event_payload 與頂層 content，供 final / tool 解析 */
function buildEventPayloadForRecord(record: Record<string, unknown>): Record<string, unknown> {
  const ep = record.event_payload;
  const base = isRecord(ep) ? { ...ep } : {};

  // 無 event_payload 時：將頂層工具欄位（extra 展平寫入的欄位）提升到 base
  if (!isRecord(ep)) {
    for (const [key, value] of Object.entries(record)) {
      if (!_HISTORY_RECORD_META_KEYS.has(key)) {
        base[key] = value;
      }
    }
  }

  if (typeof record.content === 'string' && typeof base.content !== 'string') {
    base.content = record.content;
  }
  return base;
}

function parseHistoryTimelineEntry(
  record: Record<string, unknown>,
  sessionId: string
): HistoryTimelineEntry | null {
  const role = normalizeHistoryRole(record.role);
  const at = recordTimestampIso(record);

  if (role === 'user') {
    const content = pickFirstString(record, ['content', 'text', 'body']) ?? '';
    if (!content.trim()) {
      return null;
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-user-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: { id, role: 'user', content, timestamp: at },
    };
  }

  if (role !== 'assistant') {
    return null;
  }

  let eventType = typeof record.event_type === 'string' ? record.event_type.trim() : '';

  if (!eventType) {
    const raw = String(record.content ?? '').trim();
    if (!raw) {
      return null;
    }
    eventType = 'chat.final';
  }

  if (!ALLOWED_ASSISTANT_EVENT_TYPES.has(eventType)) {
    return null;
  }

  const payload = buildEventPayloadForRecord(record);

  if (eventType === 'chat.final') {
    const content = normalizeFinalContent(payload);
    if (!content.trim()) {
      return null;
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-final-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: { id, role: 'assistant', content, timestamp: at },
    };
  }

  if (eventType === 'chat.tool_call') {
    return { kind: 'tool_call', at, payload };
  }

  if (eventType === 'chat.tool_result') {
    return { kind: 'tool_result', at, payload };
  }

  if (eventType === 'chat.usage_summary') {
    const rawUsage = payload.usage;
    if (isRecord(rawUsage)) {
      const usage: UsageSummary = {
        input_tokens: typeof rawUsage.input_tokens === 'number' ? rawUsage.input_tokens : 0,
        output_tokens: typeof rawUsage.output_tokens === 'number' ? rawUsage.output_tokens : 0,
        total_tokens: typeof rawUsage.total_tokens === 'number' ? rawUsage.total_tokens : 0,
      };
      if (typeof rawUsage.input_cost === 'number') usage.input_cost = rawUsage.input_cost;
      if (typeof rawUsage.output_cost === 'number') usage.output_cost = rawUsage.output_cost;
      if (typeof rawUsage.total_cost === 'number') usage.total_cost = rawUsage.total_cost;
      return { kind: 'usage_summary', at, usage };
    }
    return null;
  }

  return null;
}

/** 工作區 history.json 預覽：最多展示條數（按訊息時間取最近） */
export const HISTORY_FILE_PREVIEW_MAX_MESSAGES = 20;

/**
 * 將磁碟上的 history.json 解析結果（通常為記錄陣列）轉為與歷史恢復相同的篩選規則下的訊息列表，
 * 並按時間升序僅保留時間上最近的 {@link HISTORY_FILE_PREVIEW_MAX_MESSAGES} 條使用者/助手訊息。
 */
export function parseHistoryJsonFileToPreviewMessages(
  parsed: unknown,
  sessionId: string
): Message[] {
  if (!Array.isArray(parsed)) {
    return [];
  }

  const messages: Message[] = [];
  for (const item of parsed) {
    if (!isRecord(item)) {
      continue;
    }
    const entry = parseHistoryTimelineEntry(item, sessionId);
    if (entry?.kind === 'message') {
      messages.push(entry.message);
    }
  }

  const sorted = [...messages].sort(
    (a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)
  );
  return sorted.slice(-HISTORY_FILE_PREVIEW_MAX_MESSAGES);
}

function isHistoryBatchEnd(payload: Record<string, unknown>): boolean {
  const markers = [
    payload.done,
    payload.last,
    payload.is_last,
    payload.page_complete,
    payload.end,
  ];
  return markers.some((marker) => marker === true);
}

/**
 * 僅處理屬於當前 `history.get` 會話的幀，避免多標籤/亂序下的串臺。
 * 無 `session_id` 時：丟棄資料行；仍接受明確的結束幀（相容未注入 id 的舊鏈路）。
 */
function shouldProcessHistoryPayload(payload: Record<string, unknown>, expectedSessionId: string): boolean {
  const sid = typeof payload.session_id === 'string' ? payload.session_id.trim() : '';
  if (sid && sid !== expectedSessionId) {
    return false;
  }
  if (!sid) {
    return isHistoryRestoreDonePayload(payload) || isHistoryBatchEnd(payload);
  }
  return true;
}

export function beginHistoryRestore(options: BeginHistoryRestoreOptions): HistoryRestoreHandle {
  disposeActivePageFetch();
  activeRestore?.dispose();

  const generation = restoreGeneration + 1;
  restoreGeneration = generation;

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let idleTimer: number | null = null;
  let disposed = false;

  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      window.clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = event.payload;
    if (!shouldProcessHistoryPayload(payload, options.sessionId)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      clearIdleTimer();
      finalize();
      return;
    }

    const raw = extractHistoryMessagePayload(payload);
    const record = normalizeHistoryContent(raw, options.onError);
    if (record) {
      const entry = parseHistoryTimelineEntry(record, options.sessionId);
      if (entry) {
        entries.unshift(entry);
      }
    }

    if (isHistoryBatchEnd(payload)) {
      clearIdleTimer();
      finalize();
      return;
    }

    clearIdleTimer();
    idleTimer = window.setTimeout(() => {
      finalize();
    }, HISTORY_RESTORE_IDLE_MS);
  });

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    clearIdleTimer();
    unsubscribe();
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(): void {
    if (disposed) return;

    const messages: Message[] = [];
    const toolReplay: HistoryToolReplayItem[] = [];
    for (const e of entries) {
      if (e.kind === 'message') {
        messages.push(e.message);
      } else if (e.kind === 'usage_summary') {
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === 'assistant') {
            messages[i] = { ...messages[i], usageSummary: e.usage };
            break;
          }
        }
      } else {
        toolReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      }
    }

    dispose();

    if (messages.length === 0 && toolReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady(messages, totalPages);
    if (toolReplay.length > 0) {
      options.onToolReplay?.(toolReplay);
    }
  }

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  return handle;
}

export interface FetchHistoryPageResult {
  messages: Message[];
  toolReplay: HistoryToolReplayItem[];
  totalPages: number | null;
}

export interface FetchHistoryPageOptions {
  sessionId: string;
  onReady: (result: FetchHistoryPageResult) => void;
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
}

/**
 * 拉取單頁歷史（用於「載入更早」），與 beginHistoryRestore 互斥。
 * 呼叫方需在訂閱建立後再發 `history.get`（含對應 `page_idx`）。
 */
export function fetchHistoryPage(options: FetchHistoryPageOptions): HistoryRestoreHandle {
  disposeActivePageFetch();
  activeRestore?.dispose();

  const generation = restoreGeneration + 1;
  restoreGeneration = generation;

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let idleTimer: number | null = null;
  let disposed = false;

  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      window.clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = event.payload;
    if (!shouldProcessHistoryPayload(payload, options.sessionId)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      clearIdleTimer();
      finalize();
      return;
    }

    const raw = extractHistoryMessagePayload(payload);
    const record = normalizeHistoryContent(raw, options.onError);
    if (record) {
      const entry = parseHistoryTimelineEntry(record, options.sessionId);
      if (entry) {
        entries.unshift(entry);
      }
    }

    if (isHistoryBatchEnd(payload)) {
      clearIdleTimer();
      finalize();
      return;
    }

    clearIdleTimer();
    idleTimer = window.setTimeout(() => {
      finalize();
    }, HISTORY_RESTORE_IDLE_MS);
  });

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    clearIdleTimer();
    unsubscribe();
    activePageFetchDispose = null;
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(): void {
    if (disposed) return;

    const messages: Message[] = [];
    const toolReplay: HistoryToolReplayItem[] = [];
    for (const e of entries) {
      if (e.kind === 'message') {
        messages.push(e.message);
      } else if (e.kind === 'usage_summary') {
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === 'assistant') {
            messages[i] = { ...messages[i], usageSummary: e.usage };
            break;
          }
        }
      } else {
        toolReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      }
    }

    dispose();

    if (messages.length === 0 && toolReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady({ messages, toolReplay, totalPages });
  }

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  activePageFetchDispose = dispose;
  return handle;
}