import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge, Button, Card, Dropdown, Empty, Input, Layout, Space, Tag, Typography, message } from 'antd';
import type { InputRef } from 'antd';
import {
    LoginOutlined,
    LogoutOutlined,
    NotificationOutlined,
    PlusOutlined,
    SendOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import LanguageToggle from '../../components/LanguageToggle';
import AgentForm, { type AgentFormValues } from './AgentForm';
import AgentPanel from './AgentPanel';
import {
    createAgent,
    deleteAgent,
    dispatchAllAgents,
    fetchDefaults,
    fetchRooms,
    fetchSprites,
    sendAgentToRoom,
    updateAgent,
} from './api';
import { createTownGame } from './scene';
import type { Facing, RoomOption, TownAgent, TownDefaults, TownEvent } from './types';
import { useTownSocket } from './useTownSocket';
import './style.css';

const { Content } = Layout;
const { Text, Title } = Typography;

const KEY_TO_FACING: Record<string, Facing> = {
    w: 'up',
    a: 'left',
    s: 'down',
    d: 'right',
    arrowup: 'up',
    arrowleft: 'left',
    arrowdown: 'down',
    arrowright: 'right',
};

/**
 * 中文/注音等輸入法用 Enter 確認候選字時也會觸發 onPressEnter。
 * Firefox 這種 Enter 的 keydown 上 isComposing 還是 true，能直接擋掉；
 * 但 Chrome 會先送 compositionend 才送這次 keydown，屆時 isComposing 已經是 false，
 * 所以還要靠 compositionend 之後一小段時間內的 Enter 一起過濾掉。
 */
const IME_COMMIT_GUARD_MS = 100;

const isComposingKeyEvent = (event: { nativeEvent: KeyboardEvent }): boolean => {
    const native = event.nativeEvent;
    return native.isComposing || native.keyCode === 229;
};

const isTypingTarget = (): boolean => {
    const active = document.activeElement;
    if (!active) {
        return false;
    }
    const tag = active.tagName.toLowerCase();
    return tag === 'input' || tag === 'textarea' || (active as HTMLElement).isContentEditable;
};

const describeEvent = (event: TownEvent, t: ReturnType<typeof useTranslation>['t']): string => {
    switch (event.kind) {
        case 'say':
            return `${event.actor_name}：${event.text ?? ''}`;
        case 'join':
            return t('town.events.join', { name: event.actor_name });
        case 'leave':
            return t('town.events.leave', { name: event.actor_name });
        case 'arrive':
            return t('town.events.arrive', { name: event.actor_name, room: event.room_id ?? '' });
        case 'announce':
            return t('town.events.announce', { text: event.text ?? '' });
        case 'starve':
            return t('town.events.starve', { name: event.actor_name });
        default:
            return `${event.actor_name} · ${event.kind}`;
    }
};

const TownPage = () => {
    const { t } = useTranslation();
    const [messageApi, messageHolder] = message.useMessage();
    const socket = useTownSocket();

    const canvasRef = useRef<HTMLDivElement>(null);
    const gameRef = useRef<ReturnType<typeof createTownGame> | undefined>(undefined);
    const chatInputRef = useRef<InputRef>(null);
    const compositionEndedAtRef = useRef(0);

    const handleCompositionEnd = useCallback(() => {
        compositionEndedAtRef.current = Date.now();
    }, []);

    const shouldSkipEnter = useCallback(
        (event: { nativeEvent: KeyboardEvent }) =>
            isComposingKeyEvent(event) || Date.now() - compositionEndedAtRef.current < IME_COMMIT_GUARD_MS,
        [],
    );

    const [rooms, setRooms] = useState<RoomOption[]>([]);
    const [sprites, setSprites] = useState<string[]>([]);
    const [defaults, setDefaults] = useState<TownDefaults | undefined>();
    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState<TownAgent | undefined>();
    const [submitting, setSubmitting] = useState(false);
    const [busyId, setBusyId] = useState<string | undefined>();
    const [playerName, setPlayerName] = useState('');
    const [chatText, setChatText] = useState('');

    useEffect(() => {
        Promise.all([fetchRooms(), fetchSprites(), fetchDefaults()])
            .then(([roomList, spriteList, formDefaults]) => {
                setRooms(roomList);
                setSprites(spriteList);
                setDefaults(formDefaults);
            })
            .catch((error: Error) => messageApi.error(error.message));
    }, [messageApi]);

    // 地圖與角色圖集都就位後再建 Phaser，只建一次。
    useEffect(() => {
        const parent = canvasRef.current;
        if (!socket.worldMap || sprites.length === 0 || !parent || gameRef.current) {
            return;
        }
        gameRef.current = createTownGame(
            parent,
            socket.worldMap,
            sprites,
            () => socket.actorsRef.current,
        );
    }, [socket.worldMap, socket.actorsRef, sprites]);

    useEffect(
        () => () => {
            gameRef.current?.game.destroy(true);
            gameRef.current = undefined;
        },
        [],
    );

    useEffect(() => {
        gameRef.current?.scene.setFollow(socket.humanId);
    }, [socket.humanId]);

    // WASD / 方向鍵直接驅動自己的角色。
    const { humanId, send } = socket;
    useEffect(() => {
        if (!humanId) {
            return;
        }
        const pressed: Facing[] = [];

        const onKeyDown = (event: KeyboardEvent) => {
            if (isTypingTarget()) {
                return;
            }
            const facing = KEY_TO_FACING[event.key.toLowerCase()];
            if (!facing) {
                return;
            }
            event.preventDefault();
            if (!pressed.includes(facing)) {
                pressed.push(facing);
            }
            send({ type: 'input', dir: facing });
        };

        const onKeyUp = (event: KeyboardEvent) => {
            const facing = KEY_TO_FACING[event.key.toLowerCase()];
            if (!facing) {
                return;
            }
            const index = pressed.indexOf(facing);
            if (index >= 0) {
                pressed.splice(index, 1);
            }
            send({ type: 'input', dir: pressed[pressed.length - 1] ?? null });
        };

        const onBlur = () => {
            pressed.length = 0;
            send({ type: 'input', dir: null });
        };

        window.addEventListener('keydown', onKeyDown);
        window.addEventListener('keyup', onKeyUp);
        window.addEventListener('blur', onBlur);
        return () => {
            window.removeEventListener('keydown', onKeyDown);
            window.removeEventListener('keyup', onKeyUp);
            window.removeEventListener('blur', onBlur);
        };
    }, [humanId, send]);

    const handleSubmit = useCallback(
        async (values: AgentFormValues) => {
            setSubmitting(true);
            try {
                const llm = {
                    provider: values.provider,
                    base_url: values.base_url,
                    model: values.model,
                    api_key: values.api_key || null,
                    temperature: values.temperature,
                };
                if (editing) {
                    await updateAgent(editing.id, {
                        name: values.name,
                        sprite: values.sprite,
                        persona: values.persona,
                        llm,
                        decision_interval_s: values.decision_interval_s,
                        behavior_hint: values.behavior_hint,
                    });
                    messageApi.success(t('town.messages.updated', { name: values.name }));
                } else {
                    await createAgent({
                        name: values.name,
                        sprite: values.sprite,
                        persona: values.persona,
                        room_id: values.room_id,
                        llm,
                        decision_interval_s: values.decision_interval_s,
                        enabled: true,
                        behavior_hint: values.behavior_hint,
                    });
                    messageApi.success(t('town.messages.created', { name: values.name }));
                }
                setFormOpen(false);
                setEditing(undefined);
            } catch (error) {
                messageApi.error((error as Error).message);
            } finally {
                setSubmitting(false);
            }
        },
        [editing, messageApi, t],
    );

    const handleToggle = useCallback(
        async (agent: TownAgent) => {
            setBusyId(agent.id);
            try {
                await updateAgent(agent.id, { enabled: !agent.enabled });
            } catch (error) {
                messageApi.error((error as Error).message);
            } finally {
                setBusyId(undefined);
            }
        },
        [messageApi],
    );

    const handleDelete = useCallback(
        async (agent: TownAgent) => {
            try {
                await deleteAgent(agent.id);
                messageApi.success(t('town.messages.deleted', { name: agent.name }));
            } catch (error) {
                messageApi.error((error as Error).message);
            }
        },
        [messageApi, t],
    );

    const handleSendTo = useCallback(
        async (agent: TownAgent, roomId: string) => {
            try {
                await sendAgentToRoom(agent.id, roomId);
            } catch (error) {
                messageApi.error((error as Error).message);
            }
        },
        [messageApi],
    );

    const handleDispatchAll = useCallback(
        async (roomId: string) => {
            const room = rooms.find((option) => option.id === roomId);
            const roomName = room?.name ?? roomId;
            try {
                const result = await dispatchAllAgents({
                    room_id: roomId,
                    message: t('town.panel.dispatchMessage', { room: roomName }),
                });
                messageApi.success(t('town.messages.dispatched', { count: result.dispatched, room: roomName }));
            } catch (error) {
                messageApi.error((error as Error).message);
            }
        },
        [rooms, messageApi, t],
    );

    const sendChat = () => {
        const text = chatText.trim();
        // 沒打字就按 Enter／送出：直接收起輸入框還給遊戲，不要讓玩家卡在打字狀態走不動。
        if (!text || !socket.humanId) {
            chatInputRef.current?.blur();
            return;
        }
        socket.send({ type: 'say', text });
        setChatText('');
        // 送出後交回焦點給遊戲畫布，WASD 才能馬上繼續走動。
        chatInputRef.current?.blur();
    };

    const humanCount = socket.actors.filter((actor) => actor.kind === 'human').length;

    return (
        <Layout className="town-layout">
            {messageHolder}
            <Content className="town-content">
                <div className="town-stage">
                    <div className="town-stage-bar">
                        <Space size="small" wrap>
                            <Title level={5} className="town-title">
                                {t('town.title')}
                            </Title>
                            <Badge
                                status={socket.connected ? 'success' : 'error'}
                                text={socket.connected ? t('town.connected') : t('town.disconnected')}
                            />
                            <Tag>{t('town.stats.agents', { count: socket.agents.length })}</Tag>
                            <Tag>{t('town.stats.players', { count: humanCount })}</Tag>
                            <LanguageToggle type="text" size="small" showLabel={false} />
                        </Space>
                        <Space size="small" wrap>
                            {socket.humanId ? (
                                <Button icon={<LogoutOutlined />} onClick={socket.leave}>
                                    {t('town.leave')}
                                </Button>
                            ) : (
                                <>
                                    <Input
                                        className="town-name-input"
                                        value={playerName}
                                        placeholder={t('town.namePlaceholder')}
                                        maxLength={40}
                                        onChange={(event) => setPlayerName(event.target.value)}
                                        onCompositionEnd={handleCompositionEnd}
                                        onPressEnter={(event) => {
                                            if (shouldSkipEnter(event)) {
                                                return;
                                            }
                                            socket.send({ type: 'join', name: playerName || t('town.guest') });
                                        }}
                                    />
                                    <Button
                                        type="primary"
                                        icon={<LoginOutlined />}
                                        disabled={!socket.connected}
                                        onClick={() =>
                                            socket.send({ type: 'join', name: playerName || t('town.guest') })
                                        }
                                    >
                                        {t('town.join')}
                                    </Button>
                                </>
                            )}
                        </Space>
                    </div>
                    <div ref={canvasRef} className="town-canvas" />
                    {socket.humanId && <div className="town-hint">{t('town.wasdHint')}</div>}
                    {socket.humanId && (
                        <div className="town-chat-bar">
                            <Input
                                ref={chatInputRef}
                                className="town-chat-input"
                                value={chatText}
                                placeholder={t('town.chatPlaceholder')}
                                maxLength={200}
                                onChange={(event) => setChatText(event.target.value)}
                                onCompositionEnd={handleCompositionEnd}
                                onPressEnter={(event) => {
                                    if (shouldSkipEnter(event)) {
                                        return;
                                    }
                                    sendChat();
                                }}
                                onKeyDown={(event) => {
                                    if (event.key === 'Escape') {
                                        chatInputRef.current?.blur();
                                    }
                                }}
                            />
                            <Button
                                className="town-chat-send"
                                icon={<SendOutlined />}
                                onClick={sendChat}
                            >
                                {t('town.say')}
                            </Button>
                        </div>
                    )}
                </div>

                <aside className="town-side">
                    <Card
                        size="small"
                        title={t('town.panel.title')}
                        extra={
                            <Space size="small">
                                <Dropdown
                                    disabled={socket.agents.length === 0}
                                    menu={{
                                        items: rooms.map((room) => ({ key: room.id, label: room.name })),
                                        onClick: ({ key }) => handleDispatchAll(key),
                                    }}
                                >
                                    <Button size="small" icon={<NotificationOutlined />}>
                                        {t('town.panel.dispatchAll')}
                                    </Button>
                                </Dropdown>
                                <Button
                                    type="primary"
                                    size="small"
                                    icon={<PlusOutlined />}
                                    onClick={() => {
                                        setEditing(undefined);
                                        setFormOpen(true);
                                    }}
                                >
                                    {t('town.panel.add')}
                                </Button>
                            </Space>
                        }
                        className="town-card town-card-agents"
                    >
                        <AgentPanel
                            agents={socket.agents}
                            rooms={rooms}
                            busyId={busyId}
                            onEdit={(agent) => {
                                setEditing(agent);
                                setFormOpen(true);
                            }}
                            onToggle={handleToggle}
                            onDelete={handleDelete}
                            onSendTo={handleSendTo}
                        />
                    </Card>

                    <Card size="small" title={t('town.log.title')} className="town-card town-card-log">
                        {socket.events.length === 0 ? (
                            <Empty description={t('town.log.empty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
                        ) : (
                            <div className="town-log">
                                {[...socket.events].reverse().map((event) => (
                                    <div key={event.id} className={`town-log-line kind-${event.kind}`}>
                                        <Text>{describeEvent(event, t)}</Text>
                                    </div>
                                ))}
                            </div>
                        )}
                    </Card>
                </aside>
            </Content>

            <AgentForm
                open={formOpen}
                agent={editing}
                rooms={rooms}
                sprites={sprites}
                defaults={defaults}
                submitting={submitting}
                onCancel={() => {
                    setFormOpen(false);
                    setEditing(undefined);
                }}
                onSubmit={handleSubmit}
            />
        </Layout>
    );
};

export default TownPage;
