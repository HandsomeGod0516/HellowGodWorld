import { useCallback, useEffect, useRef, useState } from 'react';
import type React from 'react';
import { resolveAppUrl } from '../../components/fetch';
import type {
    ActorSnapshot,
    ClientMessage,
    ServerMessage,
    TownAgent,
    TownEvent,
    WorldMap,
} from './types';

const EVENT_LIMIT = 200;
const RECONNECT_DELAY_MS = 2000;
/** 面板文字只需要低頻重新整理；畫布走 actorsRef。 */
const STATE_THROTTLE_MS = 400;

const socketUrl = (): string => {
    const path = resolveAppUrl('/api/v1/town/ws');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${path}`;
};

export type TownSocket = {
    connected: boolean;
    worldMap: WorldMap | undefined;
    /** 每幀渲染用的最新快照，不進 React state，避免 10Hz 重渲染整個頁面。 */
    actorsRef: React.MutableRefObject<ActorSnapshot[]>;
    actors: ActorSnapshot[];
    agents: TownAgent[];
    events: TownEvent[];
    humanId: string | undefined;
    send: (message: ClientMessage) => void;
    leave: () => void;
};

/**
 * 訂閱世界快照。斷線自動重連，重連後服務端會重新推送地圖與全量快照。
 */
export const useTownSocket = (): TownSocket => {
    const socketRef = useRef<WebSocket | undefined>(undefined);
    const reconnectRef = useRef<number | undefined>(undefined);
    const closedByUsRef = useRef(false);
    const actorsRef = useRef<ActorSnapshot[]>([]);
    const lastStatePushRef = useRef(0);

    const [connected, setConnected] = useState(false);
    const [worldMap, setWorldMap] = useState<WorldMap | undefined>();
    const [actors, setActors] = useState<ActorSnapshot[]>([]);
    const [agents, setAgents] = useState<TownAgent[]>([]);
    const [events, setEvents] = useState<TownEvent[]>([]);
    const [humanId, setHumanId] = useState<string | undefined>();

    const send = useCallback((message: ClientMessage) => {
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(message));
        }
    }, []);

    // 服務端不會為「leave」回一條確認訊息，這裡本地立刻清掉 humanId，
    // 不然介面會一直停在“已加入”狀態，玩家沒法再次進入小鎮。
    const leave = useCallback(() => {
        send({ type: 'leave' });
        setHumanId(undefined);
    }, [send]);

    useEffect(() => {
        closedByUsRef.current = false;

        const connect = () => {
            const socket = new WebSocket(socketUrl());
            socketRef.current = socket;

            socket.onopen = () => setConnected(true);

            socket.onmessage = (raw) => {
                let payload: ServerMessage;
                try {
                    payload = JSON.parse(raw.data as string) as ServerMessage;
                } catch {
                    return;
                }
                switch (payload.type) {
                    case 'map':
                        setWorldMap(payload.map);
                        break;
                    case 'snapshot': {
                        actorsRef.current = payload.actors;
                        const now = performance.now();
                        if (now - lastStatePushRef.current >= STATE_THROTTLE_MS) {
                            lastStatePushRef.current = now;
                            setActors(payload.actors);
                        }
                        break;
                    }
                    case 'agent_list':
                        setAgents(payload.agents);
                        break;
                    case 'events':
                        setEvents(payload.events.slice(-EVENT_LIMIT));
                        break;
                    case 'event':
                        setEvents((previous) => [...previous, payload.event].slice(-EVENT_LIMIT));
                        break;
                    case 'joined':
                        setHumanId(payload.actor_id);
                        break;
                    default:
                        break;
                }
            };

            socket.onclose = () => {
                setConnected(false);
                setHumanId(undefined);
                if (!closedByUsRef.current) {
                    reconnectRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
                }
            };

            socket.onerror = () => socket.close();
        };

        connect();

        return () => {
            closedByUsRef.current = true;
            if (reconnectRef.current) {
                window.clearTimeout(reconnectRef.current);
            }
            socketRef.current?.close();
        };
    }, []);

    return { connected, worldMap, actorsRef, actors, agents, events, humanId, send, leave };
};
