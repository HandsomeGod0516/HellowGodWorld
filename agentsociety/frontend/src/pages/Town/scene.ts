import Phaser from 'phaser';
import { resolveAppUrl } from '../../components/fetch';
import type { ActorSnapshot, Facing, Rect, WorldMap } from './types';

const CHARACTER_ROOT = resolveAppUrl('/pixel-town/characters');
const FRAME_SIZE = 32;
const SPRITE_SCALE = 1.15;
const FONT_FAMILY = 'Arial, "PingFang SC", "Microsoft YaHei", sans-serif';

const IDLE_FRAMES: Record<Facing, number> = { down: 1, left: 4, right: 7, up: 10 };
const WALK_FRAMES: Record<Facing, number[]> = {
    down: [0, 1, 2, 1],
    left: [3, 4, 5, 4],
    right: [6, 7, 8, 7],
    up: [9, 10, 11, 10],
};

const COLORS = {
    void: 0x16232a,
    corridor: 0xcfe3e0,
    plaza: 0xbcd9d5,
    wall: 0x5d7d79,
    roomLabel: '#1f2f34',
    plazaLabel: '#1f2f34',
    playerRing: 0xffc24d,
};

/** 六个房间各自一个淡色地板，方便一眼分辨。 */
const ROOM_FLOOR_COLORS: Record<string, number> = {
    cafe: 0xf2ddc4,
    library: 0xd8dff2,
    studio: 0xe6d6ef,
    kitchen: 0xf3dcd9,
    gameroom: 0xd6ecd8,
    meeting: 0xe9e6cf,
};

type ActorView = {
    sprite: Phaser.GameObjects.Sprite;
    label: Phaser.GameObjects.Text;
    bubble: Phaser.GameObjects.Text;
    ring?: Phaser.GameObjects.Graphics;
    targetX: number;
    targetY: number;
    facing: Facing;
    moving: boolean;
};

/** 快照是 10Hz 的，画面按帧向目标位置追，走动才不会一格一格跳。 */
const SMOOTHING_MS = 90;
/** 超过这个像素差直接吸附（出生、传送、重连）。 */
const SNAP_DISTANCE_PX = 96;

export class TownScene extends Phaser.Scene {
    private readonly worldMap: WorldMap;
    private readonly spriteKeys: string[];
    private readonly getSnapshot: () => ActorSnapshot[];
    private views = new Map<string, ActorView>();
    private followId: string | undefined;
    private lastSnapshot: ActorSnapshot[] | undefined;
    private ready = false;

    constructor(worldMap: WorldMap, spriteKeys: string[], getSnapshot: () => ActorSnapshot[]) {
        super('town');
        this.worldMap = worldMap;
        this.spriteKeys = spriteKeys;
        this.getSnapshot = getSnapshot;
    }

    preload() {
        this.spriteKeys.forEach((key) => {
            this.load.spritesheet(key, `${CHARACTER_ROOT}/${key}.png`, {
                frameWidth: FRAME_SIZE,
                frameHeight: FRAME_SIZE,
            });
        });
    }

    create() {
        const tile = this.worldMap.tile_size;
        const width = this.worldMap.grid_w * tile;
        const height = this.worldMap.grid_h * tile;

        this.cameras.main.setBackgroundColor(COLORS.void);
        this.cameras.main.setBounds(0, 0, width, height);
        this.drawMap();
        this.fitCamera();
        this.ready = true;

        // RESIZE 模式下画布跟着容器变，没有跟随目标时重新把整张地图缩放到刚好铺满。
        this.scale.on('resize', () => {
            if (!this.followId) {
                this.fitCamera();
            }
        });
    }

    private fillRect(graphics: Phaser.GameObjects.Graphics, rect: Rect, color: number) {
        const tile = this.worldMap.tile_size;
        graphics.fillStyle(color, 1);
        graphics.fillRect(rect.x * tile, rect.y * tile, rect.w * tile, rect.h * tile);
    }

    private drawMap() {
        const tile = this.worldMap.tile_size;
        const floor = this.add.graphics().setDepth(0);

        this.worldMap.corridors.forEach((corridor) => this.fillRect(floor, corridor, COLORS.corridor));
        this.fillRect(floor, this.worldMap.plaza, COLORS.plaza);
        this.worldMap.rooms.forEach((room) => {
            this.fillRect(floor, room.interior, ROOM_FLOOR_COLORS[room.id] ?? COLORS.corridor);
            // 门是墙上的开口，补一格地板让视觉连通。
            this.fillRect(floor, { x: room.door.x, y: room.door.y, w: 1, h: 1 }, COLORS.corridor);
        });

        const walls = this.add.graphics().setDepth(1);
        walls.fillStyle(COLORS.wall, 1);
        this.worldMap.walls.forEach((wall) => {
            walls.fillRect(wall.x * tile, wall.y * tile, tile, tile);
        });

        const labelStyle = {
            fontFamily: FONT_FAMILY,
            fontSize: '20px',
            color: COLORS.roomLabel,
            fontStyle: 'bold',
        };
        this.worldMap.rooms.forEach((room) => {
            this.add
                .text((room.anchor.x + 0.5) * tile, (room.rect.y + 1.2) * tile, room.name, labelStyle)
                .setOrigin(0.5)
                .setDepth(2);
        });
        this.add
            .text(
                (this.worldMap.plaza_room.anchor.x + 0.5) * tile,
                (this.worldMap.plaza.y + 0.9) * tile,
                this.worldMap.plaza_room.name,
                { ...labelStyle, color: COLORS.plazaLabel },
            )
            .setOrigin(0.5)
            .setDepth(2);
    }

    /** 无人跟随时把整张地图缩放到刚好铺满画布。 */
    fitCamera() {
        const tile = this.worldMap.tile_size;
        const width = this.worldMap.grid_w * tile;
        const height = this.worldMap.grid_h * tile;
        const camera = this.cameras.main;
        const zoom = Math.min(camera.width / width, camera.height / height);
        camera.setZoom(zoom > 0 ? zoom : 1);
        camera.centerOn(width / 2, height / 2);
    }

    setFollow(actorId: string | undefined) {
        this.followId = actorId;
        if (!this.ready) {
            return;
        }
        const camera = this.cameras.main;
        if (!actorId) {
            camera.stopFollow();
            this.fitCamera();
            return;
        }
        const view = this.views.get(actorId);
        if (view) {
            camera.setZoom(1.4);
            camera.startFollow(view.sprite, true, 0.15, 0.15);
        }
    }

    private ensureAnimations(spriteKey: string) {
        (Object.keys(WALK_FRAMES) as Facing[]).forEach((facing) => {
            const key = `${spriteKey}-${facing}`;
            if (this.anims.exists(key)) {
                return;
            }
            this.anims.create({
                key,
                frames: WALK_FRAMES[facing].map((frame) => ({ key: spriteKey, frame })),
                frameRate: 7,
                repeat: -1,
            });
        });
    }

    private createView(actor: ActorSnapshot): ActorView {
        const spriteKey = this.textures.exists(actor.sprite) ? actor.sprite : this.spriteKeys[0];
        this.ensureAnimations(spriteKey);

        const sprite = this.add.sprite(0, 0, spriteKey, IDLE_FRAMES[actor.facing]);
        sprite.setScale(SPRITE_SCALE).setDepth(10);

        const label = this.add
            .text(0, 0, actor.name, {
                fontFamily: FONT_FAMILY,
                fontSize: '13px',
                color: '#ffffff',
                backgroundColor: 'rgba(24, 38, 44, 0.72)',
                padding: { x: 4, y: 1 },
            })
            .setOrigin(0.5, 1)
            .setDepth(11);

        const bubble = this.add
            .text(0, 0, '', {
                fontFamily: FONT_FAMILY,
                fontSize: '14px',
                color: '#1f2f34',
                backgroundColor: 'rgba(255, 253, 248, 0.95)',
                padding: { x: 7, y: 4 },
                wordWrap: { width: 190 },
                align: 'center',
            })
            .setOrigin(0.5, 1)
            .setDepth(12)
            .setVisible(false);

        const ring =
            actor.kind === 'human'
                ? this.add.graphics().setDepth(9)
                : undefined;
        if (ring) {
            ring.lineStyle(2, COLORS.playerRing, 1);
            ring.strokeCircle(0, 0, 13);
        }

        return {
            sprite,
            label,
            bubble,
            ring,
            targetX: sprite.x,
            targetY: sprite.y,
            facing: actor.facing,
            moving: false,
        };
    }

    update(_time: number, delta: number) {
        if (!this.ready) {
            return;
        }
        const snapshot = this.getSnapshot();
        if (snapshot !== this.lastSnapshot) {
            this.lastSnapshot = snapshot;
            this.applySnapshot(snapshot);
        }
        const tile = this.worldMap.tile_size;
        const factor = Math.min(1, delta / SMOOTHING_MS);
        this.views.forEach((view) => {
            const { sprite } = view;
            sprite.x += (view.targetX - sprite.x) * factor;
            sprite.y += (view.targetY - sprite.y) * factor;
            sprite.setDepth(10 + sprite.y / 10000);
            view.label.setPosition(sprite.x, sprite.y - tile * 0.6);
            view.ring?.setPosition(sprite.x, sprite.y + 8);
            if (view.bubble.visible) {
                view.bubble.setPosition(sprite.x, sprite.y - tile * 0.6 - view.label.height - 4);
            }
        });
    }

    private applySnapshot(actors: ActorSnapshot[]) {
        if (!this.ready) {
            return;
        }
        const tile = this.worldMap.tile_size;
        const seen = new Set<string>();

        actors.forEach((actor) => {
            seen.add(actor.id);
            let view = this.views.get(actor.id);
            if (!view) {
                view = this.createView(actor);
                this.views.set(actor.id, view);
                if (this.followId === actor.id) {
                    this.setFollow(actor.id);
                }
            }

            const px = (actor.x + 0.5) * tile;
            const py = (actor.y + 0.5) * tile;
            const fresh = view.targetX === view.sprite.x && view.targetY === view.sprite.y && !view.moving;
            view.targetX = px;
            view.targetY = py;
            if (fresh || Phaser.Math.Distance.Between(view.sprite.x, view.sprite.y, px, py) > SNAP_DISTANCE_PX) {
                view.sprite.setPosition(px, py);
            }
            view.label.setText(actor.name);
            view.moving = actor.moving;
            view.facing = actor.facing;

            if (actor.moving) {
                view.sprite.anims.play(`${view.sprite.texture.key}-${actor.facing}`, true);
            } else {
                view.sprite.anims.stop();
                view.sprite.setFrame(IDLE_FRAMES[actor.facing]);
            }

            if (actor.say) {
                view.bubble.setText(actor.say);
                view.bubble.setVisible(true);
            } else {
                view.bubble.setVisible(false);
            }
        });

        this.views.forEach((view, actorId) => {
            if (seen.has(actorId)) {
                return;
            }
            view.sprite.destroy();
            view.label.destroy();
            view.bubble.destroy();
            view.ring?.destroy();
            this.views.delete(actorId);
        });
    }
}

export const createTownGame = (
    parent: HTMLElement,
    worldMap: WorldMap,
    spriteKeys: string[],
    getSnapshot: () => ActorSnapshot[],
): { game: Phaser.Game; scene: TownScene } => {
    const scene = new TownScene(worldMap, spriteKeys, getSnapshot);
    const game = new Phaser.Game({
        type: Phaser.AUTO,
        parent,
        backgroundColor: '#16232a',
        pixelArt: true,
        scale: {
            mode: Phaser.Scale.RESIZE,
            width: parent.clientWidth || 800,
            height: parent.clientHeight || 600,
        },
        scene,
    });
    return { game, scene };
};
