/**
 * 型別匯出
 */

export * from './message';
export * from './todo';
export * from './websocket';

// 會話型別
export interface Session {
  session_id: string;
  title: string;
  project_path: string;
  mode: AgentMode;
  status: SessionStatus;
  message_count: number;
  created_at: string;
  updated_at: string;
  is_active?: boolean;
  is_processing?: boolean;
  current_task?: string;
  tools?: string[];
  // ---- session.list 擴充套件欄位 ----
  channel_id?: string;         // 渠道ID
  user_id?: string;            // 建立人ID
  last_message_at?: number;    // 最近對話時間(Unix時間戳)
}

export type AgentMode = 'agent.fast' | 'agent.plan' | 'team';
export type SessionStatus = 'active' | 'paused' | 'completed' | 'interrupted';

export interface ModelEntry {
  model_name: string;
  api_base: string;
  api_key: string;
  model_provider: string;
  timeout?: number;
  temperature?: number;
  /** 同 model_name 組內的預設勾選標識 */
  is_default?: boolean;
  /** 可選別名，用於快捷切換模型（如 "mimo" → "xiaomi/mimo-v2-omni"） */
  alias?: string;
  /** 用於原子性重新命名操作，指定原模型名 */
  original_model_name?: string;
  /**
   * 持久化條目在 models.defaults 中的索引；由 models.list 透傳。
   * replace_all 據此識別"未編輯欄位"並保留 YAML 佔位符（如 ${API_KEY}）。
   * 新增條目不帶此欄位。
   */
  origin_index?: number;
}

export interface OffloadFileListResponse {
  session_id: string;
  files: string[];
  path: string;
  total: number;
}

export interface OffloadFileContentResponse {
  session_id: string;
  filename: string;
  content: string;
  path: string;
}
