import { useEffect, useState } from 'react';
import {
    Alert,
    Button,
    Form,
    Input,
    InputNumber,
    Modal,
    Select,
    Slider,
    Space,
    Tag,
} from 'antd';
import { ApiOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { testConnection } from './api';
import type { EndpointTestResult, Provider, RoomOption, TownAgent, TownDefaults } from './types';

export type AgentFormValues = {
    name: string;
    sprite: string;
    persona: string;
    room_id: string;
    provider: Provider;
    base_url: string;
    model: string;
    api_key?: string;
    temperature: number;
    decision_interval_s: number;
    behavior_hint: string;
};

const DEFAULT_BASE_URL: Record<Provider, string> = {
    ollama: 'http://localhost:11434',
    openai: 'https://api.openai.com/v1',
    anthropic: 'https://api.anthropic.com/v1',
};

const MODEL_PLACEHOLDER: Record<Provider, string> = {
    ollama: 'qwen2.5:7b',
    openai: 'gpt-4o-mini',
    anthropic: 'claude-3-5-haiku-20241022',
};

const initialValues = (
    agent: TownAgent | undefined,
    rooms: RoomOption[],
    sprites: string[],
    defaults: TownDefaults | undefined,
): AgentFormValues => {
    const provider = agent?.llm.provider ?? defaults?.provider ?? 'ollama';
    return {
        name: agent?.name ?? '',
        sprite: agent?.sprite ?? sprites[0] ?? '',
        persona: agent?.persona ?? '',
        room_id: agent?.room_id ?? rooms[0]?.id ?? 'plaza',
        provider,
        base_url: agent?.llm.base_url ?? defaults?.base_url ?? DEFAULT_BASE_URL[provider],
        model: agent?.llm.model ?? defaults?.model ?? '',
        api_key: undefined,
        temperature: agent?.llm.temperature ?? 0.8,
        decision_interval_s: agent?.decision_interval_s ?? 8,
        behavior_hint: agent?.behavior_hint ?? defaults?.behavior_hint ?? '',
    };
};

type Props = {
    open: boolean;
    agent?: TownAgent;
    rooms: RoomOption[];
    sprites: string[];
    defaults?: TownDefaults;
    submitting: boolean;
    onCancel: () => void;
    onSubmit: (values: AgentFormValues) => void;
};

/** 新增/編輯一個小人。提交前可以先按「測試連線」確認端點通。 */
const AgentForm = ({ open, agent, rooms, sprites, defaults, submitting, onCancel, onSubmit }: Props) => {
    const { t } = useTranslation();
    const [form] = Form.useForm<AgentFormValues>();
    const [testing, setTesting] = useState(false);
    const [result, setResult] = useState<EndpointTestResult | undefined>();
    const selectedProvider = Form.useWatch('provider', form) ?? agent?.llm.provider ?? defaults?.provider ?? 'ollama';

    // form 是同一個例項被反覆重用的（Modal 只是開關，不會重新建立它），
    // 只靠 initialValues 在「已經填過值」的例項上不會重新生效——
    // 所以每次開窗都要手動把值塞回去，不然會看到上一次編輯/新增留下的舊內容。
    useEffect(() => {
        if (open) {
            form.setFieldsValue(initialValues(agent, rooms, sprites, defaults));
            setResult(undefined);
        }
    }, [open, agent, rooms, sprites, defaults, form]);

    const runTest = async () => {
        try {
            const { provider, base_url, model, api_key, temperature } = await form.validateFields([
                'provider',
                'base_url',
                'model',
            ]).then(() => form.getFieldsValue());
            setTesting(true);
            setResult(undefined);
            const outcome = await testConnection({
                provider,
                base_url,
                model,
                api_key: api_key || null,
                temperature,
            });
            setResult(outcome);
        } catch (error) {
            if (error instanceof Error) {
                setResult({ ok: false, latency_ms: null, models: [], sample_reply: null, error: error.message });
            }
        } finally {
            setTesting(false);
        }
    };

    return (
        <Modal
            open={open}
            title={agent ? t('town.form.editTitle') : t('town.form.createTitle')}
            okText={agent ? t('town.form.save') : t('town.form.create')}
            cancelText={t('town.form.cancel')}
            confirmLoading={submitting}
            onCancel={() => {
                setResult(undefined);
                onCancel();
            }}
            onOk={() => form.submit()}
            width={620}
            destroyOnHidden
        >
            <Form
                key={agent?.id ?? 'create'}
                form={form}
                layout="vertical"
                preserve={false}
                initialValues={initialValues(agent, rooms, sprites, defaults)}
                onFinish={onSubmit}
            >
                <Form.Item
                    name="name"
                    label={t('town.form.name')}
                    rules={[{ required: true, max: 40 }]}
                >
                    <Input placeholder={t('town.form.namePlaceholder')} />
                </Form.Item>

                <Space size="middle" style={{ display: 'flex' }}>
                    <Form.Item name="sprite" label={t('town.form.sprite')} style={{ flex: 1 }}>
                        <Select
                            options={sprites.map((sprite) => ({
                                value: sprite,
                                label: sprite.replace(/_/g, ' '),
                            }))}
                            showSearch
                        />
                    </Form.Item>
                    <Form.Item name="room_id" label={t('town.form.startRoom')} style={{ flex: 1 }}>
                        <Select options={rooms.map((room) => ({ value: room.id, label: room.name }))} />
                    </Form.Item>
                </Space>

                <Form.Item name="persona" label={t('town.form.persona')}>
                    <Input.TextArea rows={3} placeholder={t('town.form.personaPlaceholder')} />
                </Form.Item>

                <Form.Item
                    name="behavior_hint"
                    label={t('town.form.behaviorHint')}
                    extra={t('town.form.behaviorHintHelp')}
                >
                    <Input.TextArea rows={2} placeholder={t('town.form.behaviorHintPlaceholder')} />
                </Form.Item>

                <Space size="middle" style={{ display: 'flex' }}>
                    <Form.Item name="provider" label={t('town.form.provider')} style={{ width: 160 }}>
                        <Select
                            options={[
                                { value: 'ollama', label: 'Ollama' },
                                { value: 'openai', label: t('town.form.openaiCompatible') },
                                { value: 'anthropic', label: 'Anthropic (Claude)' },
                            ]}
                            onChange={(provider: Provider) =>
                                form.setFieldValue('base_url', DEFAULT_BASE_URL[provider])
                            }
                        />
                    </Form.Item>
                    <Form.Item
                        name="base_url"
                        label={t('town.form.baseUrl')}
                        rules={[{ required: true }]}
                        style={{ flex: 1 }}
                    >
                        <Input placeholder={DEFAULT_BASE_URL[selectedProvider]} />
                    </Form.Item>
                </Space>

                <Space size="middle" style={{ display: 'flex' }}>
                    <Form.Item
                        name="model"
                        label={t('town.form.model')}
                        rules={[{ required: true }]}
                        style={{ flex: 1 }}
                    >
                        <Input placeholder={MODEL_PLACEHOLDER[selectedProvider]} />
                    </Form.Item>
                    <Form.Item name="api_key" label={t('town.form.apiKey')} style={{ flex: 1 }}>
                        <Input.Password
                            placeholder={agent?.llm.api_key ? t('town.form.apiKeyKeep') : t('town.form.apiKeyOptional')}
                            autoComplete="off"
                        />
                    </Form.Item>
                </Space>

                <Space size="large" style={{ display: 'flex' }}>
                    <Form.Item name="temperature" label={t('town.form.temperature')} style={{ flex: 1 }}>
                        <Slider min={0} max={2} step={0.1} />
                    </Form.Item>
                    <Form.Item
                        name="decision_interval_s"
                        label={t('town.form.interval')}
                        style={{ width: 190 }}
                    >
                        <InputNumber min={2} max={600} step={1} addonAfter="s" style={{ width: '100%' }} />
                    </Form.Item>
                </Space>

                <Space direction="vertical" style={{ width: '100%' }}>
                    <Button icon={<ApiOutlined />} loading={testing} onClick={runTest}>
                        {t('town.form.testConnection')}
                    </Button>
                    {result && (
                        <Alert
                            type={result.ok ? 'success' : 'error'}
                            showIcon
                            message={
                                result.ok
                                    ? t('town.form.testOk', { latency: result.latency_ms ?? 0 })
                                    : t('town.form.testFailed')
                            }
                            description={
                                result.ok ? (
                                    <Space direction="vertical" size={2}>
                                        {result.sample_reply && (
                                            <span>{t('town.form.sampleReply', { reply: result.sample_reply })}</span>
                                        )}
                                        {result.models.length > 0 && (
                                            <span>
                                                {result.models.slice(0, 8).map((model) => (
                                                    <Tag key={model}>{model}</Tag>
                                                ))}
                                            </span>
                                        )}
                                    </Space>
                                ) : (
                                    result.error
                                )
                            }
                        />
                    )}
                </Space>
            </Form>
        </Modal>
    );
};

export default AgentForm;
