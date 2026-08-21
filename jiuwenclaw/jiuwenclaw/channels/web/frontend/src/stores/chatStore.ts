/**
 * 聊天狀態管理
 */

import { create } from 'zustand';
import {
  Message,
  ToolCall,
  ToolResult,
  ToolExecution,
  ToolExecutionStatus,
  InterruptResultPayload,
  SubtaskUpdatePayload,
  AskUserQuestionPayload,
  EvolutionStatusPayload,
  UsageSummary,
} from '../types';
import { useTodoStore } from './todoStore';

const TOOL_TIMEOUT_MS = 12_000_000;
const EVOLUTION_STATUS_END_VISIBLE_MS = 3_000;
let evolutionStatusClearTimer: ReturnType<typeof setTimeout> | null = null;

function computeTimeoutAt(baseIso: string): string {
  return new Date(Date.parse(baseIso) + TOOL_TIMEOUT_MS).toISOString();
}

function resolveExecutionStatus(result: ToolResult): ToolExecutionStatus {
  return result.success ? 'completed' : 'error';
}

/**
 * 子任務狀態
 */
export interface SubtaskState {
  task_id: string;
  description: string;
  status: string;
  index: number;
  total: number;
  tool_name?: string;
  tool_count: number;
  message?: string;
  is_parallel: boolean;
}

interface TaskItem {
  id: string;
  content: string;
  timestamp: number;
}

interface ChatState {
  messages: Message[];
  isProcessing: boolean;
  isThinking: boolean;  // 思考中狀態（顯示閃爍動畫）
  evolutionStatus: EvolutionStatusPayload | null;
  isPaused: boolean;    // 任務是否暫停
  pausedTask: string | null;  // 暫停的任務描述
  interruptResult: InterruptResultPayload | null;  // 最近的中斷結果
  switchingMode: boolean;  // 是否正在切換模式
  currentStreamContent: string;
  currentStreamId: string | null;
  streamBuffers: Map<string, string>;
  activeSubtasks: Map<string, SubtaskState>;  // 活躍的子任務
  toolExecutions: Map<string, ToolExecution>;
  toolExecutionOrder: string[];
  orphanResults: Map<string, ToolResult>;
  toolMetrics: {
    toolCallDedupDropped: number;
    toolResultDedupDropped: number;
  };
  // 任務佇列
  taskQueue: TaskItem[];
  // 使用者問題相關
  pendingQuestion: AskUserQuestionPayload | null;  // 待回答的問題
  // 輸入框內容
  inputValue: string;

  // Actions
  addMessage: (message: Message) => void;
  updateMessage: (id: string, updates: Partial<Message>) => void;
  appendStreamContent: (content: string, streamKey?: string) => void;
  startStreaming: (messageId: string, streamKey?: string) => void;
  stopStreaming: (streamKey?: string) => void;
  setProcessing: (status: boolean) => void;
  setThinking: (status: boolean) => void;
  setEvolutionStatus: (status: EvolutionStatusPayload | null) => void;
  setPaused: (paused: boolean, task?: string | null) => void;
  setInterruptResult: (result: InterruptResultPayload | null) => void;
  setSwitchingMode: (switching: boolean) => void;
  addToolCall: (toolCall: ToolCall, options?: { startedAt?: string }) => void;
  addToolResult: (toolResult: ToolResult, options?: { updatedAt?: string }) => void;
  markTimedOutExecutions: () => void;
  updateSubtask: (payload: SubtaskUpdatePayload) => void;
  clearSubtasks: () => void;
  clearMessages: () => void;
  /** 在列表頭部插入更早的歷史訊息（陣列內建議時間升序） */
  prependMessages: (olderFirst: Message[]) => void;
  // 任務佇列相關
  addToTaskQueue: (content: string) => void;
  clearTaskQueue: () => void;
  removeFromTaskQueue: (id: string) => void;
  // 使用者問題相關
  setPendingQuestion: (question: AskUserQuestionPayload | null) => void;
  // 輸入框相關
  setInputValue: (value: string) => void;
  // Usage summary
  setUsageSummary: (messageId: string, usage: UsageSummary) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isProcessing: false,
  isThinking: false,
  evolutionStatus: null,
  isPaused: false,
  pausedTask: null,
  interruptResult: null,
  switchingMode: false,
  currentStreamContent: '',
  currentStreamId: null,
  streamBuffers: new Map(),
  activeSubtasks: new Map(),
  toolExecutions: new Map(),
  toolExecutionOrder: [],
  orphanResults: new Map(),
  toolMetrics: {
    toolCallDedupDropped: 0,
    toolResultDedupDropped: 0,
  },
  taskQueue: [],
  pendingQuestion: null,
  inputValue: '',

  addMessage: (message) => {
    set((state) => ({
      messages: [...state.messages, message],
    }));
  },

  updateMessage: (id, updates) => {
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, ...updates } : msg
      ),
    }));
  },

  appendStreamContent: (content, streamKey = 'default') => {
    const { currentStreamId } = get();
    if (!currentStreamId) return;

    const existingBuffer = get().streamBuffers.get(streamKey) || '';
    const nextContent = existingBuffer + content;

    set((state) => ({
      currentStreamContent: nextContent,
      streamBuffers: new Map(state.streamBuffers).set(streamKey, nextContent),
      messages: state.messages.map((msg) =>
        msg.id === currentStreamId
          ? { ...msg, content: nextContent }
          : msg
      ),
    }));
  },

  startStreaming: (messageId, streamKey = 'default') => {
    set((state) => ({
      currentStreamId: messageId,
      currentStreamContent: '',
      streamBuffers: new Map(state.streamBuffers).set(streamKey, ''),
    }));
  },

  stopStreaming: (streamKey = 'default') => {
    const { currentStreamId } = get();
    if (currentStreamId) {
      set((state) => ({
        messages: state.messages.map((msg) =>
          msg.id === currentStreamId ? { ...msg, isStreaming: false } : msg
        ),
        currentStreamId: null,
        currentStreamContent: '',
        streamBuffers: new Map(state.streamBuffers).set(streamKey, ''),
      }));
    }
  },

  setProcessing: (status) => {
    set({ isProcessing: status });
  },

  setThinking: (status) => {
    set({ isThinking: status });
  },

  setEvolutionStatus: (status) => {
    if (evolutionStatusClearTimer) {
      clearTimeout(evolutionStatusClearTimer);
      evolutionStatusClearTimer = null;
    }
    set({ evolutionStatus: status });
    if (status?.status === 'end') {
      evolutionStatusClearTimer = setTimeout(() => {
        set((state) => {
          if (state.evolutionStatus === status) {
            return { evolutionStatus: null };
          }
          return {};
        });
        evolutionStatusClearTimer = null;
      }, EVOLUTION_STATUS_END_VISIBLE_MS);
    }
  },

  setPaused: (paused, task = null) => {
    set({ isPaused: paused, pausedTask: task ?? null });
  },

  setInterruptResult: (result) => {
    set({ interruptResult: result });
    // 3 秒後自動清除中斷結果提示
    if (result) {
      setTimeout(() => {
        set((state) => {
          // 只有當前結果沒有變化時才清除
          if (state.interruptResult === result) {
            return { interruptResult: null };
          }
          return {};
        });
      }, 3000);
    }
  },

  setSwitchingMode: (switching) => {
    // 切換模式時，同時重置所有相關狀態
    if (switching) {
      set({ 
        switchingMode: true,
        isProcessing: false,
        isPaused: false,
        pausedTask: null,
        interruptResult: null
      });
    } else {
      set({ switchingMode: false });
    }
  },

  addToolCall: (toolCall, options) => {
    set((state) => {
      if (!toolCall.id) {
        const nextDropped = state.toolMetrics.toolCallDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolCallDedupDropped', {
            count: nextDropped,
            reason: 'missing toolCallId',
          });
        }
        return {
          ...state,
          toolMetrics: {
            ...state.toolMetrics,
            toolCallDedupDropped: nextDropped,
          },
        };
      }
      if (state.toolExecutions.has(toolCall.id)) {
        const nextDropped = state.toolMetrics.toolCallDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolCallDedupDropped', {
            count: nextDropped,
            reason: 'toolCallId execution hit',
          });
        }
        return {
          ...state,
          toolMetrics: {
            ...state.toolMetrics,
            toolCallDedupDropped: nextDropped,
          },
        };
      }
      const nowIso = new Date().toISOString();
      const startedAt =
        typeof options?.startedAt === 'string' && options.startedAt.trim()
          ? options.startedAt.trim()
          : nowIso;
      const orphanResult = state.orphanResults.get(toolCall.id);
      const nextExecutions = new Map(state.toolExecutions);
      const nextOrphanResults = new Map(state.orphanResults);
      if (orphanResult) {
        nextOrphanResults.delete(toolCall.id);
      }
      const timeoutAt = computeTimeoutAt(startedAt);
      const resultStatus = orphanResult ? resolveExecutionStatus(orphanResult) : 'pending';
      nextExecutions.set(toolCall.id, {
        toolCallId: toolCall.id,
        toolCall,
        result: orphanResult,
        status: resultStatus,
        startedAt,
        updatedAt: startedAt,
        timeoutAt,
      });

      const nextOrder = [...state.toolExecutionOrder, toolCall.id];
      return {
        toolExecutions: nextExecutions,
        toolExecutionOrder: nextOrder,
        orphanResults: nextOrphanResults,
      };
    });
  },

  addToolResult: (toolResult, options) => {
    set((state) => {
      const incomingToolCallId = toolResult.toolCallId;
      if (!incomingToolCallId) {
        const nextDropped = state.toolMetrics.toolResultDedupDropped + 1;
        if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
          console.debug('[ws][metrics] toolResultDedupDropped', {
            count: nextDropped,
            reason: 'missing toolCallId',
          });
        }
        return {
          ...state,
          toolMetrics: {
            ...state.toolMetrics,
            toolResultDedupDropped: nextDropped,
          },
        };
      }
      const nowIso = new Date().toISOString();
      const updatedAt =
        typeof options?.updatedAt === 'string' && options.updatedAt.trim()
          ? options.updatedAt.trim()
          : nowIso;
      const existingExecution = state.toolExecutions.get(incomingToolCallId);

      if (!existingExecution) {
        const nextOrphanResults = new Map(state.orphanResults);
        const duplicatedOrphan = nextOrphanResults.get(incomingToolCallId);
        if (
          duplicatedOrphan &&
          duplicatedOrphan.result === toolResult.result &&
          duplicatedOrphan.success === toolResult.success &&
          (duplicatedOrphan.summary || '') === (toolResult.summary || '')
        ) {
          const nextDropped = state.toolMetrics.toolResultDedupDropped + 1;
          if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
            console.debug('[ws][metrics] toolResultDedupDropped', {
              count: nextDropped,
              reason: 'orphan duplicate',
            });
          }
          return {
            ...state,
            toolMetrics: {
              ...state.toolMetrics,
              toolResultDedupDropped: nextDropped,
            },
          };
        }
        nextOrphanResults.set(incomingToolCallId, toolResult);
        return {
          orphanResults: nextOrphanResults,
        };
      }

      if (existingExecution.result) {
        const duplicated =
          existingExecution.result.result === toolResult.result &&
          existingExecution.result.success === toolResult.success &&
          (existingExecution.result.summary || '') === (toolResult.summary || '');
        if (duplicated) {
          const nextDropped = state.toolMetrics.toolResultDedupDropped + 1;
          if (import.meta.env.DEV && (nextDropped === 1 || nextDropped % 10 === 0)) {
            console.debug('[ws][metrics] toolResultDedupDropped', {
              count: nextDropped,
              reason: 'execution duplicate',
            });
          }
          return {
            ...state,
            toolMetrics: {
              ...state.toolMetrics,
              toolResultDedupDropped: nextDropped,
            },
          };
        }
      }

      const nextExecutions = new Map(state.toolExecutions);
      const nextStatus = resolveExecutionStatus(toolResult);
      nextExecutions.set(incomingToolCallId, {
        ...existingExecution,
        result: toolResult,
        status: nextStatus,
        updatedAt,
        resultArrivedAfterTimeout:
          existingExecution.status === 'timeout' ? true : existingExecution.resultArrivedAfterTimeout,
      });
      return {
        toolExecutions: nextExecutions,
      };
    });
  },

  markTimedOutExecutions: () => {
    const now = Date.now();
    set((state) => {
      let changed = false;
      const nextExecutions = new Map(state.toolExecutions);
      for (const [toolCallId, execution] of nextExecutions) {
        if (execution.status !== 'pending') {
          continue;
        }
        const timeoutTs = Date.parse(execution.timeoutAt);
        if (Number.isNaN(timeoutTs) || timeoutTs > now) {
          continue;
        }
        changed = true;
        nextExecutions.set(toolCallId, {
          ...execution,
          status: 'timeout',
          timedOutAt: new Date(now).toISOString(),
          updatedAt: new Date(now).toISOString(),
        });
      }
      if (!changed) {
        return state;
      }
      return {
        ...state,
        toolExecutions: nextExecutions,
      };
    });
  },

  updateSubtask: (payload: SubtaskUpdatePayload) => {
    set((state) => {
      const newSubtasks = new Map(state.activeSubtasks);
      
      if (payload.status === 'completed' || payload.status === 'error') {
        // 任務完成或出錯，從活躍列表中移除
        newSubtasks.delete(payload.task_id);
      } else {
        // 更新或新增子任務狀態
        newSubtasks.set(payload.task_id, {
          task_id: payload.task_id,
          description: payload.description,
          status: payload.status,
          index: payload.index,
          total: payload.total,
          tool_name: payload.tool_name,
          tool_count: payload.tool_count || 0,
          message: payload.message,
          is_parallel: payload.is_parallel || false,
        });
      }
      
      return { activeSubtasks: newSubtasks };
    });

    // 同時更新 todoStore 中對應任務的 activeForm（如果能匹配）
    const todoState = useTodoStore.getState();
    const { todos, setTodos } = todoState;
    
    // 嘗試匹配子任務描述和 todo 內容
    const matchingTodo = todos.find(
      (todo) =>
        todo.status === 'in_progress' &&
        (todo.content.includes(payload.description) ||
         payload.description.includes(todo.content.slice(0, 20)))
    );
    
    if (matchingTodo) {
      let activeForm = '';
      if (payload.status === 'starting') {
        activeForm = `正在${payload.description}...`;
      } else if (payload.status === 'tool_call') {
        activeForm = `正在呼叫 ${payload.tool_name}...`;
      } else if (payload.status === 'completed') {
        activeForm = '';  // 清除
      }
      
      if (activeForm || payload.status === 'completed') {
        const updatedTodos = todos.map((todo) =>
          todo.id === matchingTodo.id
            ? { ...todo, activeForm }
            : todo
        );
        setTodos(updatedTodos);
      }
    }
  },

  clearSubtasks: () => {
    set({ activeSubtasks: new Map() });
  },

  prependMessages: (olderFirst) => {
    if (!olderFirst.length) {
      return;
    }
    set((state) => ({
      messages: [...olderFirst, ...state.messages],
    }));
  },

  clearMessages: () => {
    if (evolutionStatusClearTimer) {
      clearTimeout(evolutionStatusClearTimer);
      evolutionStatusClearTimer = null;
    }
    set({
      messages: [],
      currentStreamContent: '',
      currentStreamId: null,
      streamBuffers: new Map(),
      evolutionStatus: null,
      isPaused: false,
      pausedTask: null,
      interruptResult: null,
      switchingMode: false,
      activeSubtasks: new Map(),
      toolExecutions: new Map(),
      toolExecutionOrder: [],
      orphanResults: new Map(),
      toolMetrics: {
        toolCallDedupDropped: 0,
        toolResultDedupDropped: 0,
      },
      taskQueue: [],
      pendingQuestion: null,
    });
  },

  addToTaskQueue: (content) => {
    set((state) => ({
      taskQueue: [
        ...state.taskQueue,
        {
          id: Date.now().toString() + Math.random().toString(36).substr(2, 9),
          content,
          timestamp: Date.now(),
        },
      ],
    }));
  },

  clearTaskQueue: () => {
    set({ taskQueue: [] });
  },

  removeFromTaskQueue: (id) => {
    set((state) => ({
      taskQueue: state.taskQueue.filter((task) => task.id !== id),
    }));
  },

  setPendingQuestion: (question) => {
    set({ pendingQuestion: question });
  },
  
  setInputValue: (value) => {
    set({ inputValue: value });
  },

  setUsageSummary: (messageId, usage) => {
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === messageId ? { ...msg, usageSummary: usage } : msg
      ),
    }));
  },
}));
