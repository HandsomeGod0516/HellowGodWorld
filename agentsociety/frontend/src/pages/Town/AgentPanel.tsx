import {
    Badge,
    Button,
    Dropdown,
    Empty,
    List,
    Popconfirm,
    Space,
    Tag,
    Tooltip,
    Typography,
} from 'antd';
import {
    DeleteOutlined,
    EditOutlined,
    PauseCircleOutlined,
    PlayCircleOutlined,
    SendOutlined,
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { RoomOption, TownAgent } from './types';

const { Text } = Typography;

type Props = {
    agents: TownAgent[];
    rooms: RoomOption[];
    busyId?: string;
    onEdit: (agent: TownAgent) => void;
    onToggle: (agent: TownAgent) => void;
    onDelete: (agent: TownAgent) => void;
    onSendTo: (agent: TownAgent, roomId: string) => void;
};

const AgentPanel = ({ agents, rooms, busyId, onEdit, onToggle, onDelete, onSendTo }: Props) => {
    const { t } = useTranslation();

    if (agents.length === 0) {
        return <Empty description={t('town.panel.empty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
    }

    return (
        <List
            className="town-agent-list"
            dataSource={agents}
            renderItem={(agent) => {
                const runtime = agent.runtime;
                const status = !agent.enabled ? 'default' : agent.runtime?.last_error ? 'error' : 'success';
                return (
                    <List.Item key={agent.id} className="town-agent-item">
                        <div className="town-agent-row">
                            <div className="town-agent-headline">
                                <Badge status={status} />
                                <Text strong>{agent.name}</Text>
                                <Tag color={agent.llm.provider === 'ollama' ? 'geekblue' : 'purple'}>
                                    {agent.llm.provider}
                                </Tag>
                                <Tooltip title={agent.llm.base_url}>
                                    <Tag>{agent.llm.model}</Tag>
                                </Tooltip>
                                <Tag>{t('town.panel.every', { seconds: agent.decision_interval_s })}</Tag>
                            </div>

                            <div className="town-agent-meta">
                                <Text type="secondary">
                                    {runtime?.room_name ?? t('town.panel.corridor')}
                                    {runtime?.target_room
                                        ? ` → ${rooms.find((room) => room.id === runtime.target_room)?.name ?? runtime.target_room}`
                                        : ''}
                                </Text>
                                <Text type="secondary">{runtime?.status ?? '-'}</Text>
                            </div>

                            {runtime?.last_error && (
                                <Text type="danger" className="town-agent-error">
                                    {runtime.last_error}
                                </Text>
                            )}

                            <Space size={4} wrap>
                                <Dropdown
                                    menu={{
                                        items: rooms.map((room) => ({ key: room.id, label: room.name })),
                                        onClick: ({ key }) => onSendTo(agent, key),
                                    }}
                                >
                                    <Button size="small" icon={<SendOutlined />}>
                                        {t('town.panel.sendTo')}
                                    </Button>
                                </Dropdown>
                                <Button
                                    size="small"
                                    icon={agent.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                                    loading={busyId === agent.id}
                                    onClick={() => onToggle(agent)}
                                >
                                    {agent.enabled ? t('town.panel.pause') : t('town.panel.resume')}
                                </Button>
                                <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(agent)}>
                                    {t('town.panel.edit')}
                                </Button>
                                <Popconfirm
                                    title={t('town.panel.deleteConfirm', { name: agent.name })}
                                    okText={t('town.panel.deleteOk')}
                                    cancelText={t('town.form.cancel')}
                                    onConfirm={() => onDelete(agent)}
                                >
                                    <Button size="small" danger icon={<DeleteOutlined />}>
                                        {t('town.panel.delete')}
                                    </Button>
                                </Popconfirm>
                            </Space>
                        </div>
                    </List.Item>
                );
            }}
        />
    );
};

export default AgentPanel;
