export type Facing = 'up' | 'down' | 'left' | 'right';

export type Rect = { x: number; y: number; w: number; h: number };

export type Point = { x: number; y: number };

export type RoomInfo = {
    id: string;
    name: string;
    name_en: string;
    rect: Rect;
    interior: Rect;
    door: Point;
    anchor: Point;
};

export type WorldMap = {
    grid_w: number;
    grid_h: number;
    tile_size: number;
    plaza: Rect;
    corridors: Rect[];
    rooms: RoomInfo[];
    plaza_room: { id: string; name: string; name_en: string; rect: Rect; anchor: Point };
    walls: Point[];
    food: Point;
};

export type ActorSnapshot = {
    id: string;
    name: string;
    sprite: string;
    kind: 'ai' | 'human';
    x: number;
    y: number;
    facing: Facing;
    moving: boolean;
    room_id: string | null;
    room_name: string | null;
    target_room: string | null;
    status: string;
    say: string | null;
    last_error: string | null;
    hp: number;
};

export type TownEvent = {
    id: string;
    kind: 'say' | 'join' | 'leave' | 'arrive' | string;
    actor_id: string;
    actor_name: string;
    ts: number;
    text?: string;
    room_id?: string;
};

export type Provider = 'ollama' | 'openai' | 'anthropic';

export type LLMEndpoint = {
    provider: Provider;
    base_url: string;
    model: string;
    api_key?: string | null;
    temperature: number;
};

export type TownAgent = {
    id: string;
    name: string;
    sprite: string;
    persona: string;
    room_id: string;
    llm: LLMEndpoint;
    decision_interval_s: number;
    enabled: boolean;
    behavior_hint: string;
    runtime: ActorSnapshot | null;
};

export type RoomOption = { id: string; name: string; name_en: string };

export type EndpointTestResult = {
    ok: boolean;
    latency_ms: number | null;
    models: string[];
    sample_reply: string | null;
    error: string | null;
};

export type TownDefaults = {
    provider: Provider;
    base_url: string;
    model: string;
    has_api_key: boolean;
    behavior_hint: string;
};

export type ServerMessage =
    | { type: 'map'; map: WorldMap }
    | { type: 'snapshot'; tick: number; actors: ActorSnapshot[] }
    | { type: 'event'; event: TownEvent }
    | { type: 'events'; events: TownEvent[] }
    | { type: 'agent_list'; agents: TownAgent[] }
    | { type: 'joined'; actor_id: string };

export type ClientMessage =
    | { type: 'join'; name: string }
    | { type: 'input'; dir: Facing | null }
    | { type: 'say'; text: string }
    | { type: 'leave' };
