import { fetchCustom } from '../../components/fetch';
import type { EndpointTestResult, LLMEndpoint, RoomOption, TownAgent, TownDefaults } from './types';

const request = async <T>(url: string, init?: RequestInit): Promise<T> => {
    const response = await fetchCustom(url, {
        ...init,
        headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : undefined;
    if (!response.ok) {
        const detail = payload?.detail ?? response.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return payload as T;
};

export type AgentCreatePayload = {
    name: string;
    sprite: string;
    persona: string;
    room_id: string;
    llm: LLMEndpoint;
    decision_interval_s: number;
    enabled: boolean;
    behavior_hint: string;
    skip_connection_test?: boolean;
};

export type AgentUpdatePayload = Partial<Omit<AgentCreatePayload, 'skip_connection_test'>>;

export const fetchRooms = () => request<RoomOption[]>('/api/v1/town/rooms');

export const fetchDefaults = () => request<TownDefaults>('/api/v1/town/defaults');

export const fetchSprites = () => request<string[]>('/api/v1/town/sprites');

export const fetchAgents = () => request<TownAgent[]>('/api/v1/town/agents');

export const testConnection = (endpoint: LLMEndpoint) =>
    request<EndpointTestResult>('/api/v1/town/agents/test-connection', {
        method: 'POST',
        body: JSON.stringify(endpoint),
    });

export const createAgent = (payload: AgentCreatePayload) =>
    request<{ id: string; agents: TownAgent[] }>('/api/v1/town/agents', {
        method: 'POST',
        body: JSON.stringify(payload),
    });

export const updateAgent = (agentId: string, payload: AgentUpdatePayload) =>
    request<{ id: string; agents: TownAgent[] }>(`/api/v1/town/agents/${agentId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
    });

export const deleteAgent = (agentId: string) =>
    request<{ removed: string; agents: TownAgent[] }>(`/api/v1/town/agents/${agentId}`, {
        method: 'DELETE',
    });

export const sendAgentToRoom = (agentId: string, roomId: string) =>
    request<{ ok: boolean }>(`/api/v1/town/agents/${agentId}/goto`, {
        method: 'POST',
        body: JSON.stringify({ room_id: roomId }),
    });

export const dispatchAllAgents = (payload: { room_id: string; message?: string }) =>
    request<{ dispatched: number }>('/api/v1/town/agents/dispatch-all', {
        method: 'POST',
        body: JSON.stringify(payload),
    });
