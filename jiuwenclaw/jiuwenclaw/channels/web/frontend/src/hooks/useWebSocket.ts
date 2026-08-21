/**
 * WebSocket Hook
 *
 * 管理 WebSocket 連線和訊息處理
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import {
  ConnectionAckPayload,
  WebConnectOptions,
  WebError,
  WebRequestOptions,
  WebConnectionState,
  InterruptResultPayload,
  InterruptIntent,
  SubtaskUpdatePayload,
  AskUserQuestionPayload,
  EvolutionStatusPayload,
  UserAnswer,
  MediaItem,
  AgentMode,
  Session,
  ToolResult,
 	ToolCall,
  UsageSummary,
} from '../types';
import { useChatStore, useTodoStore, useSessionStore } from '../stores';
import { webClient } from '../services/webClient';
import i18n from '../i18n';
import {
  fetchTtsAudio,
  playAudioBase64,
  sanitizeTtsText,
  stopAllTts,
  normalizeFinalContent,
} from '../utils';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
} from '../features/tool-events/toolEventNormalizer';

const WS_RECONNECT_EVENT = 'jiuwenclaw:ws-reconnect-request';

interface UseWebSocketOptions {
  activeSessionId?: string;
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectPath?: string;
  onConnect?: (payload: ConnectionAckPayload) => void;
  onDisconnect?: () => void;
  onError?: (error: string) => void;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  connectionState: WebConnectionState;
  request: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: WebRequestOptions
  ) => Promise<T>;
  sendMessage: (content: string, sessionId: string) => Promise<void>;
  interrupt: (
    sessionId: string,
    intent: InterruptIntent,
    options?: { newInput?: string }
  ) => Promise<void>;
  pause: (sessionId: string) => Promise<void>;
  cancel: (sessionId: string) => Promise<void>;
  supplement: (sessionId: string, newInput: string) => Promise<void>;
  resume: (sessionId: string) => Promise<void>;
  switchMode: (sessionId: string, mode: AgentMode) => Promise<void>;
  disconnect: () => void;
  sendUserAnswer: (
    sessionId: string,
    requestId: string,
    answers: UserAnswer[],
    source?: string
  ) => Promise<void>;
  getInflightCount: () => number;
}

function normalizeAgentMode(rawMode: unknown): AgentMode {
  if (typeof rawMode !== 'string') return 'agent.plan';
  const normalized = rawMode.trim().toLowerCase();
  if (normalized === 'agent.fast') return 'agent.fast';
  if (normalized === 'team') return 'team';
  return 'agent.plan';
}

const EVENT_DEDUP_WINDOW_MS = 1500;

function stringifyPayloadForDedup(payload: Record<string, unknown>): string {
  try {
    const serialized = JSON.stringify(payload);
    if (!serialized) {
      return '';
    }
    return serialized.length > 800 ? serialized.slice(0, 800) : serialized;
  } catch {
    return '';
  }
}

function makeEventDedupKey(eventName: string, payload: Record<string, unknown>): string {
  const payloadSessionId =
    typeof payload.session_id === 'string' ? payload.session_id : '';
  const payloadEventType =
    typeof payload.event_type === 'string' ? payload.event_type : '';
  const payloadSnapshot = stringifyPayloadForDedup(payload);
  return `${eventName}::${payloadSessionId}::${payloadEventType}::${payloadSnapshot}`;
}

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const {
    activeSessionId,
    provider,
    apiKey,
    apiBase,
    model,
    projectPath,
    onConnect,
    onDisconnect,
    onError,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] =
    useState<WebConnectionState>('idle');
  const userInputVersionRef = useRef(0);
  const activeSessionIdRef = useRef(activeSessionId);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const sendMessageRef = useRef<typeof sendMessage>();
  const recentEventRef = useRef<Map<string, number>>(new Map());
  const eventDedupDroppedRef = useRef<Record<string, number>>({});

  // Stores
  const {
    addMessage,
    appendStreamContent,
    startStreaming,
    stopStreaming,
    updateMessage,
    setProcessing,
    setThinking,
    setEvolutionStatus,
    setPaused,
    setInterruptResult,
    addToolCall,
    addToolResult,
    markTimedOutExecutions,
    updateSubtask,
    clearSubtasks,
    clearMessages,
    setPendingQuestion,
    removeFromTaskQueue,
  } = useChatStore();
  const { setTodos, clearTodos } = useTodoStore();
  const {
    setMode,
    setConnected,
    setAvailableTools,
    setConnectionStats,
    updateSession,
    setContextCompressionStats,
    setHeartbeatStatus,
  } =
    useSessionStore();

  const handleTtsPlayback = useCallback(
    (messageId: string, content: string) => {
      const sanitized = sanitizeTtsText(content);
      if (!sanitized || sanitized.startsWith('[任務已中斷]')) {
        return;
      }

      const { messages } = useChatStore.getState();
      const existing = messages.find((msg) => msg.id === messageId);
      if (existing?.audioBase64) {
        return;
      }

      void (async () => {
        const versionAtStart = userInputVersionRef.current;
        const ttsSessionId = activeSessionIdRef.current;
        const response = await fetchTtsAudio(
          sanitized,
          ttsSessionId && ttsSessionId !== 'new' ? ttsSessionId : undefined
        );
        if (!response?.success || !response.audio_base64) {
          return;
        }

        updateMessage(messageId, {
          audioBase64: response.audio_base64,
          audioMime: response.audio_mime,
        });

        if (versionAtStart !== userInputVersionRef.current) {
          return;
        }

        await playAudioBase64(
          response.audio_base64,
          response.audio_mime || 'audio/mpeg'
        );
      })();
    },
    [updateMessage]
  );

  const shouldHandleSessionEvent = useCallback(
    (payload: Record<string, unknown>): boolean => {
      const payloadSessionId = payload.session_id;
      if (typeof payloadSessionId !== 'string' || !payloadSessionId) {
        return true;
      }
      const currentSessionId = activeSessionIdRef.current;
      if (!currentSessionId || currentSessionId === 'new') {
        return true;
      }
      return payloadSessionId === currentSessionId;
    },
    []
  );

  const handleConnectionAck = useCallback(
    (payload: Record<string, unknown>) => {
      const ackPayload = payload as unknown as ConnectionAckPayload;
      setConnected(true);
      if (Array.isArray(ackPayload.tools)) {
        setAvailableTools(ackPayload.tools);
      }
      onConnectRef.current?.(ackPayload);
    },
    [setAvailableTools, setConnected]
  );

  // 斷開連線
  const disconnect = useCallback(() => {
    webClient.disconnect();
  }, [setConnected]);

  const request = useCallback(
    async <T = unknown>(
      method: string,
      params?: Record<string, unknown>,
      requestOptions?: WebRequestOptions
    ): Promise<T> => {
      return webClient.request<T>(method, params, requestOptions);
    },
    []
  );

  // 傳送聊天訊息
  const sendMessage = useCallback(
    async (content: string, sessionId: string) => {
      if (!content.trim()) return;

      userInputVersionRef.current += 1;
      stopAllTts();

      // 新增使用者訊息
      addMessage({
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      });

      // 不再預先建立助手訊息，而是在收到第一個 content_chunk 時建立
      // 這樣工具呼叫會先顯示，然後才是助手的回覆

      setProcessing(true);
      setThinking(true);
      
      // 正常呼叫介面
      const currentMode = useSessionStore.getState().mode;
      const selectedModel = useSessionStore.getState().selectedModelName;
      try {
        await request('chat.send', {
          session_id: sessionId,
          content,
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
        });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        setProcessing(false);
        setThinking(false);
        const errorMsg = webError.message || i18n.t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        addMessage({
          id: `error-${Date.now()}`,
          role: 'system',
          content: i18n.t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }
    },
    [addMessage, request, setProcessing, setThinking]
  );

  // 儲存sendMessage函式到ref
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  // 統一中斷介面 - pause/cancel/supplement/resume
  const interrupt = useCallback(
    async (
      sessionId: string,
      intent: InterruptIntent,
      options?: { newInput?: string }
    ) => {
      const newInput = options?.newInput;
      if (intent === 'supplement' && newInput) {
        userInputVersionRef.current += 1;
        stopAllTts();
        addMessage({
          id: `user-${Date.now()}`,
          role: 'user',
          content: newInput,
          timestamp: new Date().toISOString(),
        });
      }
      try {
        const params: Record<string, unknown> = {
          session_id: sessionId,
          intent,
        };
        if (intent === 'supplement') {
          params.new_input = newInput ?? '';
          const selectedModel = useSessionStore.getState().selectedModelName;
          if (selectedModel) params.model_name = selectedModel;
        }
        await request('chat.interrupt', params);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.interruptFailed'));
      }
    },
    [addMessage, request, setConnectionStats]
  );

  // 暫停 - 顯式暫停當前任務
  const pause = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'pause');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.pauseFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  const cancel = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'cancel');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.cancelFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  const supplement = useCallback(
    async (sessionId: string, newInput: string) => {
      try {
        await interrupt(sessionId, 'supplement', { newInput });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.supplementFailed'));
      }
    },
    [interrupt, setConnectionStats]
  );

  // 恢復 - 恢復暫停的任務
  const resume = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'resume');
        setPaused(false);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.resumeFailed'));
      }
    },
    [interrupt, setConnectionStats, setPaused]
  );

  // 切換模式
  const switchMode = useCallback(
    async (sessionId: string, mode: AgentMode) => {
      // 標記正在切換模式
      useChatStore.getState().setSwitchingMode(true);
      
      // 只有在有任務執行時才呼叫 interrupt
      if (sessionId && sessionId !== 'new') {
        const state = useChatStore.getState();
        if (state.isProcessing || state.isPaused) {
          try {
            await interrupt(sessionId, 'cancel');
          } catch {
            // 忽略中斷錯誤
          }
        }
      }
      
      setMode(mode);
      if (sessionId && sessionId !== 'new') {
        updateSession(sessionId, { mode });
      }
      // 延遲重置標誌
      setTimeout(() => {
        useChatStore.getState().setSwitchingMode(false);
      }, 300);
    },
    [setMode, updateSession, interrupt]
  );

  // 傳送使用者回答
  const sendUserAnswer = useCallback(
    async (sessionId: string, requestId: string, answers: UserAnswer[], source?: string) => {
      try {
        // 如果是工具許可權確認，傳送 chat.send
        if (source === 'permission_interrupt') {
          await request('chat.send', {
            session_id: sessionId,
            query: '',
            request_id: requestId,
            answers: answers,
          });
        } else {
          // 否則傳送 chat.user_answer（自進化確認）
          await request('chat.user_answer', {
            session_id: sessionId,
            request_id: requestId,
            answers,
          });
        }
        setPendingQuestion(null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || i18n.t('network.submitAnswerFailed'));
      }
    },
    [request, setConnectionStats, setPendingQuestion]
  );

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  // 會話切換時不再重置上下文壓縮資訊，保持本地儲存的狀態
  // useEffect(() => {
  //   setContextCompressionStats(null);
  // }, [activeSessionId, setContextCompressionStats]);

  useEffect(() => {
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onErrorRef.current = onError;
  }, [onConnect, onDisconnect, onError]);

  const shouldDropDuplicatedEvent = useCallback(
    (eventName: string, payload: Record<string, unknown>): boolean => {
      const now = Date.now();
      const dedupKey = makeEventDedupKey(eventName, payload);
      const recent = recentEventRef.current;
      const lastSeen = recent.get(dedupKey);
      recent.set(dedupKey, now);

      // 控制 map 大小，避免長期執行後無限增長
      if (recent.size > 400) {
        for (const [key, ts] of recent) {
          if (now - ts > EVENT_DEDUP_WINDOW_MS * 6) {
            recent.delete(key);
          }
        }
      }

      const dropped = lastSeen != null && now - lastSeen <= EVENT_DEDUP_WINDOW_MS;
      if (dropped && import.meta.env.DEV) {
        const nextCount = (eventDedupDroppedRef.current[eventName] || 0) + 1;
        eventDedupDroppedRef.current[eventName] = nextCount;
        if (nextCount === 1 || nextCount % 10 === 0) {
          console.debug('[ws][metrics] eventDedupDropped', {
            eventName,
            count: nextCount,
          });
        }
      }
      return dropped;
    },
    []
  );

  useEffect(() => {
    const unsubs = [
      webClient.on('connection.ack', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('hello', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('chat.delta', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        
        const currentMode = useSessionStore.getState().mode;
        const content = typeof payload.content === 'string' ? payload.content : '';
        
        // team 模式下，累積 chat.delta 內容
        if (currentMode === 'team' && content) {
          setThinking(false);
          
          const { messages } = useChatStore.getState();
          const existingMsg = messages.find(m => 
            m.id.startsWith('team-leader-') && 
            (m as { isStreaming?: boolean }).isStreaming === true
          );
          
          if (existingMsg) {
            const existingContent = existingMsg.content || '';
            const newContent = existingContent + content;
            const updatePayload: { content: string; isStreaming?: boolean } = { content: newContent };
            if (content.includes('MEDIA:')) {
              updatePayload.isStreaming = false;
            }
            updateMessage(existingMsg.id, updatePayload);
          } else {
            const msgId = `team-leader-${Date.now()}`;
            addMessage({
              id: msgId,
              role: 'system',
              content: content,
              timestamp: new Date().toISOString(),
              isStreaming: true,
            });
          }
          return;
        }
        
        const { currentStreamId } = useChatStore.getState();
        setThinking(false);
        if (!currentStreamId && content) {
          const assistantMsgId = `assistant-${Date.now()}`;
          addMessage({
            id: assistantMsgId,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            isStreaming: true,
          });
          startStreaming(assistantMsgId);
        }
        appendStreamContent(content);
      }),
      webClient.on('chat.final', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        
        const currentMode = useSessionStore.getState().mode;
        const content = normalizeFinalContent(payload);
        
        // team 模式下，將 chat.final 作為 team_leader 訊息處理
        if (currentMode === 'team' && content) {
          setThinking(false);
          
          const { messages } = useChatStore.getState();
          const existingMsg = messages.find(m => 
            m.id.startsWith('team-leader-') && 
            (m as { isStreaming?: boolean }).isStreaming === true
          );
          
          if (existingMsg) {
            updateMessage(existingMsg.id, { content, isStreaming: false });
          } else {
            const timestamp = payload.timestamp || Date.now();
            addMessage({
              id: `team-leader-${Date.now()}`,
              role: 'system',
              content: `team.leader:${JSON.stringify({ content, timestamp })}`,
              timestamp: new Date().toISOString(),
            });
          }
          return;
        }
        
        const { currentStreamId, messages } = useChatStore.getState();
        const payloadSessionId =
          typeof payload.session_id === 'string' ? payload.session_id.trim() : '';
        // 僅當有明確會話繫結時才把 final 合併進當前流式氣泡。
        // 定時任務等廣播的 session_id 為空/null，若仍走 currentStreamId 會寫到錯誤氣泡甚至“無可見更新”。
        const streamId = currentStreamId;
        if (streamId && payloadSessionId) {
          updateMessage(streamId, { ...(content ? { content } : {}), isStreaming: false });
          stopStreaming();
          if (content && !content.includes('MEDIA:')) {
            handleTtsPlayback(streamId, content);
          }
          return;
        }
        if (content) {
          const cronMeta = payload.cron as Record<string, unknown> | undefined;
          const cronRunId =
            typeof cronMeta?.run_id === 'string' ? cronMeta.run_id.trim() : '';
          const isCronPlaceholderContent = /^\[cron\].*正在執行中/.test(content);

          // 正式結果：替換同 run_id 的佔位氣泡，或最近的 [cron]…正在執行中…
          if (!isCronPlaceholderContent) {
            let placeholderId: string | null = null;
            if (cronRunId) {
              const byRun = messages.find((m) => m.id === `cron-placeholder-${cronRunId}`);
              if (byRun) placeholderId = byRun.id;
            }
            if (!placeholderId) {
              for (let i = messages.length - 1; i >= 0; i -= 1) {
                const msg = messages[i];
                if (msg.role !== 'assistant' || typeof msg.content !== 'string') continue;
                if (/^\[cron\].*正在執行中/.test(msg.content)) {
                  placeholderId = msg.id;
                  break;
                }
              }
            }
            if (placeholderId) {
              updateMessage(placeholderId, { content, isStreaming: false });
              if (!content.includes('MEDIA:')) {
                handleTtsPlayback(placeholderId, content);
              }
              return;
            }
          }

          const messageId =
            isCronPlaceholderContent && cronRunId
              ? `cron-placeholder-${cronRunId}`
              : cronRunId && !isCronPlaceholderContent
                ? `cron-final-${cronRunId}`
                : `msg-${Date.now()}`;

          const existing = messages.find((m) => m.id === messageId);
          if (existing) {
            if (existing.content === content) {
              return;
            }
            updateMessage(messageId, { content, isStreaming: false });
            if (!content.includes('MEDIA:')) {
              handleTtsPlayback(messageId, content);
            }
            return;
          }

          // 去重：若上一條已是相同內容的助手訊息（同一回覆被收到兩次），不再追加
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant' && last.content === content) {
            return;
          }
          addMessage({
            id: messageId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
          });
          if (!content.includes('MEDIA:')) {
            handleTtsPlayback(messageId, content);
          }
        }
      }),
      webClient.on('chat.media', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const mediaPayload = payload as {
          content?: string;
          media_items?: MediaItem[];
        };
        const { currentStreamId, messages } = useChatStore.getState();
        const targetId =
          currentStreamId ??
          [...messages].reverse().find((msg) => msg.role === 'assistant')?.id;
        if (!targetId) {
          return;
        }
        const updates: { content?: string; mediaItems?: MediaItem[] } = {};
        if (mediaPayload.content !== undefined) {
          updates.content = mediaPayload.content;
        }
        if (mediaPayload.media_items?.length) {
          updates.mediaItems = mediaPayload.media_items;
        }
        if (Object.keys(updates).length > 0) {
          updateMessage(targetId, updates);
        }
        if (mediaPayload.content) {
          handleTtsPlayback(targetId, mediaPayload.content);
        }
      }),
      webClient.on('chat.tool_call', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.tool_call', payload)) return;
        setThinking(false);
        const { currentStreamId, currentStreamContent } = useChatStore.getState();
        if (currentStreamId && currentStreamContent) {
          updateMessage(currentStreamId, { isStreaming: false });
          stopStreaming();
          handleTtsPlayback(currentStreamId, currentStreamContent);
        }
        addToolCall(normalizeToolCallPayload(payload));
      }),
      webClient.on('chat.tool_result', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.tool_result', payload)) return;
        addToolResult(normalizeToolResultPayload(payload));
      }),
      webClient.on('todo.updated', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('todo.updated', payload)) return;
        const todos = Array.isArray(payload.todos) ? payload.todos : [];
        setTodos(todos as Parameters<typeof setTodos>[0]);
      }),
      webClient.on('context.compressed', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        const rate =
          typeof payload.rate === 'number' ? payload.rate : 0;
        const beforeCompressed =
          typeof payload.before_compressed === 'number' && Number.isFinite(payload.before_compressed)
            ? payload.before_compressed
            : null;
        const afterCompressed =
          typeof payload.after_compressed === 'number' && Number.isFinite(payload.after_compressed)
            ? payload.after_compressed
            : null;
        setContextCompressionStats({ rate, beforeCompressed, afterCompressed });
        console.debug('[ws] context.compressed', {
          session_id: payload.session_id,
          rate,
          before_compressed: beforeCompressed,
          after_compressed: afterCompressed,
        });
      }),
      webClient.on('heartbeat.relay', ({ payload }) => {
        const heartbeatText =
          typeof payload.heartbeat === 'string' ? payload.heartbeat : '';
        // 只要成功收到 relay 即表示已成功發到前端，始終為 ok，不存在 alert
        setHeartbeatStatus(
          'ok',
          heartbeatText || null,
          new Date().toISOString()
        );
      }),
      webClient.on('session.updated', ({ payload }) => {
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        if (!sessionId) return;
        updateSession(sessionId, payload as Partial<Session>);
        if (sessionId === activeSessionIdRef.current && typeof payload.mode === 'string') {
          setMode(normalizeAgentMode(payload.mode));
        }
      }),
      webClient.on('chat.processing_status', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.processing_status', payload)) return;
        // 切換模式時忽略處理狀態更新
        if (useChatStore.getState().switchingMode) return;
        const isProcessingNow = Boolean(payload.is_processing);
        setProcessing(isProcessingNow);
        if (!isProcessingNow) {
          setThinking(false);
          clearSubtasks();
          
          // 檢查是否有等待的任務佇列
          const currentMode = useSessionStore.getState().mode;
          const { taskQueue } = useChatStore.getState();
          if (currentMode === 'agent.fast' && taskQueue.length > 0) {
            // 智慧執行模式下，自動處理佇列中的下一個任務
            const nextTask = taskQueue[0];
            if (nextTask && activeSessionIdRef.current && sendMessageRef.current) {
              // 從佇列中移除該任務
              removeFromTaskQueue(nextTask.id);
              // 傳送下一個任務
              sendMessageRef.current(nextTask.content, activeSessionIdRef.current);
            }
          }
        }
      }),
      webClient.on('chat.evolution_status', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.evolution_status', payload)) return;
        setEvolutionStatus(payload as unknown as EvolutionStatusPayload);
      }),
      webClient.on('chat.error', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.error', payload)) return;
        setThinking(false);
        const errorMsg =
          typeof payload.error === 'string' ? payload.error : i18n.t('network.unknownError');
        // 忽略 "invalid page_idx or session history not found" 錯誤，因為這是新會話的正常情況
        if (errorMsg.includes('invalid page_idx or session history not found')) {
          return;
        }
        onErrorRef.current?.(errorMsg);
        addMessage({
          id: `error-${Date.now()}`,
          role: 'system',
          content: i18n.t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('chat.interrupt_result', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        if (shouldDropDuplicatedEvent('chat.interrupt_result', payload)) return;
        // 切換模式時忽略中斷結果
        if (useChatStore.getState().switchingMode) return;
        const resultPayload = payload as unknown as InterruptResultPayload;
        setInterruptResult(resultPayload);
        if (resultPayload.intent === 'pause') {
          if (resultPayload.success) {
            setPaused(true, resultPayload.paused_task);
          }
          setProcessing(false);
          setThinking(false);
        } else if (resultPayload.intent === 'resume') {
          if (resultPayload.success) {
            setPaused(false);
          }
        } else if (resultPayload.intent === 'cancel') {
          setPaused(false);
          setProcessing(false);
          setThinking(false);
        } else if (resultPayload.intent === 'supplement') {
          setPaused(false);
        }
      }),
      webClient.on('chat.subtask_update', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        updateSubtask(payload as unknown as SubtaskUpdatePayload);
      }),
      webClient.on('chat.ask_user_question', ({ payload }) => {
        if (!shouldHandleSessionEvent(payload)) return;
        setPendingQuestion(payload as unknown as AskUserQuestionPayload);
      }),
      // 同時監聽 session_result 事件，以處理後端可能傳送的不同格式
      webClient.on('session_result', ({ payload }) => {
        setThinking(false);
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 建立工具呼叫物件
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '會話完成',
          formatted_args: `會話任務：【${description || '未知任務'}】`,
        };
        addToolCall(sessionToolCall);
        // 組合 description 和 result 作為完整結果
        const fullResult = description
          ? `描述: ${description}\n\n結果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        addToolResult(sessionResult);
      }),
      webClient.on('chat.session_result', ({ payload }) => {
        if (shouldDropDuplicatedEvent('chat.session_result', payload)) {
          return;
        }
        setThinking(false);
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 建立工具呼叫物件
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '會話完成',
          formatted_args: `會話任務：【${description || '未知任務'}】`,
        };
        addToolCall(sessionToolCall);
        // 組合 description 和 result 作為完整結果
        const fullResult = description
          ? `描述: ${description}\n\n結果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        addToolResult(sessionResult);
      }),
      webClient.on('team.event', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.event', payload)) {
          return;
        }
        setThinking(false);
        addMessage({
          id: `team-event-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.message', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.message', payload)) {
          return;
        }
        setThinking(false);
        addMessage({
          id: `team-message-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.task', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.task', payload)) {
          return;
        }
        setThinking(false);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as { type?: string; team_id?: string; task_id?: string; status?: string; timestamp?: number };
          useSessionStore.getState().addTeamTaskEvent({
            id: `task-${Date.now()}`,
            type: e.type || '',
            team_id: e.team_id || '',
            task_id: e.task_id || '',
            status: e.status || '',
            timestamp: e.timestamp || Date.now(),
          });
        }
      }),
      webClient.on('team.member', ({ payload }) => {
        if (shouldDropDuplicatedEvent('team.member', payload)) {
          return;
        }
        setThinking(false);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as { type?: string; member_id?: string; status?: string; new_status?: string; timestamp?: number };
          if (e.type === 'team.member.status_changed' && e.member_id && e.new_status) {
            useSessionStore.getState().updateTeamMemberStatus(
              e.member_id,
              e.new_status,
              e.timestamp
            );
          } else {
            useSessionStore.getState().addTeamMember({
              id: `member-${Date.now()}`,
              member_id: e.member_id || '',
              status: e.status || '',
              timestamp: e.timestamp || Date.now(),
            });
          }
        }
      }),
      webClient.on('chat.usage_summary', ({ payload }) => {
        console.log('[usage_summary] received:', payload);
        if (!shouldHandleSessionEvent(payload)) {
          console.log('[usage_summary] filtered by session check');
          return;
        }
        const usage = payload.usage as UsageSummary | undefined;
        if (!usage) {
          console.log('[usage_summary] no usage field in payload');
          return;
        }
        const { currentStreamId, messages } = useChatStore.getState();
        let targetId = currentStreamId;
        if (!targetId) {
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'assistant') {
              targetId = messages[i].id;
              break;
            }
          }
        }
        console.log('[usage_summary] targetId:', targetId, 'usage:', usage);
        if (targetId) {
          useChatStore.getState().setUsageSummary(targetId, usage);
        }
      }),
    ];

    return () => {
      unsubs.forEach((fn) => fn());
    };
  }, [
    addMessage,
    addToolCall,
    addToolResult,
    appendStreamContent,
    clearSubtasks,
    handleConnectionAck,
    handleTtsPlayback,
    setMode,
    setPaused,
    setPendingQuestion,
    setProcessing,
    setThinking,
    setInterruptResult,
    setTodos,
    setContextCompressionStats,
    setHeartbeatStatus,
    updateSession,
    shouldHandleSessionEvent,
    shouldDropDuplicatedEvent,
    startStreaming,
    stopStreaming,
    updateMessage,
    updateSubtask,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectPath,
    };
    void webClient.connect(connectOptions).catch((error) => {
      const webError = error as WebError;
      setConnectionStats({ lastError: webError.message });
      onErrorRef.current?.(webError.message || 'WebSocket connection error');
    });

    return () => {
      webClient.disconnect();
      clearMessages();
      clearTodos();
      clearSubtasks();
      setConnected(false);
      // 不再重置上下文壓縮資訊，保持本地儲存的狀態
      // setContextCompressionStats(null);
      setHeartbeatStatus('unknown', null, null);
      setConnectionStats({ state: 'closed', inflight: 0 });
    };
  }, [
    apiBase,
    apiKey,
    clearMessages,
    clearSubtasks,
    clearTodos,
    model,
    projectPath,
    provider,
    setContextCompressionStats,
    setConnectionStats,
    setConnected,
    setHeartbeatStatus,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectPath,
    };
    const reconnectByDebugToggle = () => {
      void webClient.disconnect('debug mode toggled').then(() => {
        void webClient.connect(connectOptions).catch((error) => {
          const webError = error as WebError;
          setConnectionStats({ lastError: webError.message });
          onErrorRef.current?.(webError.message || 'WebSocket reconnect error');
        });
      });
    };
    window.addEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    return () => {
      window.removeEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    };
  }, [apiBase, apiKey, model, projectPath, provider, setConnectionStats]);

  useEffect(() => {
    const unsub = webClient.onStateChange((state) => {
      setConnectionState(state);
      const connected = state === 'ready';
      setIsConnected(connected);
      setConnected(connected);
      setConnectionStats({
        state,
        inflight: webClient.getInflightCount(),
        lastError: null,
      });
      if (!connected && (state === 'reconnecting' || state === 'closed')) {
        onDisconnectRef.current?.();
      }
    });
    return () => {
      unsub();
    };
  }, [setConnected, setConnectionStats]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setConnectionStats({
        inflight: webClient.getInflightCount(),
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [setConnectionStats]);

  useEffect(() => {
    markTimedOutExecutions();
    const timer = window.setInterval(() => {
      markTimedOutExecutions();
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [markTimedOutExecutions]);

  return {
    isConnected,
    connectionState,
    request,
    sendMessage,
    interrupt,
    pause,
    cancel,
    supplement,
    resume,
    switchMode,
    disconnect,
    sendUserAnswer,
    getInflightCount: () => webClient.getInflightCount(),
  };
}
