// Imperative Pixi renderer for a whole world map (WORLD_SPEC §11, W6).
//
// Renders every hex imperatively rather than through @pixi/react, which would be
// fine for a 40×40 map but not for ~122k hexes. This renderer instead:
//
// * **close up** (tactical zoom) keeps a pool of tile sprites and shows only the
//   hexes on screen, re-using sprites as the camera moves (a site or founded base
//   hex shows its painted tile instead of its terrain, `worldTiles.ts`), with
//   same-terrain join sprites over the woods/city seams among them and river
//   mouth sprites where a river runs into a lake (same culling, own pool), and
//   fog over the hexes the player can't currently see;
// * **far out** (operational zoom) shows one prerendered overview texture of the
//   whole map (terrain and roads, a few pixels per hex) instead of tiles;
// * always draws site markers (owner colour) and unit tokens on top, and a minimap
//   in the corner that doubles as a jump-to control.
//
// React owns only the canvas element and feeds this class view updates; camera
// moves never go through React. Camera changes only mark the scene dirty: the
// ticker redraws at most once per frame, and also drives keyboard panning and
// eased jumps. The camera is clamped (`camera.ts`) so the map never leaves the
// screen. Hover (outline, travel preview) is drawn here too, so moving the
// pointer never renders React; listeners hear only when the hovered hex changes.
//
// Turn playback (`setPlayback`) swaps the live units for a supplied list drawn
// at fractional positions with alpha, adds an effects layer (fights, shells,
// floaters, capture rings), takes fog and site owners per beat, and suspends
// planning marks, hover and clicks until it is cleared. Frames (`setPlaybackFrame`)
// redraw only the units and effects, never the tiles.

import {
  Application,
  Container,
  Graphics,
  Rectangle,
  Sprite,
  Text,
  Texture,
  type FederatedPointerEvent,
  type Ticker,
} from "pixi.js";

import { axialEqual, axialToPixel, hexCornerPoints, hexKey, HEX_SIZE, pixelToAxial, type Axial } from "@/board/hex";
import { hexTileRef } from "@/board/hexTile";
import {
  JOIN_CODE_COUNT,
  JOIN_TERRAINS,
  MOUTH_ART,
  joinDrawRank,
  joinsOwnedBy,
  joinSpriteScale,
  joinVariant,
  mouthPlacement,
  type JoinPlacement,
  type LoadedJoins,
} from "@/board/joins";
import { lakeMasks } from "@/board/lakeTiles";
import { ROAD_COLOR, TERRAIN_FILL } from "@/board/palette";
import { riverCourseEdges, riverMouths, type RiverOutlet } from "@/board/riverTiles";
import { roadCourseEdges, roadTileRefs, type RoadTileRef } from "@/board/roadTiles";
import type { LoadedTiles } from "@/board/tiles";
import type { LoadedUnits, UnitSpritePair } from "@/board/units";
import type { components } from "@/lib/api/schema";

import {
  cameraBetween,
  centredOn,
  clampCamera,
  comfortablyVisible,
  fitCamera,
  followCamera,
  fitScale,
  JUMP_MS,
  panStep,
  zoomAbout,
  type Camera,
  type Insets,
  type PanInput,
} from "./camera";
import { NEUTRAL_OWNER, ownerColor } from "./colors";
import { dashSegments } from "./dashes";
import type { LoadedColumns } from "./columnArt";
import type { DrawnUnit, Effect } from "./playback/interpolate";
import type { LoadedSites } from "./siteArt";
import { siteTileTexture } from "./siteTiles";
import { axialToOffset, visibleCells, worldPixelBounds, type CellRange, type WorldMap } from "./worldMap";
import {
  baseSiteTiles,
  changedBaseTiles,
  hexTileChoice,
  overviewHexFill,
  staticSiteTiles,
  type SiteTileMap,
} from "./worldTiles";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];

/** Below this zoom the tile layer gives way to the overview texture. */
export const TILE_MIN_SCALE = 0.4;
/** The comfortable close-up zoom a "centre on" jump lands at. */
export const TACTICAL_SCALE = 0.9;
/** Below this zoom neutral sites are hidden on the overview. */
const NEUTRAL_SITE_MIN_SCALE = 0.12;
const MIN_SCALE = 0.02;
const MAX_SCALE = 2.5;
/** How far out past "whole world" the wheel may zoom (the map then sits centred). */
const FIT_ZOOM_SLACK = 0.85;
/**
 * Where the shell's panels cover the board (left column, right column, plan
 * bar): a unit under them counts as hidden for `reveal`.
 */
const PANEL_INSETS: Insets = { left: 312, right: 344, top: 16, bottom: 76 };
const ATTACK_COLOR = 0xe0503a;
const DISORDER_COLOR = 0xf5b342;
const IVORY = 0xf2e6c8;
const DARK = 0x101114;
const OVERVIEW_HEX = 2; // overview texture: hex radius in texture pixels
const MINIMAP_SIZE = 220;
const MINIMAP_MARGIN = 12;
/** Join codes per owner hex (edges 0-2, vertices 3-4, mouths 5-10); sprite key = index * count + code. */
const JOIN_CODES_PER_HEX = JOIN_CODE_COUNT;
const NO_JOINS: readonly JoinPlacement[] = [];

/**
 * Where each column of a 1–6 stack sits in its hex (in HEX_SIZE units from the
 * centre) and how big it's drawn (× the standard unit size). Six is the stack cap.
 */
const CONVOY_SLOTS: readonly { scale: number; slots: readonly (readonly [number, number])[] }[] = [
  { scale: 1, slots: [[0, 0]] },
  { scale: 0.68, slots: [[-0.32, -0.22], [0.32, 0.22]] },
  { scale: 0.6, slots: [[0, -0.4], [-0.38, 0.28], [0.38, 0.28]] },
  { scale: 0.55, slots: [[-0.36, -0.32], [0.36, -0.32], [-0.36, 0.32], [0.36, 0.32]] },
  { scale: 0.48, slots: [[-0.34, -0.55], [0.34, -0.55], [-0.34, 0], [0.34, 0], [0, 0.55]] },
  { scale: 0.48, slots: [[-0.34, -0.55], [0.34, -0.55], [-0.34, 0], [0.34, 0], [-0.34, 0.55], [0.34, 0.55]] },
];

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
  routes: readonly OverlayRoute[];
  barrages: readonly { from: Axial; target: Axial }[];
  /** Hexes a click would move the selected unit to; `attack`: a hostile holds it. */
  reach: readonly { hex: Axial; attack: boolean }[];
  /** Hexes the selected artillery can shell (barrage mode). */
  barrageRange: readonly Axial[];
  recruits: readonly Axial[];
  /** Last turn's trails and fights (`null`: hidden). */
  lastTurn: {
    moves: readonly { owner: string | null; path: readonly Axial[] }[];
    fights: readonly Axial[];
    captures: readonly { coord: Axial; owner: string }[];
    barrages: readonly Axial[];
  } | null;
}

export interface OverlayRoute {
  from: Axial;
  path: readonly Axial[];
  kind: "travel" | "standing" | "move" | "march";
  selected: boolean;
  /** Emphasised without being selected (its Army-panel row is hovered). */
  highlighted: boolean;
  /** The hostile-held hex a move ends on (drawn as an attack), else `null`. */
  attack: Axial | null;
}

/** A travel route previewed under the pointer before a click commits it. */
export interface RoutePreview {
  from: Axial;
  path: readonly Axial[];
  /** The route runs within auto-deploy distance of a hostile (drawn amber). */
  caution: boolean;
}

/** The hex under the pointer, with the pointer's position on the canvas. */
export interface Hover {
  hex: Axial;
  x: number;
  y: number;
}

/** What a playback beat changes on the board beyond its units: sight and site owners. */
export interface PlaybackBeatScene {
  /** The viewer's visible hexes at this beat (`hexKey` keys). */
  visible: ReadonlySet<string>;
  /** Owners of the sites that change hands this turn, as of this beat (`null`: neutral). */
  control: ReadonlyMap<string, string | null>;
}

/** A unit group drawn together: one hex's units at rest, or one unit on the move. */
interface UnitGroup {
  x: number;
  y: number;
  list: Unit[];
  alpha: number;
}

export interface WorldRendererOptions {
  /** A click on a map hex; `shift` was held (Shift+click orders a swap). */
  onHexClick?: (hex: Axial, mods: { shift: boolean }) => void;
}

export type { Camera };

export class WorldRenderer {
  readonly app: Application;
  private readonly map: WorldMap;
  private readonly world = new Container();
  private readonly tileLayer = new Container();
  /** Join sprites, kept in draw order by `zIndex` (they are added in camera order). */
  private readonly joinLayer = new Container({ sortableChildren: true });
  private readonly fogLayer = new Container();
  private readonly overview: Sprite;
  private readonly sites = new Graphics();
  private readonly siteLayer = new Container();
  private readonly siteSprites = new Map<string, Sprite>();
  private siteArt: LoadedSites | null = null;
  private columnArt: LoadedColumns = {};
  /** Hexes where a player's (not a garrison's) unit stands; refreshed per scene. */
  private armyHexes = new Set<string>();
  /** Settlement control by hex key (absent: a vacant base site); refreshed per scene. */
  private control = new Map<string, string | null>();
  private readonly unitLayer = new Container();
  /** Unit tokens drawn as shapes (overview tokens, columns without art), under the sprites. */
  private readonly unitTokens = new Graphics();
  private readonly unitSpriteLayer = new Container();
  /** Badges over the units: HP bars, disorder, garrison shields, stack pips. */
  private readonly unitMarks = new Graphics();
  private readonly unitLabelLayer = new Container();
  /** Pooled unit sprites and labels: reused every draw, the spare ones hidden. */
  private readonly unitSprites: Sprite[] = [];
  private unitSpritesUsed = 0;
  private readonly unitLabels: Text[] = [];
  private unitLabelsUsed = 0;
  private readonly overlay = new Graphics();
  private readonly hoverLayer = new Graphics();
  private readonly selection = new Graphics();
  private overlayData: WorldOverlay | null = null;
  private preview: RoutePreview | null = null;
  private readonly minimap = new Container();
  private readonly minimapFrame = new Graphics();
  private readonly minimapSites = new Graphics();
  private readonly minimapMask = new Graphics();
  private readonly tileSprites = new Map<number, Sprite>();
  private readonly joinSprites = new Map<number, Sprite>();
  private readonly joinPool: Sprite[] = [];
  /** The joins each hex owns, by hex index; the map is static, so filled lazily once. */
  private readonly ownedJoins = new Map<number, readonly JoinPlacement[]>();
  private readonly fogSprites = new Map<number, Sprite>();
  private readonly spritePool: Sprite[] = [];
  private readonly fogPool: Sprite[] = [];
  /** A white board-size hex: tinted for fog, and for hexes whose terrain has no tile art. */
  private readonly plainHexTexture: Texture;
  private readonly roadTiles: Map<string, RoadTileRef>;
  private readonly riverCourse: Map<string, number[]>;
  /** Lake neighbour masks by hex key (a lake hex draws its mask's tile). */
  private readonly lakeMasks: Map<string, number>;
  /** River mouths by river hex key (where a mouth sprite is drawn). */
  private readonly mouths: Map<string, RiverOutlet>;
  /** Painted tiles of the map's leveled sites (static). */
  private readonly staticSiteTiles: SiteTileMap;
  /** Painted tiles of the founded bases; recomputed per scene (`setScene`). */
  private baseSiteTiles: SiteTileMap = new Map();
  private readonly overviewScale: number;
  private tiles: LoadedTiles | null = null;
  private joins: LoadedJoins | null = null;
  private units: LoadedUnits | null = null;
  private scene: WorldScene | null = null;
  private readonly options: WorldRendererOptions;
  private readonly bounds: ReturnType<typeof worldPixelBounds>;
  private drag: { x: number; y: number; moved: boolean } | null = null;
  /** Live pointers on the canvas (two of them pinch-zoom). */
  private readonly pointers = new Map<number, { x: number; y: number }>();
  private pinch: { dist: number; midX: number; midY: number } | null = null;
  private minimapDrag = false;
  /** The last mouse position over the canvas (`null`: outside it). */
  private pointer: { x: number; y: number } | null = null;
  private hovered: Axial | null = null;
  private readonly hoverListeners = new Set<(hover: Hover | null) => void>();
  private panInput: PanInput = { x: 0, y: 0, fast: false };
  private jump: { from: Camera; to: Camera; start: number; duration: number } | null = null;
  /** The scene needs redrawing for the camera or data; done once per frame by `tick`. */
  private dirty = true;
  /** Only the units and effects need redrawing (a playback frame). */
  private unitsDirty = false;
  /** Turn playback in progress: its beat scene and the current frame. */
  private playback: { beat: PlaybackBeatScene; units: readonly DrawnUnit[]; effects: readonly Effect[] } | null = null;
  /** Settlement control from the live scene (playback overrides it per beat). */
  private liveControl = new Map<string, string | null>();
  private readonly effectsLayer = new Graphics();
  private readonly effectLabelLayer = new Container();
  private readonly effectLabels: Text[] = [];
  private effectLabelsUsed = 0;
  private readonly cameraListeners = new Set<() => void>();
  /** Minimap pixels per world pixel (set when the minimap is built). */
  private minimapScale = 1;

  constructor(app: Application, map: WorldMap, options: WorldRendererOptions = {}) {
    this.app = app;
    this.map = map;
    this.options = options;
    // Road and river course graphs (junction chords pruned), built once per map.
    this.roadTiles = roadTileRefs(map.hexes, roadCourseEdges(map.hexes, (q, r) => map.hexAt(q, r)));
    const terrainAt = (q: number, r: number) => map.hexAt(q, r)?.terrain;
    const riverCoords = map.hexes.filter((h) => h.terrain === "river" || h.terrain === "bridge").map((h) => h.coord);
    this.riverCourse = riverCourseEdges(riverCoords, terrainAt);
    this.lakeMasks = lakeMasks(
      map.hexes.filter((h) => h.terrain === "lake").map((h) => h.coord),
      terrainAt,
    );
    this.mouths = new Map(
      (MOUTH_ART ? riverMouths(riverCoords, terrainAt) : []).map((m) => [hexKey(m.coord.q, m.coord.r), m]),
    );
    this.staticSiteTiles = staticSiteTiles(map.sites);

    const overviewCanvas = drawOverview(map);
    const overviewTexture = Texture.from(overviewCanvas);
    this.overview = new Sprite(overviewTexture);
    this.overviewScale = HEX_SIZE / OVERVIEW_HEX;
    this.overview.scale.set(this.overviewScale);
    this.overview.position.set(-HEX_SIZE, -HEX_SIZE);
    this.plainHexTexture = hexTexture(app);

    this.world.addChild(
      this.overview,
      this.tileLayer,
      this.joinLayer,
      this.fogLayer,
      this.siteLayer,
      this.sites,
      this.overlay,
      this.hoverLayer,
      this.unitLayer,
      this.selection,
      this.effectsLayer,
      this.effectLabelLayer,
    );
    this.unitLayer.addChild(this.unitTokens, this.unitSpriteLayer, this.unitMarks, this.unitLabelLayer);
    app.stage.addChild(this.world, this.minimap);
    this.bounds = worldPixelBounds(map, HEX_SIZE);
    this.buildMinimap(overviewTexture);

    app.stage.eventMode = "static";
    app.stage.hitArea = app.screen;
    app.stage.on("pointerdown", this.onPointerDown);
    app.stage.on("pointermove", this.onPointerMove);
    app.stage.on("pointerup", this.onPointerUp);
    app.stage.on("pointerupoutside", this.onPointerUp);
    app.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    app.canvas.addEventListener("pointerleave", this.onPointerLeave);
    app.renderer.on("resize", this.onResize);
    app.ticker.add(this.tick);

    this.fitAll(false);
  }

  /**
   * Set once `destroy` has run. When the whole world screen unmounts, React runs
   * the board's cleanup (which destroys this) before its siblings' cleanups,
   * which still call in to unhook themselves; those late calls must be no-ops.
   */
  destroyed = false;

  destroy(): void {
    this.destroyed = true;
    this.app.ticker.remove(this.tick);
    this.app.canvas.removeEventListener("wheel", this.onWheel);
    this.app.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    this.app.renderer.off("resize", this.onResize);
    this.hoverListeners.clear();
    this.cameraListeners.clear();
    this.app.destroy(true, { children: true });
  }

  setTextures(
    tiles: LoadedTiles | null,
    joins: LoadedJoins | null,
    units: LoadedUnits | null,
    sites: LoadedSites | null,
    columns: LoadedColumns,
  ): void {
    this.tiles = tiles;
    this.joins = joins;
    this.units = units;
    this.siteArt = sites;
    this.columnArt = columns;
    for (const sprite of this.tileSprites.values()) this.releaseTile(sprite);
    this.tileSprites.clear();
    for (const sprite of this.joinSprites.values()) this.releaseJoin(sprite);
    this.joinSprites.clear();
    this.invalidate();
  }

  setScene(scene: WorldScene): void {
    this.scene = scene;
    const bases = baseSiteTiles(this.map, scene.state.settlements);
    const changed = changedBaseTiles(this.baseSiteTiles, bases);
    this.baseSiteTiles = bases;
    this.retileHexes(changed);
    this.armyHexes = new Set(
      scene.state.units.filter((u) => u.owner !== NEUTRAL_OWNER).map((u) => hexKey(u.coord.q, u.coord.r)),
    );
    this.liveControl = new Map(scene.state.settlements.map((s) => [hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null]));
    this.applyControl();
    this.drawMinimapSites();
    this.invalidate();
  }

  // --- turn playback ------------------------------------------------------------

  /** Whether turn playback has the board. */
  get playbackActive(): boolean {
    return this.playback !== null;
  }

  /**
   * Enter playback, or move it to a new beat (fog and site owners), or with
   * `null` leave it and restore the live scene exactly. Entering suspends the
   * planning marks, the hover outline and preview, and board clicks.
   */
  setPlayback(beat: PlaybackBeatScene | null): void {
    if (this.destroyed) return;
    if (beat === null) {
      if (this.playback === null) return;
      this.playback = null;
      this.effectsLayer.clear();
      for (const label of this.effectLabels) label.visible = false;
    } else {
      this.playback = { beat, units: this.playback?.units ?? [], effects: this.playback?.effects ?? [] };
      this.setHovered(null);
    }
    this.applyControl();
    this.drawOverlay();
    this.invalidate();
  }

  /** The units and effects of one playback frame; redraws only those layers. */
  setPlaybackFrame(units: readonly DrawnUnit[], effects: readonly Effect[]): void {
    if (this.playback === null || this.destroyed) return;
    this.playback.units = units;
    this.playback.effects = effects;
    this.unitsDirty = true;
  }

  /**
   * Bring `hexes` (a beat's action) into view for playback: no move if they are
   * already comfortably on screen, else an animated pan that keeps the zoom when
   * they fit (`followCamera`). Not reported as a manual camera move.
   */
  followHexes(hexes: readonly Axial[], animate = true): void {
    if (hexes.length === 0 || this.destroyed) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const h of hexes) {
      const { x, y } = axialToPixel(h.q, h.r, HEX_SIZE);
      minX = Math.min(minX, x - HEX_SIZE);
      maxX = Math.max(maxX, x + HEX_SIZE);
      minY = Math.min(minY, y - HEX_SIZE);
      maxY = Math.max(maxY, y + HEX_SIZE);
    }
    const from = this.jump?.to ?? this.camera;
    const to = followCamera(
      from,
      { minX, minY, maxX, maxY },
      this.app.screen,
      PANEL_INSETS,
      TILE_MIN_SCALE,
      TACTICAL_SCALE,
    );
    if (to !== null) this.moveCamera(to, animate);
  }

  /** Hear when the player moves the camera themselves (drag, wheel, keys, buttons). */
  onUserCamera(listener: () => void): () => void {
    this.cameraListeners.add(listener);
    return () => this.cameraListeners.delete(listener);
  }

  private userMovedCamera(): void {
    for (const listener of this.cameraListeners) listener();
  }

  /** Site owners as drawn: the live scene's, overridden per beat during playback. */
  private applyControl(): void {
    const overrides = this.playback?.beat.control;
    if (overrides === undefined || overrides.size === 0) {
      this.control = this.liveControl;
      return;
    }
    const control = new Map(this.liveControl);
    for (const [key, owner] of overrides) control.set(key, owner);
    this.control = control;
  }

  setOverlay(overlay: WorldOverlay): void {
    this.overlayData = overlay;
    this.drawOverlay();
  }

  /** Show (or with `null` clear) a dashed travel route preview. */
  setPreview(preview: RoutePreview | null): void {
    if (this.destroyed) return;
    this.preview = preview;
    this.drawHover();
  }

  /** Hear when the hovered hex changes (`null`: none, e.g. while dragging). Returns an unsubscribe. */
  onHover(listener: (hover: Hover | null) => void): () => void {
    this.hoverListeners.add(listener);
    return () => this.hoverListeners.delete(listener);
  }

  /** The pan keys currently held; the ticker pans while any are. */
  setPanInput(input: PanInput): void {
    if (input.x !== 0 || input.y !== 0) {
      this.jump = null;
      this.userMovedCamera();
    }
    this.panInput = input;
  }

  get camera(): Camera {
    return { x: this.world.x, y: this.world.y, scale: this.world.scale.x };
  }

  /** The minimap's box on the canvas, so DOM controls can sit clear of it. */
  get minimapBox(): { x: number; y: number; width: number; height: number } {
    const b = this.bounds;
    const s = this.minimapScale;
    return { x: this.minimap.x, y: this.minimap.y, width: (b.maxX - b.minX) * s, height: (b.maxY - b.minY) * s };
  }

  /** Centre the view on `hex`, keeping the zoom (at least tile zoom). */
  panTo(hex: Axial, animate = true): void {
    this.userMovedCamera();
    this.centerOn(hex, Math.max(this.world.scale.x, TILE_MIN_SCALE), animate);
  }

  /** Centre the view on `hex` at `scale` (default: a comfortable tactical zoom). */
  centerOn(hex: Axial, scale = TACTICAL_SCALE, animate = true): void {
    this.userMovedCamera();
    const { x, y } = axialToPixel(hex.q, hex.r, HEX_SIZE);
    this.moveCamera(centredOn(x, y, scale, this.app.screen), animate);
  }

  /** Bring `hex` into view only if it is off-screen or under a panel. */
  reveal(hex: Axial): void {
    if (!this.comfortablyVisible(hex)) this.panTo(hex);
  }

  /**
   * Bring a unit at `hex` into view to work with it: from the overview (where
   * units are dots) zoom in to tactical zoom, otherwise as `reveal`.
   */
  focusUnit(hex: Axial): void {
    if (this.world.scale.x < TILE_MIN_SCALE) this.centerOn(hex, TACTICAL_SCALE);
    else this.reveal(hex);
  }

  /** Whether `hex` is on screen and clear of the panels. */
  comfortablyVisible(hex: Axial): boolean {
    const { x, y } = axialToPixel(hex.q, hex.r, HEX_SIZE);
    const s = this.world.scale.x;
    return comfortablyVisible(this.world.x + x * s, this.world.y + y * s, this.app.screen, PANEL_INSETS);
  }

  /** Zoom by `factor` about the screen centre. */
  zoomBy(factor: number, animate = true): void {
    // Chain off an unfinished jump, so repeated presses compound.
    this.userMovedCamera();
    const from = this.jump?.to ?? this.camera;
    const { width, height } = this.app.screen;
    this.moveCamera(zoomAbout(from, from.scale * factor, width / 2, height / 2), animate);
  }

  /** Zoom out to show the whole world, centred. */
  fitAll(animate = true): void {
    this.userMovedCamera();
    this.moveCamera(fitCamera(this.bounds, this.app.screen), animate);
  }

  // --- camera -----------------------------------------------------------------------

  /** The lowest zoom: a little past the whole world. */
  private minScale(): number {
    return Math.max(MIN_SCALE, fitScale(this.bounds, this.app.screen) * FIT_ZOOM_SLACK);
  }

  /** `cam` with its zoom and position held inside the camera limits. */
  private limit(cam: Camera): Camera {
    const scale = clamp(cam.scale, this.minScale(), MAX_SCALE);
    const scaled = scale === cam.scale ? cam : zoomAbout(cam, scale, this.app.screen.width / 2, this.app.screen.height / 2);
    return clampCamera(scaled, this.bounds, this.app.screen);
  }

  /** Apply a camera now (limited), dropping any jump in flight. */
  private setCamera(cam: Camera): void {
    this.jump = null;
    this.applyCamera(this.limit(cam));
  }

  private applyCamera(cam: Camera): void {
    this.world.scale.set(cam.scale);
    this.world.position.set(cam.x, cam.y);
    this.invalidate();
  }

  /** Go to `cam` (limited), easing over `JUMP_MS` unless reduced motion is asked for. */
  private moveCamera(cam: Camera, animate: boolean): void {
    const to = this.limit(cam);
    if (!animate || prefersReducedMotion()) {
      this.setCamera(to);
      return;
    }
    this.jump = { from: this.camera, to, start: performance.now(), duration: JUMP_MS };
  }

  private invalidate(): void {
    this.dirty = true;
  }

  private tick = (ticker: Ticker): void => {
    // A long frame (a background tab) shouldn't fling the map.
    const dt = Math.min(ticker.deltaMS, 50) / 1000;
    if (this.panInput.x !== 0 || this.panInput.y !== 0) {
      const { dx, dy } = panStep(this.panInput, dt);
      this.setCamera({ ...this.camera, x: this.world.x + dx, y: this.world.y + dy });
    }
    if (this.jump !== null) {
      const { from, to, start, duration } = this.jump;
      const t = (performance.now() - start) / duration;
      this.applyCamera(cameraBetween(from, to, t, this.app.screen));
      if (t >= 1) this.jump = null;
    }
    if (this.dirty) {
      this.dirty = false;
      this.unitsDirty = false;
      this.refresh();
    } else if (this.unitsDirty) {
      // A playback frame: just the units and effects, the tiles stay as they are.
      this.unitsDirty = false;
      const { range, tileMode } = this.viewRange();
      this.drawUnits(range, tileMode);
      this.drawEffects();
    }
  };

  private onResize = (): void => {
    this.positionMinimap();
    this.setCamera(this.camera);
  };

  // --- pointer input ----------------------------------------------------------------

  private onPointerDown = (e: FederatedPointerEvent): void => {
    const { x, y } = e.global;
    if (this.pointers.size === 0 && this.minimapHit(x, y)) {
      this.minimapDrag = true;
      this.jumpFromMinimap(x, y, true);
      return;
    }
    this.jump = null;
    this.pointers.set(e.pointerId, { x, y });
    if (this.pointers.size === 2) {
      // A second finger: pinch-zoom about the midpoint, and never a click.
      const [a, b] = [...this.pointers.values()];
      this.pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y), midX: (a.x + b.x) / 2, midY: (a.y + b.y) / 2 };
      this.drag = null;
      this.setHovered(null);
    } else if (this.pointers.size === 1) {
      this.drag = { x, y, moved: false };
    }
  };

  private onPointerMove = (e: FederatedPointerEvent): void => {
    const { x, y } = e.global;
    if (this.minimapDrag) {
      this.jumpFromMinimap(x, y, false);
      return;
    }
    // Pixi hears moves over the whole document; over a panel the map isn't hovered.
    if (e.pointerType === "mouse") this.pointer = e.nativeEvent.target === this.app.canvas ? { x, y } : null;
    if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x, y });
    if (this.pinch !== null && this.pointers.size >= 2) {
      this.pinchMove();
      return;
    }
    if (this.drag === null) {
      if (e.pointerType === "mouse") this.updateHover();
      return;
    }
    const dx = x - this.drag.x;
    const dy = y - this.drag.y;
    if (!this.drag.moved && Math.hypot(dx, dy) < 4) return;
    if (!this.drag.moved) {
      this.setHovered(null);
      this.userMovedCamera();
    }
    this.drag = { x, y, moved: true };
    this.setCamera({ ...this.camera, x: this.world.x + dx, y: this.world.y + dy });
  };

  private pinchMove(): void {
    const pinch = this.pinch;
    if (pinch === null) throw new Error("pinch move without a pinch");
    const [a, b] = [...this.pointers.values()];
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    const midX = (a.x + b.x) / 2;
    const midY = (a.y + b.y) / 2;
    this.userMovedCamera();
    const zoomed = zoomAbout(this.camera, this.world.scale.x * (dist / Math.max(1, pinch.dist)), pinch.midX, pinch.midY);
    this.setCamera({ ...zoomed, x: zoomed.x + midX - pinch.midX, y: zoomed.y + midY - pinch.midY });
    this.pinch = { dist, midX, midY };
  }

  private onPointerUp = (e: FederatedPointerEvent): void => {
    if (this.minimapDrag) {
      this.minimapDrag = false;
      return;
    }
    this.pointers.delete(e.pointerId);
    if (this.pinch !== null) {
      // Lifting one finger of a pinch carries on as a drag with the other.
      this.pinch = null;
      const rest = [...this.pointers.values()][0];
      this.drag = rest === undefined ? null : { x: rest.x, y: rest.y, moved: true };
      return;
    }
    const drag = this.drag;
    this.drag = null;
    if (drag === null || drag.moved) {
      if (e.pointerType === "mouse") this.updateHover();
      return;
    }
    // Playback owns the board: a click gives no orders.
    if (this.playback !== null) return;
    const local = this.world.toLocal(e.global);
    const hex = pixelToAxial(local.x, local.y, HEX_SIZE);
    if (this.map.hexAt(hex.q, hex.r) !== undefined) this.options.onHexClick?.(hex, { shift: e.shiftKey });
  };

  private onPointerLeave = (): void => {
    this.pointer = null;
    this.setHovered(null);
  };

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    this.userMovedCamera();
    const rect = this.app.canvas.getBoundingClientRect();
    const factor = Math.exp(-e.deltaY * 0.0015);
    this.setCamera(zoomAbout(this.camera, this.world.scale.x * factor, e.clientX - rect.left, e.clientY - rect.top));
  };

  // --- hover ------------------------------------------------------------------------

  /** Re-read the hex under the mouse (it moved, or the camera moved under it). */
  private updateHover(): void {
    const p = this.pointer;
    if (p === null || this.playback !== null || this.drag?.moved === true || this.pinch !== null || this.minimapHit(p.x, p.y)) {
      this.setHovered(null);
      return;
    }
    const local = this.world.toLocal(p);
    const hex = pixelToAxial(local.x, local.y, HEX_SIZE);
    this.setHovered(this.map.hexAt(hex.q, hex.r) === undefined ? null : hex);
  }

  private setHovered(hex: Axial | null): void {
    const same = hex === null ? this.hovered === null : this.hovered !== null && axialEqual(hex, this.hovered);
    if (same) return;
    this.hovered = hex;
    this.drawHover();
    const p = this.pointer;
    const hover = hex === null || p === null ? null : { hex, x: p.x, y: p.y };
    for (const listener of this.hoverListeners) listener(hover);
  }

  /** The hover outline (tile zoom only) and the travel preview. */
  private drawHover(): void {
    const g = this.hoverLayer.clear();
    if (this.playback !== null) return;
    const scale = this.world.scale.x;
    const px = (n: number) => n / scale;
    const centre = (a: Axial) => axialToPixel(a.q, a.r, HEX_SIZE);
    if (this.hovered !== null && scale >= TILE_MIN_SCALE) {
      const { x, y } = centre(this.hovered);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.97)).stroke({ color: IVORY, width: px(1.5), alpha: 0.85 });
    }
    const preview = this.preview;
    if (preview === null || preview.path.length === 0) return;
    const color = preview.caution ? DISORDER_COLOR : 0x3d7eff;
    const pts = [preview.from, ...preview.path].map(centre);
    const dashes = dashSegments(pts, px(9), px(6));
    for (const [x0, y0, x1, y1] of dashes) g.moveTo(x0, y0).lineTo(x1, y1);
    g.stroke({ color: DARK, width: px(5), alpha: 0.45, cap: "round" });
    for (const [x0, y0, x1, y1] of dashes) g.moveTo(x0, y0).lineTo(x1, y1);
    g.stroke({ color, width: px(3), alpha: 0.95, cap: "round" });
    const end = pts[pts.length - 1];
    g.circle(end.x, end.y, Math.max(px(6), HEX_SIZE * 0.24)).stroke({ color, width: px(2.5) });
  }

  // --- drawing ----------------------------------------------------------------------

  /** The cells on screen (plus a margin) and whether tiles are shown at this zoom. */
  private viewRange(): { range: CellRange; tileMode: boolean; x0: number; y0: number; x1: number; y1: number } {
    const s = this.world.scale.x;
    const tileMode = s >= TILE_MIN_SCALE && this.tiles !== null;
    const x0 = -this.world.x / s;
    const y0 = -this.world.y / s;
    const x1 = x0 + this.app.screen.width / s;
    const y1 = y0 + this.app.screen.height / s;
    return { range: visibleCells(this.map, x0, y0, x1, y1, HEX_SIZE), tileMode, x0, y0, x1, y1 };
  }

  private refresh(): void {
    const { range, tileMode, x0, y0, x1, y1 } = this.viewRange();

    this.overview.visible = !tileMode;
    this.tileLayer.visible = tileMode;
    this.joinLayer.visible = tileMode;
    this.fogLayer.visible = tileMode;
    if (tileMode) this.syncTiles(range);
    this.drawSites(range, tileMode);
    this.drawUnits(range, tileMode);
    this.drawEffects();
    this.drawOverlay();
    this.drawMinimapFrame(x0, y0, x1, y1);
    // The camera may have moved under a still mouse (keys, a jump).
    if (this.drag === null) this.updateHover();
    this.drawHover();
  }

  private syncTiles(range: CellRange): void {
    const want = new Set<number>();
    const { width } = this.map;
    const visible = this.playback?.beat.visible ?? this.scene?.visible;
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
    if (this.joins !== null) this.syncJoins(range, this.joins);
  }

  /**
   * Join sprites for the same cells as the tiles: each join is owned by one hex
   * (`joins.ts`) and drawn only while every hex it spans is in the range, i.e.
   * has its tile sprite, so a join never hangs over a hex whose tile is culled.
   */
  private syncJoins(range: CellRange, joins: LoadedJoins): void {
    const want = new Set<number>();
    const { width } = this.map;
    const inRange = (a: Axial): boolean => {
      const { col, row } = axialToOffset(a);
      return col >= range.col0 && col <= range.col1 && row >= range.row0 && row <= range.row1;
    };
    for (let row = range.row0; row <= range.row1; row++) {
      for (let col = range.col0; col <= range.col1; col++) {
        const index = row * width + col;
        for (const join of this.joinsOwnedAt(index)) {
          if (!join.members.every(inRange)) continue;
          const key = index * JOIN_CODES_PER_HEX + join.code;
          want.add(key);
          if (!this.joinSprites.has(key)) this.joinSprites.set(key, this.placeJoin(join, joins));
        }
      }
    }
    for (const [key, sprite] of this.joinSprites) {
      if (!want.has(key)) {
        this.releaseJoin(sprite);
        this.joinSprites.delete(key);
      }
    }
  }

  private joinsOwnedAt(index: number): readonly JoinPlacement[] {
    let owned = this.ownedJoins.get(index);
    if (owned === undefined) {
      // A join never spans a hex painted with a site tile: that tile has a plains
      // rim, so a woods/city seam into it would hang over the painting. Only old
      // stored maps (sites on town/city hexes) have such joins at all.
      const coord = this.map.hexes[index].coord;
      const mouth = this.mouths.get(hexKey(coord.q, coord.r));
      const found = [
        ...joinsOwnedBy(coord, (q, r) => this.map.hexAt(q, r)?.terrain, JOIN_TERRAINS, HEX_SIZE),
        ...(mouth === undefined ? [] : [mouthPlacement(mouth, HEX_SIZE)]),
      ].filter((join) => !join.members.some((m) => this.staticSiteTiles.has(hexKey(m.q, m.r))));
      owned = found.length === 0 ? NO_JOINS : found;
      this.ownedJoins.set(index, owned);
    }
    return owned;
  }

  private placeJoin(join: JoinPlacement, joins: LoadedJoins): Sprite {
    const sprite = this.joinPool.pop() ?? new Sprite();
    sprite.texture = joinVariant(joins, join);
    sprite.anchor.set(0.5);
    sprite.scale.set(joinSpriteScale(HEX_SIZE));
    sprite.position.set(join.x, join.y);
    sprite.zIndex = joinDrawRank(join);
    this.joinLayer.addChild(sprite);
    return sprite;
  }

  private releaseJoin(sprite: Sprite): void {
    sprite.removeFromParent();
    this.joinPool.push(sprite);
  }

  private placeTile(hex: WorldMap["hexes"][number]): Sprite {
    const sprite = this.spritePool.pop() ?? new Sprite();
    this.textureTile(sprite, hex);
    sprite.anchor.set(0.5);
    const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
    sprite.position.set(x, y);
    sprite.visible = true;
    this.tileLayer.addChild(sprite);
    return sprite;
  }

  /**
   * Give a tile sprite its hex's texture: a painted site/base tile, else its
   * terrain tile (a lake by its neighbour mask), else (no tile art for the hex)
   * a plain hex tinted with the terrain's flat fill.
   */
  private textureTile(sprite: Sprite, hex: WorldMap["hexes"][number]): void {
    const tiles = this.tiles;
    if (tiles === null) throw new Error("tile texture asked for before textures loaded");
    const choice = hexTileChoice(hex.coord.q, hex.coord.r, this.staticSiteTiles, this.baseSiteTiles);
    // Site tiles are painted unmirrored at terrain-tile size.
    const ref =
      choice.kind === "site"
        ? { tex: siteTileTexture(choice.key), mirror: false }
        : hexTileRef(hex, tiles, this.riverCourse, this.roadTiles, this.lakeMasks);
    // Pooled sprites are reused across hexes, so the tint is always set.
    if (ref === null) {
      sprite.texture = this.plainHexTexture;
      sprite.tint = TERRAIN_FILL[hex.terrain];
      sprite.scale.set(1);
      return;
    }
    sprite.tint = 0xffffff;
    sprite.texture = ref.tex;
    const scale = (2 * HEX_SIZE) / ref.tex.width;
    sprite.scale.set(ref.mirror ? -scale : scale, scale);
  }

  /**
   * Re-texture the live tile sprites of `hexes` (a base founded or gone). Hexes
   * that are culled have no sprite; they pick the new tile up when next placed.
   */
  private retileHexes(hexes: readonly Axial[]): void {
    for (const coord of hexes) {
      const { col, row } = axialToOffset(coord);
      const index = row * this.map.width + col;
      const sprite = this.tileSprites.get(index);
      if (sprite !== undefined) this.textureTile(sprite, this.map.hexes[index]);
    }
  }

  private releaseTile(sprite: Sprite): void {
    sprite.removeFromParent();
    this.spritePool.push(sprite);
  }

  private placeFog(hex: WorldMap["hexes"][number]): Sprite {
    const sprite = this.fogPool.pop() ?? new Sprite(this.plainHexTexture);
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
    const control = this.control;
    const scale = this.world.scale.x;
    const radius = HEX_SIZE * 0.92;
    const art = tileMode ? this.siteArt : null;
    this.siteLayer.visible = art !== null;
    const shown = new Set<string>();
    for (const site of this.map.sites) {
      const { col, row } = { col: site.coord.q, row: site.coord.r + (site.coord.q - (site.coord.q & 1)) / 2 };
      if (col < range.col0 || col > range.col1 || row < range.row0 || row > range.row1) continue;
      const key = hexKey(site.coord.q, site.coord.r);
      if (!control.has(key)) continue; // a vacant base site: nothing there yet
      const owner = control.get(key) ?? null;
      const { x, y } = axialToPixel(site.coord.q, site.coord.r, HEX_SIZE);
      const color = ownerColor(owner, scene.viewer);
      // A painted site or base tile already says what the hex is; only cities
      // (drawn with their city terrain tile) keep the round emblem.
      if (art !== null && !this.paintedSite(key)) {
        shown.add(key);
        this.placeSiteIcon(key, art[site.kind], x, y, this.armyHexes.has(key));
      }
      if (tileMode) {
        g.poly(hexCornerPoints(x, y, radius)).stroke({ color, width: owner === null ? 2 : 4, alpha: 0.95 });
      } else if (owner === null) {
        // Neutral sites are context, not news: small, and gone when far out.
        if (scale >= NEUTRAL_SITE_MIN_SCALE) g.circle(x, y, 2.2 / scale).fill({ color, alpha: 0.7 });
      } else if (owner === scene.viewer && site.kind === "base") {
        // Your own base: a ringed marker that stands out from every other dot.
        g.circle(x, y, 9 / scale).stroke({ color: IVORY, width: 2.5 / scale });
        g.circle(x, y, 5.5 / scale).fill({ color }).stroke({ color: DARK, width: 1.2 / scale });
      } else {
        const r = (site.kind === "base" ? 5 : 3.5) / scale;
        g.circle(x, y, r).fill({ color }).stroke({ color: owner === scene.viewer ? IVORY : DARK, width: 1 / scale });
      }
    }
    for (const [key, sprite] of this.siteSprites) {
      if (!shown.has(key)) {
        sprite.destroy();
        this.siteSprites.delete(key);
      }
    }
  }

  /** Whether the hex (by `hexKey`) is drawn with a painted site or base tile. */
  private paintedSite(key: string): boolean {
    return this.baseSiteTiles.has(key) || this.staticSiteTiles.has(key);
  }

  /**
   * A site's painted icon: across most of the hex when it's empty or only a
   * garrison holds it, shrunk to a corner badge when a player's unit stands there
   * so the unit stays readable.
   */
  private placeSiteIcon(key: string, texture: Texture, x: number, y: number, underUnit: boolean): void {
    let sprite = this.siteSprites.get(key);
    if (sprite === undefined) {
      sprite = new Sprite(texture);
      sprite.anchor.set(0.5);
      this.siteLayer.addChild(sprite);
      this.siteSprites.set(key, sprite);
    }
    sprite.texture = texture;
    const size = underUnit ? HEX_SIZE * 0.7 : HEX_SIZE * 1.3;
    sprite.scale.set(size / texture.width);
    sprite.position.set(underUnit ? x - HEX_SIZE * 0.5 : x, underUnit ? y - HEX_SIZE * 0.5 : y);
  }

  private drawUnits(range: CellRange, tileMode: boolean): void {
    const tokens = this.unitTokens.clear();
    const marks = this.unitMarks.clear();
    this.unitSpritesUsed = 0;
    this.unitLabelsUsed = 0;
    const scene = this.scene;
    if (scene !== null) {
      const groups = this.playback === null ? liveGroups(scene.state.units, range) : playbackGroups(this.playback.units, range);
      this.drawUnitGroups(groups, scene.viewer, tileMode, tokens, marks);
    }
    // Pooled sprites and labels not used this draw stay around, hidden.
    for (let i = this.unitSpritesUsed; i < this.unitSprites.length; i++) this.unitSprites[i].visible = false;
    for (let i = this.unitLabelsUsed; i < this.unitLabels.length; i++) this.unitLabels[i].visible = false;
  }

  private drawUnitGroups(groups: Iterable<UnitGroup>, viewer: string, tileMode: boolean, tokens: Graphics, marks: Graphics): void {
    const units = this.units;
    const scale = this.world.scale.x;
    for (const { x, y, list, alpha } of groups) {
      const lead = list.find((u) => u.posture !== "column") ?? list[0];
      const color = ownerColor(lead.owner, viewer);
      const mine = lead.owner === viewer;
      const columns = list.filter((u) => u.posture === "column").length;
      if (tileMode && lead.posture === "column" && lead.owner !== NEUTRAL_OWNER) {
        // A hex of nothing but columns: every column drawn, side by side.
        this.drawConvoy(tokens, marks, list, x, y, color, alpha);
        continue;
      }
      if (tileMode && lead.owner === NEUTRAL_OWNER && this.siteArt !== null) {
        this.drawGarrisonBadge(marks, list, x, y, alpha);
        continue;
      }
      if (tileMode && units !== null && lead.posture !== "column" && lead.owner !== NEUTRAL_OWNER) {
        this.placeUnitSprite(units[lead.type], x, y, 1, lead.facing, color, alpha);
      } else {
        const r = tileMode ? HEX_SIZE * 0.42 : Math.max(HEX_SIZE * 0.6, 4 / scale);
        // Your own units carry a light rim on the overview, so they read apart from everyone else's.
        const rim =
          !tileMode && mine
            ? { color: IVORY, width: Math.max(r * 0.22, 1.5 / scale), alpha }
            : { color: DARK, width: r * 0.15, alpha };
        if (lead.posture === "column") {
          tokens.roundRect(x - r * 1.2, y - r * 0.7, r * 2.4, r * 1.4, r * 0.3).fill({ color, alpha }).stroke(rim);
        } else {
          tokens.circle(x, y, r).fill({ color, alpha }).stroke(rim);
        }
      }
      if (tileMode && columns > 0 && lead.posture !== "column") {
        // Columns sharing a deployed unit's hex: a pill with one pip each.
        const n = Math.min(columns, 6);
        const w = 4 + n * 3.5;
        const bx = x + HEX_SIZE * 0.5;
        const by = y - HEX_SIZE * 0.45;
        marks.roundRect(bx - w / 2, by - 4, w, 8, 4).fill({ color: DARK, alpha });
        for (let k = 0; k < n; k++) marks.circle(bx + (k - (n - 1) / 2) * 3.5, by, 1.2).fill({ color: 0xffffff, alpha });
      }
      if (tileMode && lead.posture !== "column" && lead.disordered) {
        drawDisorderBadge(marks, x - HEX_SIZE * 0.6, y - HEX_SIZE * 0.1, HEX_SIZE * 0.26, alpha);
      }
      if (tileMode && lead.hp < 10) {
        const w = HEX_SIZE * 1.1;
        marks.rect(x - w / 2, y + HEX_SIZE * 0.62, w, 4).fill({ color: DARK, alpha });
        marks.rect(x - w / 2, y + HEX_SIZE * 0.62, (w * lead.hp) / 10, 4).fill({ color: hpColor(lead.hp), alpha });
      }
    }
  }

  /**
   * A neutral garrison: a shield in the hex's lower-right corner with the number
   * of defenders on it, and their combined HP once they've taken damage, so an
   * attacker can see progress. The site's painted tile stays the main read.
   */
  private drawGarrisonBadge(marks: Graphics, list: readonly Unit[], x: number, y: number, alpha: number): void {
    const bx = x + HEX_SIZE * 0.44;
    const by = y + HEX_SIZE * 0.24;
    const w = HEX_SIZE * 0.36;
    const top = by - HEX_SIZE * 0.4;
    const shoulder = by + HEX_SIZE * 0.18;
    marks
      .poly([bx - w, top, bx + w, top, bx + w, shoulder, bx, by + HEX_SIZE * 0.52, bx - w, shoulder])
      .fill({ color: 0x2b2f36, alpha: 0.95 * alpha })
      .stroke({ color: 0xe8dcc0, width: 1.8, join: "round", alpha });
    const label = this.nextLabel();
    const count = String(list.length);
    if (label.text !== count) label.text = count;
    label.alpha = alpha;
    label.position.set(bx, by - HEX_SIZE * 0.17);
    const hp = list.reduce((sum, u) => sum + u.hp, 0);
    const max = list.length * 10;
    if (hp < max) {
      // Inside the shield, under the count: it never hangs over the next hex.
      const bw = w * 1.5;
      const bar = by + HEX_SIZE * 0.13;
      marks.rect(bx - bw / 2, bar, bw, 4).fill({ color: DARK, alpha });
      marks.rect(bx - bw / 2, bar, (bw * hp) / max, 4).fill({ color: hp > max / 2 ? 0x7bd389 : 0xe0a040, alpha });
    }
  }

  private nextSprite(): Sprite {
    let sprite = this.unitSprites[this.unitSpritesUsed];
    if (sprite === undefined) {
      sprite = new Sprite();
      sprite.anchor.set(0.5);
      this.unitSpriteLayer.addChild(sprite);
      this.unitSprites.push(sprite);
    }
    this.unitSpritesUsed += 1;
    sprite.visible = true;
    return sprite;
  }

  private nextLabel(): Text {
    let label = this.unitLabels[this.unitLabelsUsed];
    if (label === undefined) {
      label = new Text({
        text: "",
        style: { fontFamily: "Archivo Narrow, Arial Narrow, sans-serif", fontSize: 16, fontWeight: "700", fill: 0xf2e6c8 },
        // Sharp when zoomed in to the maximum.
        resolution: 3,
      });
      label.anchor.set(0.5);
      this.unitLabelLayer.addChild(label);
      this.unitLabels.push(label);
    }
    this.unitLabelsUsed += 1;
    label.visible = true;
    return label;
  }

  /** One unit's base + tinted mask, `scale` × the standard unit size. */
  private placeUnitSprite(
    pair: UnitSpritePair,
    x: number,
    y: number,
    scale: number,
    facing: Unit["facing"],
    color: number,
    alpha = 1,
  ): void {
    // The art faces west; a unit facing right is mirrored (as on the 2-player board).
    const s = (1.5 * HEX_SIZE * scale) / pair.base.width;
    const base = this.nextSprite();
    const mask = this.nextSprite();
    base.texture = pair.base;
    mask.texture = pair.mask;
    for (const sp of [base, mask]) {
      sp.scale.set(facing === "right" ? -s : s, s);
      sp.position.set(x, y);
      sp.alpha = alpha;
    }
    base.tint = 0xffffff;
    mask.tint = color;
  }

  /**
   * Up to six friendly columns in one hex, laid out side by side and shrinking as
   * the stack grows, so the whole stack reads at a glance (no count badge). A
   * type without column art falls back to a small token.
   */
  private drawConvoy(
    tokens: Graphics,
    marks: Graphics,
    list: readonly Unit[],
    x: number,
    y: number,
    color: number,
    alpha = 1,
  ): void {
    const stack = [...list].sort((a, b) => a.id - b.id).slice(0, CONVOY_SLOTS.length);
    const { scale, slots } = CONVOY_SLOTS[stack.length - 1];
    stack.forEach((unit, k) => {
      const cx = x + slots[k][0] * HEX_SIZE;
      const cy = y + slots[k][1] * HEX_SIZE;
      const pair = this.columnArt[unit.type];
      if (pair !== undefined) {
        this.placeUnitSprite(pair, cx, cy, scale, unit.facing, color, alpha);
      } else {
        const r = HEX_SIZE * 0.42 * scale;
        tokens
          .roundRect(cx - r * 1.2, cy - r * 0.7, r * 2.4, r * 1.4, r * 0.3)
          .fill({ color, alpha })
          .stroke({ color: DARK, width: r * 0.15, alpha });
      }
      if (unit.hp < 10) {
        const w = HEX_SIZE * 0.9 * scale;
        const by = cy + HEX_SIZE * 0.5 * scale;
        marks.rect(cx - w / 2, by, w, 3).fill({ color: DARK, alpha });
        marks.rect(cx - w / 2, by, (w * unit.hp) / 10, 3).fill({ color: hpColor(unit.hp), alpha });
      }
    });
  }

  // --- playback effects ---------------------------------------------------------

  private nextEffectLabel(): Text {
    let label = this.effectLabels[this.effectLabelsUsed];
    if (label === undefined) {
      label = new Text({
        text: "",
        style: {
          fontFamily: "Archivo Narrow, Arial Narrow, sans-serif",
          fontSize: 20,
          fontWeight: "700",
          fill: 0xffffff,
          stroke: { color: DARK, width: 4 },
        },
        resolution: 3,
      });
      label.anchor.set(0.5);
      this.effectLabelLayer.addChild(label);
      this.effectLabels.push(label);
    }
    this.effectLabelsUsed += 1;
    label.visible = true;
    return label;
  }

  /** The current playback frame's effects, in Graphics (no image assets). */
  private drawEffects(): void {
    const g = this.effectsLayer.clear();
    this.effectLabelsUsed = 0;
    const pb = this.playback;
    const viewer = this.scene?.viewer ?? "";
    if (pb !== null) {
      const px = (n: number) => n / this.world.scale.x;
      for (const e of pb.effects) this.drawEffect(g, e, viewer, px);
    }
    for (let i = this.effectLabelsUsed; i < this.effectLabels.length; i++) this.effectLabels[i].visible = false;
  }

  private drawEffect(g: Graphics, e: Effect, viewer: string, px: (n: number) => number): void {
    const centre = (a: { q: number; r: number }) => axialToPixel(a.q, a.r, HEX_SIZE);
    const p = e.progress;
    const fade = (u: number) => Math.min(1, (1 - u) * 3); // full until the last third, then out
    switch (e.kind) {
      case "combat": {
        const { x, y } = centre(e.at);
        const r = HEX_SIZE * (0.35 + 0.65 * p);
        const color = e.viewerInvolved ? 0xffb347 : 0xd9c7a0;
        g.circle(x, y, r).stroke({ color, width: px(3), alpha: fade(p) });
        starPoints(g, x, y, HEX_SIZE * (0.3 + 0.25 * Math.sin(Math.PI * p)), HEX_SIZE * 0.12, 8);
        g.fill({ color: 0xfff1c2, alpha: 0.9 * Math.sin(Math.PI * p) });
        break;
      }
      case "shell": {
        const to = centre(e.to);
        const from = e.from === null ? { x: to.x, y: to.y - HEX_SIZE * 6 } : centre(e.from);
        const lift = Math.max(HEX_SIZE * 1.2, Math.hypot(to.x - from.x, to.y - from.y) * 0.35);
        const at = (u: number) => ({
          x: from.x + (to.x - from.x) * u,
          y: from.y + (to.y - from.y) * u - (e.from === null ? 0 : 4 * u * (1 - u) * lift),
        });
        const start = at(0);
        g.moveTo(start.x, start.y);
        for (let i = 1; i <= 12; i++) {
          const q = at((p * i) / 12);
          g.lineTo(q.x, q.y);
        }
        g.stroke({ color: 0xf2e6c8, width: px(2), alpha: 0.55, cap: "round" });
        const head = at(p);
        g.circle(head.x, head.y, Math.max(px(4), HEX_SIZE * 0.12)).fill({ color: 0xffe08a }).stroke({ color: DARK, width: px(1.5) });
        break;
      }
      case "impact": {
        const { x, y } = centre(e.at);
        const r = HEX_SIZE * (0.3 + 0.8 * p);
        g.circle(x, y, r).fill({ color: ATTACK_COLOR, alpha: 0.35 * (1 - p) });
        g.circle(x, y, r).stroke({ color: 0xffd25a, width: px(3), alpha: 1 - p });
        break;
      }
      case "damage":
      case "heal": {
        const { x, y } = centre(e.at);
        const label = this.nextEffectLabel();
        const text = `${e.kind === "damage" ? "-" : "+"}${e.amount}`;
        if (label.text !== text) label.text = text;
        const fill = e.kind === "damage" ? 0xff6b5a : 0x7bd389;
        if (label.style.fill !== fill) label.style.fill = fill;
        label.alpha = fade(p);
        label.scale.set(px(1) * Math.max(1, this.world.scale.x) * 0.9);
        label.position.set(x + HEX_SIZE * 0.35, y - HEX_SIZE * (0.4 + 0.7 * p));
        break;
      }
      case "capture": {
        const { x, y } = centre(e.at);
        const color = ownerColor(e.owner, viewer);
        g.poly(hexCornerPoints(x, y, HEX_SIZE * (0.6 + 0.6 * p))).stroke({ color, width: px(4), alpha: fade(p) });
        g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.95)).stroke({ color, width: px(3), alpha: Math.min(1, p * 2) });
        break;
      }
      case "blocked": {
        const a = centre(e.from);
        const b = centre(e.toward);
        const x = (a.x + b.x) / 2;
        const y = (a.y + b.y) / 2;
        const r = HEX_SIZE * 0.22;
        const alpha = Math.sin(Math.PI * p);
        g.moveTo(x - r, y - r).lineTo(x + r, y + r).moveTo(x + r, y - r).lineTo(x - r, y + r);
        g.stroke({ color: ATTACK_COLOR, width: px(4), alpha, cap: "round" });
        break;
      }
      case "halt": {
        const { x, y } = centre(e.at);
        drawDisorderBadge(g, x, y - HEX_SIZE * 0.75, HEX_SIZE * (0.28 + 0.06 * Math.sin(Math.PI * 4 * p)), Math.sin(Math.PI * p));
        break;
      }
      case "recruit": {
        const { x, y } = centre(e.at);
        g.poly(hexCornerPoints(x, y, HEX_SIZE * (0.4 + 0.55 * p))).stroke({ color: 0x7bd389, width: px(3), alpha: fade(p) });
        break;
      }
    }
  }

  private drawOverlay(): void {
    const g = this.overlay.clear();
    const top = this.selection.clear();
    const o = this.overlayData;
    // Playback hides the plan and last turn's trails: the turn is replaying itself.
    if (o === null || this.playback !== null) return;
    const scale = this.world.scale.x;
    // Line widths in screen pixels, so marks stay readable at every zoom.
    const px = (n: number) => n / scale;
    const centre = (a: Axial) => axialToPixel(a.q, a.r, HEX_SIZE);

    if (o.lastTurn !== null) this.drawLastTurn(g, o.lastTurn, px);

    for (const hex of o.barrageRange) {
      const { x, y } = centre(hex);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.9)).fill({ color: ATTACK_COLOR, alpha: 0.2 });
    }
    for (const { hex, attack } of o.reach) {
      const { x, y } = centre(hex);
      if (attack) {
        g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.9))
          .fill({ color: ATTACK_COLOR, alpha: 0.28 })
          .stroke({ color: ATTACK_COLOR, width: px(2), alpha: 0.9 });
      } else {
        g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.9)).fill({ color: IVORY, alpha: 0.22 });
      }
    }
    // Quiet routes first, so the selected or hovered one is drawn over them.
    const routes = [...o.routes].sort((a, b) => Number(a.selected || a.highlighted) - Number(b.selected || b.highlighted));
    for (const route of routes) {
      if (route.path.length === 0) continue;
      const strong = route.selected || route.highlighted;
      const color =
        route.attack !== null ? ATTACK_COLOR : route.kind === "move" ? IVORY : route.kind === "standing" ? 0x9fb7d9 : 0x3d7eff;
      // Unselected routes are thin and dim, so overlapping ones can be told apart.
      const width = px(strong ? 4 : 2);
      const alpha = strong ? 0.95 : route.kind === "standing" ? 0.4 : 0.55;
      const pts = [route.from, ...route.path].map(centre);
      const line = () => {
        g.moveTo(pts[0].x, pts[0].y);
        for (const p of pts.slice(1)) g.lineTo(p.x, p.y);
      };
      line();
      g.stroke({ color: DARK, width: width + px(2), alpha: alpha * 0.6, cap: "round", join: "round" });
      line();
      g.stroke({ color, width, alpha, cap: "round", join: "round" });
      const end = pts[pts.length - 1];
      if (route.attack !== null) {
        // Up and left of centre: a garrison's shield sits in the lower right.
        const r = Math.max(px(11), HEX_SIZE * 0.4);
        drawAttackMark(g, end.x - HEX_SIZE * 0.32, end.y - HEX_SIZE * 0.2, r, px, strong ? 1 : 0.75);
      } else {
        g.circle(end.x, end.y, Math.max(px(strong ? 5 : 4), HEX_SIZE * (strong ? 0.22 : 0.16)))
          .fill({ color, alpha })
          .stroke({ color: DARK, width: px(1.5), alpha });
      }
    }
    for (const b of o.barrages) {
      const from = centre(b.from);
      const to = centre(b.target);
      g.moveTo(from.x, from.y).lineTo(to.x, to.y).stroke({ color: ATTACK_COLOR, width: px(2.5), alpha: 0.9 });
      g.poly(hexCornerPoints(to.x, to.y, HEX_SIZE * 0.85)).stroke({ color: ATTACK_COLOR, width: px(3) });
    }
    for (const hex of o.recruits) {
      const { x, y } = centre(hex);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 0.7)).stroke({ color: 0x7bd389, width: px(3) });
      g.moveTo(x - HEX_SIZE * 0.3, y).lineTo(x + HEX_SIZE * 0.3, y).moveTo(x, y - HEX_SIZE * 0.3).lineTo(x, y + HEX_SIZE * 0.3);
      g.stroke({ color: 0x7bd389, width: px(3) });
    }
    if (o.selectedHex !== null) {
      const { x, y } = centre(o.selectedHex);
      top.poly(hexCornerPoints(x, y, HEX_SIZE)).stroke({ color: IVORY, width: px(2), alpha: 0.9 });
    }
    if (o.selectedUnit !== null) {
      const { x, y } = centre(o.selectedUnit);
      top.circle(x, y, Math.max(HEX_SIZE * 0.85, px(10))).stroke({ color: 0xffd25a, width: px(3) });
    }
  }

  private drawLastTurn(g: Graphics, last: NonNullable<WorldOverlay["lastTurn"]>, px: (n: number) => number): void {
    const viewer = this.scene?.viewer ?? "";
    const centre = (a: Axial) => axialToPixel(a.q, a.r, HEX_SIZE);
    for (const move of last.moves) {
      const color = move.owner === null ? 0x9a9a9a : ownerColor(move.owner, viewer);
      const pts = move.path.map(centre);
      g.moveTo(pts[0].x, pts[0].y);
      for (const p of pts.slice(1)) g.lineTo(p.x, p.y);
      g.stroke({ color, width: px(2), alpha: 0.55, cap: "round", join: "round" });
      // Arrowhead on the last step, so direction reads without animation.
      const a = pts[pts.length - 2];
      const b = pts[pts.length - 1];
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      const len = Math.max(px(7), HEX_SIZE * 0.3);
      g.moveTo(b.x, b.y)
        .lineTo(b.x - len * Math.cos(ang - 0.5), b.y - len * Math.sin(ang - 0.5))
        .lineTo(b.x - len * Math.cos(ang + 0.5), b.y - len * Math.sin(ang + 0.5))
        .closePath()
        .fill({ color, alpha: 0.7 });
    }
    for (const at of last.barrages) {
      const { x, y } = centre(at);
      g.circle(x, y, HEX_SIZE * 0.55).stroke({ color: 0xe0503a, width: px(1.5), alpha: 0.7 });
    }
    for (const at of last.fights) {
      const { x, y } = centre(at);
      const r = Math.max(px(6), HEX_SIZE * 0.35);
      g.moveTo(x - r, y - r).lineTo(x + r, y + r).moveTo(x + r, y - r).lineTo(x - r, y + r);
      g.stroke({ color: 0xffb347, width: px(3), alpha: 0.9, cap: "round" });
    }
    for (const c of last.captures) {
      const { x, y } = centre(c.coord);
      g.poly(hexCornerPoints(x, y, HEX_SIZE * 1.05)).stroke({ color: ownerColor(c.owner, viewer), width: px(3.5) });
    }
  }

  // --- minimap ----------------------------------------------------------------------

  private buildMinimap(texture: Texture): void {
    const b = this.bounds;
    const s = MINIMAP_SIZE / Math.max(b.maxX - b.minX, b.maxY - b.minY);
    const w = (b.maxX - b.minX) * s;
    const h = (b.maxY - b.minY) * s;
    const backdrop = new Graphics().rect(-4, -4, w + 8, h + 8).fill({ color: 0x15171b, alpha: 0.85 });
    const img = new Sprite(texture);
    img.scale.set(this.overviewScale * s);
    // The viewport frame is clipped to the minimap: at close zoom near an edge it
    // would otherwise spill past the backdrop.
    this.minimapMask.rect(0, 0, w, h).fill(0xffffff);
    this.minimapFrame.mask = this.minimapMask;
    this.minimap.addChild(backdrop, img, this.minimapSites, this.minimapFrame, this.minimapMask);
    this.minimap.label = "minimap";
    this.minimapScale = s;
    this.positionMinimap();
  }

  private positionMinimap(): void {
    const h = (this.bounds.maxY - this.bounds.minY) * this.minimapScale;
    this.minimap.position.set(MINIMAP_MARGIN, this.app.screen.height - h - MINIMAP_MARGIN);
  }

  private minimapHit(x: number, y: number): boolean {
    const box = this.minimapBox;
    return x >= box.x && y >= box.y && x <= box.x + box.width && y <= box.y + box.height;
  }

  /** Centre the view on the world point under minimap point `(x, y)`. */
  private jumpFromMinimap(x: number, y: number, animate: boolean): void {
    this.userMovedCamera();
    const b = this.bounds;
    const s = this.minimapScale;
    const box = this.minimapBox;
    const wx = (clamp(x - box.x, 0, box.width)) / s + b.minX;
    const wy = (clamp(y - box.y, 0, box.height)) / s + b.minY;
    this.moveCamera(centredOn(wx, wy, this.world.scale.x, this.app.screen), animate);
  }

  private drawMinimapFrame(x0: number, y0: number, x1: number, y1: number): void {
    const b = this.bounds;
    const s = this.minimapScale;
    const w = (b.maxX - b.minX) * s;
    const h = (b.maxY - b.minY) * s;
    // Inset by half the stroke so the frame's edge stays on the minimap.
    const left = clamp((x0 - b.minX) * s, 0.75, w - 0.75);
    const top = clamp((y0 - b.minY) * s, 0.75, h - 0.75);
    const right = clamp((x1 - b.minX) * s, 0.75, w - 0.75);
    const bottom = clamp((y1 - b.minY) * s, 0.75, h - 0.75);
    this.minimapFrame.clear().rect(left, top, right - left, bottom - top).stroke({ color: IVORY, width: 1.5 });
  }

  /** Held settlements, plus your own units and base, which stand out from everyone else's. */
  private drawMinimapSites(): void {
    const g = this.minimapSites.clear();
    const scene = this.scene;
    if (scene === null) return;
    const b = this.bounds;
    const s = this.minimapScale;
    const at = (a: Axial) => {
      const { x, y } = axialToPixel(a.q, a.r, HEX_SIZE);
      return { x: (x - b.minX) * s, y: (y - b.minY) * s };
    };
    let base: Axial | null = null;
    for (const settlement of scene.state.settlements) {
      const owner = settlement.controlled_by ?? null;
      if (owner === null) continue;
      const p = at(settlement.coord);
      if (owner === scene.viewer && this.map.siteAt(settlement.coord.q, settlement.coord.r)?.kind === "base") {
        base = settlement.coord;
        continue;
      }
      g.circle(p.x, p.y, owner === scene.viewer ? 2.2 : 1.4).fill(ownerColor(owner, scene.viewer));
    }
    const viewer = ownerColor(scene.viewer, scene.viewer);
    for (const unit of scene.state.units) {
      if (unit.owner !== scene.viewer) continue;
      const p = at(unit.coord);
      g.circle(p.x, p.y, 1.8).fill(viewer).stroke({ color: 0xffffff, width: 0.8 });
    }
    if (base !== null) {
      const p = at(base);
      g.circle(p.x, p.y, 4.5).stroke({ color: 0xffffff, width: 1.5 });
      g.circle(p.x, p.y, 2.6).fill(viewer);
    }
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** An attack's end marker: a red burst with crossed strokes over it. */
function drawAttackMark(g: Graphics, x: number, y: number, r: number, px: (n: number) => number, alpha: number): void {
  const points: number[] = [];
  for (let i = 0; i < 16; i++) {
    const a = (Math.PI / 8) * i - Math.PI / 2;
    const rr = i % 2 === 0 ? r : r * 0.55;
    points.push(x + rr * Math.cos(a), y + rr * Math.sin(a));
  }
  g.poly(points).fill({ color: ATTACK_COLOR, alpha }).stroke({ color: DARK, width: px(1.5), alpha });
  const k = r * 0.42;
  g.moveTo(x - k, y - k).lineTo(x + k, y + k).moveTo(x + k, y - k).lineTo(x - k, y + k);
  g.stroke({ color: IVORY, width: Math.max(px(2.5), r * 0.16), alpha, cap: "round" });
}

/** The disordered marker: a small amber warning triangle with a "!". */
function drawDisorderBadge(g: Graphics, x: number, y: number, r: number, alpha = 1): void {
  g.poly([x, y - r, x + r * 0.95, y + r * 0.7, x - r * 0.95, y + r * 0.7])
    .fill({ color: DISORDER_COLOR, alpha })
    .stroke({ color: DARK, width: 1.4, join: "round", alpha });
  g.moveTo(x, y - r * 0.4).lineTo(x, y + r * 0.15).stroke({ color: DARK, width: 1.8, cap: "round", alpha });
  g.circle(x, y + r * 0.42, 1).fill({ color: DARK, alpha });
}

function hpColor(hp: number): number {
  return hp > 5 ? 0x7bd389 : 0xe0a040;
}

/** A star path (`points` tips) centred on `(x, y)`; the caller fills it. */
function starPoints(g: Graphics, x: number, y: number, outer: number, inner: number, points: number): void {
  const coords: number[] = [];
  for (let i = 0; i < points * 2; i++) {
    const a = (Math.PI / points) * i - Math.PI / 2;
    const r = i % 2 === 0 ? outer : inner;
    coords.push(x + r * Math.cos(a), y + r * Math.sin(a));
  }
  g.poly(coords);
}

/** The live scene's units in range, one group per hex. */
function liveGroups(units: readonly Unit[], range: CellRange): UnitGroup[] {
  const byHex = new Map<string, UnitGroup>();
  for (const u of units) {
    if (!inRange(u.coord, range)) continue;
    const key = hexKey(u.coord.q, u.coord.r);
    const group = byHex.get(key);
    if (group === undefined) {
      const { x, y } = axialToPixel(u.coord.q, u.coord.r, HEX_SIZE);
      byHex.set(key, { x, y, list: [u], alpha: 1 });
    } else group.list.push(u);
  }
  return [...byHex.values()];
}

/**
 * A playback frame's units in range: those standing on their hex grouped by hex
 * (stacks and convoys as usual), and each one in motion (sliding, lunging) on
 * its own at its fractional position, so a moving unit never regroups.
 */
function playbackGroups(units: readonly DrawnUnit[], range: CellRange): UnitGroup[] {
  const groups: UnitGroup[] = [];
  const byHex = new Map<string, UnitGroup>();
  for (const u of units) {
    if (!inRange(u.coord, range)) continue;
    const atRest = u.at.q === u.coord.q && u.at.r === u.coord.r;
    if (!atRest) {
      const { x, y } = axialToPixel(u.at.q, u.at.r, HEX_SIZE);
      groups.push({ x, y, list: [u], alpha: u.alpha });
      continue;
    }
    const key = hexKey(u.coord.q, u.coord.r);
    const group = byHex.get(key);
    if (group === undefined) {
      const { x, y } = axialToPixel(u.coord.q, u.coord.r, HEX_SIZE);
      const made = { x, y, list: [u as Unit], alpha: u.alpha };
      byHex.set(key, made);
      groups.push(made);
    } else {
      group.list.push(u);
      group.alpha = Math.max(group.alpha, u.alpha);
    }
  }
  return groups;
}

function inRange(a: Axial, range: CellRange): boolean {
  const row = a.r + (a.q - (a.q & 1)) / 2;
  return a.q >= range.col0 - 1 && a.q <= range.col1 + 1 && row >= range.row0 - 1 && row <= range.row1 + 1;
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
  for (let y = 0; y < canvas.height; y++) {
    for (let x = 0; x < canvas.width; x++) {
      const wx = x + b.minX;
      const wy = y + b.minY;
      const hex = pixelToAxial(wx, wy, OVERVIEW_HEX);
      const cell = map.hexAt(hex.q, hex.r);
      if (cell === undefined) continue;
      let color = overviewHexFill(map, hex.q, hex.r);
      if (cell.road && cell.terrain !== "river") {
        const c = axialToPixel(hex.q, hex.r, OVERVIEW_HEX);
        if (Math.abs(c.x - wx) < 0.9 && Math.abs(c.y - wy) < 0.9) color = ROAD_COLOR;
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
