/**
 * WebSocket 訊息型別
 */

export type WebConnectionState =
  | 'idle'
  | 'connecting'
  | 'ready'
  | 'reconnecting'
  | 'closed';

export interface WsRequest {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

export interface WsResponse {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: string;
  code?: string;
}

export interface WsEvent {
  type: 'event';
  event: string;
  payload: Record<string, unknown>;
  seq?: number;
  stream_id?: string;
}

export type WebMessage = WsRequest | WsResponse | WsEvent;

export interface WebRequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface WebConnectOptions {
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectPath?: string;
}

export interface WebError extends Error {
  code?: string;
  requestId?: string;
  retriable?: boolean;
}

export interface ConnectionAckPayload {
  session_id?: string;
  mode?: string;
  tools?: string[];
  protocol_version?: string;
}

export interface ProcessingStatusPayload {
  is_processing: boolean;
  current_task?: string;
}

export interface EvolutionStatusPayload {
  session_id?: string;
  status: 'start' | 'progress' | 'end';
  stage?: string;
  message?: string;
}

export interface ErrorPayload {
  error: string;
  code?: string;
  recoverable: boolean;
}

/**
 * 中斷意圖型別
 */
export type InterruptIntent = 'pause' | 'cancel' | 'supplement' | 'resume';

/**
 * 中斷結果 Payload
 */
export interface InterruptResultPayload {
  intent: InterruptIntent;
  success: boolean;
  message: string;
  new_input?: string;
  merged_input?: string;
  paused_task?: string;
}

/**
 * 子任務狀態型別
 */
export type SubtaskStatus = 'starting' | 'tool_call' | 'tool_result' | 'completed' | 'error';

/**
 * 子任務更新 Payload
 */
export interface SubtaskUpdatePayload {
  task_id: string;
  description: string;
  status: SubtaskStatus;
  index: number;
  total: number;
  tool_name?: string;
  tool_count?: number;
  message?: string;
  is_parallel?: boolean;
}

/**
 * 問題選項
 */
export interface QuestionOption {
  label: string;
  description?: string;
}

/**
 * 問題定義
 */
export interface Question {
  question: string;
  header: string;
  options: QuestionOption[];
  multi_select?: boolean;
}

/**
 * 使用者問題請求 Payload（服務端 -> 客戶端）
 */
export interface AskUserQuestionPayload {
  request_id: string;
  questions: Question[];
  source?: string; // 來源標識，用於區分自進化確認和工具許可權確認
}

/**
 * 使用者回答
 */
export interface UserAnswer {
  selected_options: string[];
  custom_input?: string;
}

/**
 * 使用者回答 Payload（客戶端 -> 服務端）
 */
export interface UserAnswerPayload {
  request_id: string;
  answers: UserAnswer[];
}
