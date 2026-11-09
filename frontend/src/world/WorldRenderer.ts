// Imperative Pixi renderer for a whole world map (WORLD_SPEC §11, W6).
//
// The 2-player board renders every hex through @pixi/react, which is fine for a
// 40×40 map but not for ~122k hexes. This renderer instead:
//
// * **close up** (tactical zoom) keeps a pool of tile sprites and shows only the
//   hexes on screen, re-using sprites as the camera moves, with fog over the hexes
//   the player can't currently see;
// * **far out** (operational zoom) shows one prerendered overview texture of the
//   whole map (terrain and roads, a few pixels per hex) instead of tiles;
// * always draws site markers (owner colour) and unit tokens on top, and a minimap
//   in the corner that doubles as a jump-to control.
//
// React owns only the canvas element and feeds this class view updates; camera
// moves never go through React.

import { Application, Container, Graphics, Rectangle, Sprite, Texture, type FederatedPointerEvent } from "pixi.js";

import { axialToPixel, hexCornerPoints, hexKey, HEX_SIZE, pixelToAxial, type Axial } from "@/board/hex";
import { hexTileRef } from "@/board/hexTile";
import { TERRAIN_FILL } from "@/board/palette";
import { riverCourseEdges } from "@/board/riverTiles";
import { roadTileRefs, type RoadTileRef } from "@/board/roadTiles";
import type { LoadedTiles } from "@/board/tiles";
import type { LoadedUnits } from "@/board/units";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER, ownerColor } from "./colors";
import { visibleCells, worldPixelBounds, type WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];

/** Below this zoom the tile layer gives way to the overview texture. */
export const TILE_MIN_SCALE = 0.4;
/** Below this zoom neutral sites are hidden on the overview. */
const NEUTRAL_SITE_MIN_SCALE = 0.12;
const MIN_SCALE = 0.02;
const MAX_SCALE = 2.5;
const OVERVIEW_HEX = 2; // overview texture: hex radius in texture pixels
const MINIMAP_SIZE = 220;
const MINIMAP_MARGIN = 12;

export interface WorldScene {
  readonly state: WorldState;
  readonly viewer: string;
  /** Hexes the viewer can see this turn (keys from `hexKey`). */
  readonly visible: ReadonlySet<string>;
}

/** Planning marks drawn over the map (WorldPlanning, projected for drawing). */
export interface WorldOverlay {
  /** The selected unit's hex. */
  selectedUnit: Axial | null;
  /** The hex being inspected. */
  selectedHex: Axial | null;
  routes: readonly {
    from: Axial;
    path: readonly Axial[];
    kind: "travel" | "standing" | "move" | "march";
    selected: boolean;
  }[];
  barrages: readonly { from: Axial; target: Axial }[];
  /** Hexes a click would move the selected unit to. */
  reach: readonly Axial[];
  recruits: readonly Axial[];
}

export interface WorldRendererOptions {
  onHexClick?: (hex: Axial) => void;
}

export interface Camera {
  x: number;
  y: number;
  scale: number;
}

export class WorldRenderer {
  readonly app: Application;
  private readonly map: WorldMap;
  private readonly world = new Container();
  private readonly tileLayer = new Container();
  private readonly fogLayer = new Container();
  private readonly overview: Sprite;
  private readonly sites = new Graphics();
  private readonly unitLayer = new Container();
  private readonly overlay = new Graphics();
  private readonly selection = new Graphics();
  private overlayData: WorldOverlay | null = null;
  private readonly minimap = new Container();
  private readonly minimapFrame = new Graphics();
  private readonly minimapSites = new Graphics();
  private readonly tileSprites = new Map<number, Sprite>();
  private readonly fogSprites = new Map<number, Sprite>();
  private readonly spritePool: Sprite[] = [];
  private readonly fogPool: Sprite[] = [];
  private readonly fogTexture: Texture;
  private readonly roadTiles: Map<string, RoadTileRef>;
  private readonly riverCourse: Map<string, number[]>;
  private readonly overviewScale: number;
  private tiles: LoadedTiles | null = null;
  private units: LoadedUnits | null = null;
  private scene: WorldScene | null = null;
  private readonly options: WorldRendererOptions;
  private drag: { x: number; y: number; moved: boolean } | null = null;
  /** Minimap pixels per world pixel (set when the minimap is built). */
  private minimapScale = 1;

  constructor(app: Application, map: WorldMap, options: WorldRendererOptions = {}) {
    this.app = app;
    this.map = map;
    this.options = options;
    this.roadTiles = roadTileRefs(map.hexes, (q, r) => map.hexAt(q, r));
    this.riverCourse = riverCourseEdges(
      map.hexes.filter((h) => h.terrain === "river" || h.terrain === "bridge").map((h) => h.coord),
      (q, r) => map.hexAt(q, r)?.terrain,
    );

    const overviewCanvas = drawOverview(map);
    const overviewTexture = Texture.from(overviewCanvas);
    this.overview = new Sprite(overviewTexture);
    this.overviewScale = HEX_SIZE / OVERVIEW_HEX;
    this.overview.scale.set(this.overviewScale);
    this.overview.position.set(-HEX_SIZE, -HEX_SIZE);
    this.fogTexture = hexTexture(app);

    this.world.addChild(this.overview, this.tileLayer, this.fogLayer, this.sites, this.overlay, this.unitLayer, this.selection);
    app.stage.addChild(this.world, this.minimap);
    this.buildMinimap(overviewTexture);

    app.stage.eventMode = "static";
    app.stage.hitArea = app.screen;
    app.stage.on("pointerdown", this.onPointerDown);
    app.stage.on("pointermove", this.onPointerMove);
    app.stage.on("pointerup", this.onPointerUp);
    app.stage.on("pointerupoutside", this.onPointerUp);
    app.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    app.renderer.on("resize", this.refresh);

    this.fitAll();
  }

  destroy(): void {
    this.app.canvas.removeEventListener("wheel", this.onWheel);
    this.app.renderer.off("resize", this.refresh);
    this.app.destroy(true, { children: true });
  }

  setTextures(tiles: LoadedTiles | null, units: LoadedUnits | null): void {
    this.tiles = tiles;
    this.units = units;
    for (const sprite of this.tileSprites.values()) this.releaseTile(sprite);
    this.tileSprites.clear();
    this.refresh();
  }

  setScene(scene: WorldScene): void {
    this.scene = scene;
    this.drawMinimapSites();
    this.refresh();
  }

  setOverlay(overlay: WorldOverlay): void {
    this.overlayData = overlay;
    this.drawOverlay();
  }

  get camera(): Camera {
    return { x: this.world.x, y: this.world.y, scale: this.world.scale.x };
  }

  /** Centre the view on `hex`, keeping the zoom unless `scale` is given. */
  panTo(hex: Axial): void {
    this.centerOn(hex, Math.max(this.world.scale.x, TILE_MIN_SCALE));
  }

  /** Centre the view on `hex` at `scale` (default: a comfortable tactical zoom). */
  centerOn(hex: Axial, scale = 0.9): void {
    const { x, y } = axialToPixel(hex.q, hex.r, HEX_SIZE);
    const s = clamp(scale, MIN_SCALE, MAX_SCALE);
    this.world.scale.set(s);
    this.world.position.set(this.app.screen.width / 2 - x * s, this.app.screen.height / 2 - y * s);
    this.refresh();
  }

  /** The zoom that shows the whole world. */
  private fitScale(): number {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    return Math.min(this.app.screen.width / (b.maxX - b.minX), this.app.screen.height / (b.maxY - b.minY));
  }

  /** Zoom out to show the whole world. */
  fitAll(): void {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = clamp(
      Math.min(this.app.screen.width / (b.maxX - b.minX), this.app.screen.height / (b.maxY - b.minY)),
      MIN_SCALE,
      MAX_SCALE,
    );
    this.world.scale.set(s);
    this.world.position.set(
      (this.app.screen.width - (b.maxX - b.minX) * s) / 2 - b.minX * s,
      (this.app.screen.height - (b.maxY - b.minY) * s) / 2 - b.minY * s,
    );
    this.refresh();
  }

  // --- camera input ---------------------------------------------------------------

  private onPointerDown = (e: FederatedPointerEvent): void => {
    if (this.minimapHit(e.global.x, e.global.y)) {
      this.jumpFromMinimap(e.global.x, e.global.y);
      return;
    }
    this.drag = { x: e.global.x, y: e.global.y, moved: false };
  };

  private onPointerMove = (e: FederatedPointerEvent): void => {
    if (this.drag === null) return;
    const dx = e.global.x - this.drag.x;
    const dy = e.global.y - this.drag.y;
    if (!this.drag.moved && Math.hypot(dx, dy) < 4) return;
    this.drag = { x: e.global.x, y: e.global.y, moved: true };
    this.world.position.set(this.world.x + dx, this.world.y + dy);
    this.refresh();
  };

  private onPointerUp = (e: FederatedPointerEvent): void => {
    const drag = this.drag;
    this.drag = null;
    if (drag === null || drag.moved) return;
    const local = this.world.toLocal(e.global);
    const hex = pixelToAxial(local.x, local.y, HEX_SIZE);
    if (this.map.hexAt(hex.q, hex.r) !== undefined) this.options.onHexClick?.(hex);
  };

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    const rect = this.app.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    const old = this.world.scale.x;
    const next = clamp(old * factor, Math.max(MIN_SCALE, this.fitScale() * 0.85), MAX_SCALE);
    const wx = (px - this.world.x) / old;
    const wy = (py - this.world.y) / old;
    this.world.scale.set(next);
    this.world.position.set(px - wx * next, py - wy * next);
    this.refresh();
  };

  // --- drawing ----------------------------------------------------------------------

  private refresh = (): void => {
    const s = this.world.scale.x;
    const tileMode = s >= TILE_MIN_SCALE && this.tiles !== null;
    const x0 = -this.world.x / s;
    const y0 = -this.world.y / s;
    const x1 = x0 + this.app.screen.width / s;
    const y1 = y0 + this.app.screen.height / s;
    const range = visibleCells(this.map, x0, y0, x1, y1, HEX_SIZE);

    this.overview.visible = !tileMode;
    this.tileLayer.visible = tileMode;
    this.fogLayer.visible = tileMode;
    if (tileMode) this.syncTiles(range);
    this.drawSites(range, tileMode);
    this.drawUnits(range, tileMode);
    this.drawOverlay();
    this.drawMinimapFrame(x0, y0, x1, y1);
  };

  private syncTiles(range: { col0: number; col1: number; row0: number; row1: number }): void {
    const want = new Set<number>();
    const { width } = this.map;
    const visible = this.scene?.visible;
    for (let row = range.row0; row <= range.row1; row++) {
      for (let col = range.col0; col <= range.col1; col++) {
        const index = row * width + col;
        want.add(index);
        const hex = this.map.hexes[index];
        if (!this.tileSprites.has(index)) {
          this.tileSprites.set(index, this.placeTile(hex));
        }
        const fogged = visible !== undefined && !visible.has(hexKey(hex.coord.q, hex.coord.r));
        const fog = this.fogSprites.get(index);
        if (fogged && fog === undefined) {
          this.fogSprites.set(index, this.placeFog(hex));
        } else if (!fogged && fog !== undefined) {
          this.releaseFog(fog);
          this.fogSprites.delete(index);
        }
      }
    }
    for (const [index, sprite] of this.tileSprites) {
      if (!want.has(index)) {
        this.releaseTile(sprite);
        this.tileSprites.delete(index);
      }
    }
    for (const [index, sprite] of this.fogSprites) {
      if (!want.has(index)) {
        this.releaseFog(sprite);
        this.fogSprites.delete(index);
      }
    }
  }

  private placeTile(hex: WorldMap["hexes"][number]): Sprite {
    const tiles = this.tiles;
    if (tiles === null) throw new Error("placeTile before textures loaded");
    const ref = hexTileRef(hex, tiles, this.riverCourse, this.roadTiles);
    const sprite = this.spritePool.pop() ?? new Sprite();
    sprite.texture = ref.tex;
    sprite.anchor.set(0.5);
    const scale = (2 * HEX_SIZE) / ref.tex.width;
    sprite.scale.set(ref.mirror ? -scale : scale, scale);
    const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
    sprite.position.set(x, y);
    sprite.visible = true;
    this.tileLayer.addChild(sprite);
    return sprite;
  }

  private releaseTile(sprite: Sprite): void {
    sprite.removeFromParent();
    this.spritePool.push(sprite);
  }

  private placeFog(hex: WorldMap["hexes"][number]): Sprite {
    const sprite = this.fogPool.pop() ?? new Sprite(this.fogTexture);
    sprite.anchor.set(0.5);
    sprite.tint = 0x0b0d12;
    sprite.alpha = 0.38;
    const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
    sprite.position.set(x, y);
    this.fogLayer.addChild(sprite);
    return sprite;
  }

  private releaseFog(sprite: Sprite): void {
    sprite.removeFromParent();
    this.fogPool.push(sprite);
  }

  private drawSites(
    range: { col0: number; col1: number; row0: number; row1: number },
    tileMode: boolean,
  ): void {
    const g = this.sites;
    g.clear();
    const scene = this.scene;
    if (scene === null) return;
    const control = new Map<string, string | null>();
    for (const s of scene.state.settlements) control.set(hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null);
    const scale = this.world.scale.x;
    const radius = HEX_SIZE * 0.92;
    for (const site of this.map.sites) {
      const { col, row } = { col: site.coord.q, row: site.coord.r + (site.coord.q - (site.coord.q & 1)) / 2 };
      if (col < range.col0 || col > range.col1 || row < range.row0 || row > range.row1) continue;
      const key = hexKey(site.coord.q, site.coord.r);
      if (!control.has(key)) continue; // a vacant base site: nothing there yet
      const owner = control.get(key) ?? null;
      const { x, y } = axialToPixel(site.coord.q, site.coord.r, HEX_SIZE);
      const color = ownerColor(owner, scene.viewer);
      if (tileMode) {
        g.poly(hexCornerPoints(x, y, radius)).stroke({ color, width: owner === null ? 2 : 4, alpha: 0.95 });
      } else if (owner === null) {
        // Neutral sites are context, not news: small, and gone when far out.
        if (scale >= NEUTRAL_SITE_MIN_SCALE) g.circle(x, y, 2.2 / scale).fill({ color, alpha: 0.7 });
      } else {
        const r = (site.kind === "base" ? 5 : 3.5) / scale;
        g.circle(x, y, r).fill({ color }).stroke({ color: 0x101114, width: 1 / scale });
      }
    }
  }

  private drawUnits(
    range: { col0: number; col1: number; row0: number; row1: number },
    tileMode: boolean,
  ): void {
    this.unitLayer.removeChildren().forEach((c) => c.destroy());
    const scene = this.scene;
    if (scene === null) return;
    const tokens = new Graphics();
    this.unitLayer.addChild(tokens);
    const byHex = new Map<string, Unit[]>();
    for (const u of scene.state.units) {
      const col = u.coord.q;
      const row = u.coord.r + (col - (col & 1)) / 2;
      if (col < range.col0 || col > range.col1 || row < range.row0 || row > range.row1) continue;
      const key = hexKey(u.coord.q, u.coord.r);
      const list = byHex.get(key);
      if (list === undefined) byHex.set(key, [u]);
      else list.push(u);
    }
    const units = this.units;
    for (const list of byHex.values()) {
      const lead = list.find((u) => u.posture !== "column") ?? list[0];
      const { x, y } = axialToPixel(lead.coord.q, lead.coord.r, HEX_SIZE);
      const color = ownerColor(lead.owner, scene.viewer);
      const columns = list.filter((u) => u.posture === "column").length;
      if (tileMode && units !== null && lead.posture !== "column" && lead.owner !== NEUTRAL_OWNER) {
        const pair = units[lead.type];
        const s = (1.5 * HEX_SIZE) / pair.base.width;
        const base = new Sprite(pair.base);
        const mask = new Sprite(pair.mask);
        for (const sp of [base, mask]) {
          sp.anchor.set(0.5);
          sp.scale.set(lead.facing === "left" ? -s : s, s);
          sp.position.set(x, y);
        }
        mask.tint = color;
        this.unitLayer.addChild(base, mask);
      } else {
        const r = tileMode ? HEX_SIZE * 0.42 : Math.max(HEX_SIZE * 0.6, 4 / this.world.scale.x);
        if (lead.posture === "column") {
          tokens.roundRect(x - r * 1.2, y - r * 0.7, r * 2.4, r * 1.4, r * 0.3).fill(color).stroke({ color: 0x101114, width: r * 0.15 });
        } else {
          tokens.circle(x, y, r).fill(color).stroke({ color: 0x101114, width: r * 0.15 });
        }
      }
      if (tileMode && columns > 1) {
        // Stack count for packed columns sharing the hex.
        tokens.circle(x + HEX_SIZE * 0.55, y - HEX_SIZE * 0.45, HEX_SIZE * 0.22).fill(0x101114);
        for (let k = 0; k < Math.min(columns, 6); k++) {
          tokens.circle(x + HEX_SIZE * 0.55 + (k - (Math.min(columns, 6) - 1) / 2) * 3, y - HEX_SIZE * 0.45, 1.2).fill(0xffffff);
        }
      }
      if (tileMode && lead.hp < 10) {
        const w = HEX_SIZE * 1.1;
        tokens.rect(x - w / 2, y + HEX_SIZE * 0.62, w, 4).fill(0x101114);
        tokens.rect(x - w / 2, y + HEX_SIZE * 0.62, (w * lead.hp) / 10, 4).fill(lead.hp > 5 ? 0x7bd389 : 0xe0a040);
      }
    }
  }

  private drawOverlay(): void {
    const g = this.overlay.clear();
    const top = this.selection.clear();
    const o = this.overlayData;
    if (o === null) return;
    const scale = this.world.scale.x;
    // Line widths in screen pixels, so marks stay readable at every zoom.
    const px = (n: number) => n / scale;
    const centre = (a: Axial) => axialToPixel(a.q, a.r, HEX_SIZE);

    for (const hex of o.reach) {
      const { x, y } = centre(hex);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.9)).fill({ color: 0xf2e6c8, alpha: 0.22 });
    }
    for (const route of o.routes) {
      if (route.path.length === 0) continue;
      const color = route.kind === "move" ? 0xf2e6c8 : route.kind === "standing" ? 0x9fb7d9 : 0x3d7eff;
      const width = px(route.selected ? 4 : 2.5);
      const alpha = route.selected ? 0.95 : route.kind === "standing" ? 0.55 : 0.75;
      const start = centre(route.from);
      g.moveTo(start.x, start.y);
      for (const step of route.path) {
        const p = centre(step);
        g.lineTo(p.x, p.y);
      }
      g.stroke({ color: 0x101114, width: width + px(2), alpha: alpha * 0.6, cap: "round", join: "round" });
      g.moveTo(start.x, start.y);
      for (const step of route.path) {
        const p = centre(step);
        g.lineTo(p.x, p.y);
      }
      g.stroke({ color, width, alpha, cap: "round", join: "round" });
      const end = centre(route.path[route.path.length - 1]);
      g.circle(end.x, end.y, Math.max(px(5), HEX_SIZE * 0.22)).fill({ color, alpha }).stroke({ color: 0x101114, width: px(1.5) });
    }
    for (const b of o.barrages) {
      const from = centre(b.from);
      const to = centre(b.target);
      g.moveTo(from.x, from.y).lineTo(to.x, to.y).stroke({ color: 0xe0503a, width: px(2.5), alpha: 0.9 });
      g.poly(hexCornerPoints(to.x, to.y, HEX_SIZE * 0.85)).stroke({ color: 0xe0503a, width: px(3) });
    }
    for (const hex of o.recruits) {
      const { x, y } = centre(hex);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.7)).stroke({ color: 0x7bd389, width: px(3) });
      g.moveTo(x - HEX_SIZE * 0.3, y).lineTo(x + HEX_SIZE * 0.3, y).moveTo(x, y - HEX_SIZE * 0.3).lineTo(x, y + HEX_SIZE * 0.3);
      g.stroke({ color: 0x7bd389, width: px(3) });
    }
    if (o.selectedHex !== null) {
      const { x, y } = centre(o.selectedHex);
      top.poly(hexCornerPoints(x, y, HEX_SIZE)).stroke({ color: 0xf2e6c8, width: px(2), alpha: 0.9 });
    }
    if (o.selectedUnit !== null) {
      const { x, y } = centre(o.selectedUnit);
      top.circle(x, y, Math.max(HEX_SIZE * 0.85, px(10))).stroke({ color: 0xffd25a, width: px(3) });
    }
  }

  // --- minimap ----------------------------------------------------------------------

  private buildMinimap(texture: Texture): void {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = MINIMAP_SIZE / Math.max(b.maxX - b.minX, b.maxY - b.minY);
    const backdrop = new Graphics()
      .rect(-4, -4, (b.maxX - b.minX) * s + 8, (b.maxY - b.minY) * s + 8)
      .fill({ color: 0x15171b, alpha: 0.85 });
    const img = new Sprite(texture);
    img.scale.set((this.overviewScale * s));
    this.minimap.addChild(backdrop, img, this.minimapSites, this.minimapFrame);
    this.minimap.label = "minimap";
    this.minimapScale = s;
    this.positionMinimap();
    this.app.renderer.on("resize", () => this.positionMinimap());
  }

  private positionMinimap(): void {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const h = (b.maxY - b.minY) * this.minimapScale;
    this.minimap.position.set(MINIMAP_MARGIN, this.app.screen.height - h - MINIMAP_MARGIN);
  }

  private minimapHit(x: number, y: number): boolean {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = this.minimapScale;
    const local = { x: x - this.minimap.x, y: y - this.minimap.y };
    return local.x >= 0 && local.y >= 0 && local.x <= (b.maxX - b.minX) * s && local.y <= (b.maxY - b.minY) * s;
  }

  private jumpFromMinimap(x: number, y: number): void {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = this.minimapScale;
    const wx = (x - this.minimap.x) / s + b.minX;
    const wy = (y - this.minimap.y) / s + b.minY;
    const scale = this.world.scale.x;
    this.world.position.set(this.app.screen.width / 2 - wx * scale, this.app.screen.height / 2 - wy * scale);
    this.refresh();
  }

  private drawMinimapFrame(x0: number, y0: number, x1: number, y1: number): void {
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = this.minimapScale;
    const w = (b.maxX - b.minX) * s;
    const h = (b.maxY - b.minY) * s;
    const left = clamp((x0 - b.minX) * s, 0, w);
    const top = clamp((y0 - b.minY) * s, 0, h);
    const right = clamp((x1 - b.minX) * s, 0, w);
    const bottom = clamp((y1 - b.minY) * s, 0, h);
    this.minimapFrame.clear().rect(left, top, right - left, bottom - top).stroke({ color: 0xf2e6c8, width: 1.5 });
  }

  private drawMinimapSites(): void {
    const g = this.minimapSites.clear();
    const scene = this.scene;
    if (scene === null) return;
    const b = worldPixelBounds(this.map, HEX_SIZE);
    const s = this.minimapScale;
    for (const settlement of scene.state.settlements) {
      const owner = settlement.controlled_by ?? null;
      if (owner === null) continue;
      const { x, y } = axialToPixel(settlement.coord.q, settlement.coord.r, HEX_SIZE);
      g.circle((x - b.minX) * s, (y - b.minY) * s, owner === scene.viewer ? 2.2 : 1.4).fill(ownerColor(owner, scene.viewer));
    }
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** A white flat-top hex the size of a board hex, for tinted overlays (fog). */
function hexTexture(app: Application): Texture {
  const g = new Graphics().poly(hexCornerPoints(HEX_SIZE, HEX_SIZE, HEX_SIZE)).fill(0xffffff);
  const texture = app.renderer.generateTexture({ target: g, frame: new Rectangle(0, 0, 2 * HEX_SIZE, 2 * HEX_SIZE) });
  g.destroy();
  return texture;
}

/**
 * The far-zoom map: every hex a few pixels across, plus roads, on a 2D canvas.
 *
 * Written pixel by pixel (each pixel → the hex containing it) rather than with
 * canvas paths: one path of ~122k hexes takes Chrome tens of seconds to fill,
 * while this is a single pass over ~1.3M pixels.
 */
export function drawOverview(map: WorldMap): HTMLCanvasElement {
  const b = worldPixelBounds(map, OVERVIEW_HEX);
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(b.maxX - b.minX);
  canvas.height = Math.ceil(b.maxY - b.minY);
  const ctx = canvas.getContext("2d");
  if (ctx === null) throw new Error("no 2D canvas context for the world overview");
  const image = ctx.createImageData(canvas.width, canvas.height);
  const px = image.data;
  const ROAD = 0x5c4028;
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const wx = x + b.minX;
      const wy = y + b.minY;
      const hex = pixelToAxial(wx, wy, OVERVIEW_HEX);
      const cell = map.hexAt(hex.q, hex.r);
      if (cell === undefined) continue;
      let color = TERRAIN_FILL[cell.terrain];
      if (cell.road && cell.terrain !== "river") {
        const c = axialToPixel(hex.q, hex.r, OVERVIEW_HEX);
        if (Math.abs(c.x - wx) < 0.9 && Math.abs(c.y - wy) < 0.9) color = ROAD;
      }
      const i = (y * canvas.width + x) * 4;
      px[i] = (color >> 16) & 0xff;
      px[i + 1] = (color >> 8) & 0xff;
      px[i + 2] = color & 0xff;
      px[i + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}
