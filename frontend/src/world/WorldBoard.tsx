// Hosts the Pixi canvas for a world and hands it to `WorldRenderer`.
//
// React mounts the application once per map and pushes scene updates; panning
// and zooming happen inside the renderer without re-rendering React. The live
// renderer is handed up (`onRenderer`) for the camera controls, hotkeys and hover.

import { Application } from "pixi.js";
import { useEffect, useRef, useState } from "react";

import { hexKey, type Axial } from "@/board/hex";
import { loadJoinTextures } from "@/board/joins";
import { loadTerrainTextures } from "@/board/tiles";
import { loadUnitTextures } from "@/board/units";

import { loadColumnTextures } from "./columnArt";
import { loadSiteTextures } from "./siteArt";
import { loadSiteTileTextures } from "./siteTiles";
import type { WorldView } from "./useWorld";
import { WorldRenderer, type WorldOverlay } from "./WorldRenderer";
import type { WorldMap } from "./worldMap";

export function WorldBoard({
  map,
  view,
  focus,
  overlay,
  panTo,
  onHexClick,
  onRenderer,
}: {
  map: WorldMap;
  view: WorldView;
  /** Where to centre the camera when the board first opens (e.g. the player's base). */
  focus: Axial | null;
  overlay: WorldOverlay;
  /**
   * Pan the camera here whenever the request changes (`seq` makes repeats
   * count). `reveal`: only if the hex is off-screen or under a panel.
   */
  panTo: { hex: Axial; seq: number; reveal?: boolean } | null;
  onHexClick: (hex: Axial, mods: { shift: boolean }) => void;
  onRenderer: (renderer: WorldRenderer | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [renderer, setRenderer] = useState<WorldRenderer | null>(null);
  const clickRef = useRef(onHexClick);
  useEffect(() => {
    clickRef.current = onHexClick;
  }, [onHexClick]);
  const focusRef = useRef(focus);
  const rendererRef = useRef(onRenderer);
  useEffect(() => {
    rendererRef.current = onRenderer;
  }, [onRenderer]);

  useEffect(() => {
    const el = host.current;
    if (el === null) return;
    let disposed = false;
    let made: WorldRenderer | null = null;
    const app = new Application();
    void app
      .init({ resizeTo: el, background: 0x1a1d22, antialias: true, autoDensity: true, resolution: window.devicePixelRatio })
      .then(() => {
        if (disposed) {
          app.destroy(true);
          return;
        }
        el.appendChild(app.canvas);
        made = new WorldRenderer(app, map, { onHexClick: (hex, mods) => clickRef.current(hex, mods) });
        if (focusRef.current !== null) made.centerOn(focusRef.current, undefined, false);
        setRenderer(made);
        rendererRef.current(made);
        void Promise.all([
          loadTerrainTextures(),
          loadJoinTextures(),
          loadUnitTextures(),
          loadSiteTextures(),
          loadColumnTextures(),
          // Painted site/base tiles: `WorldRenderer` looks them up by key, so they
          // must be in before `setTextures` turns tile mode on.
          loadSiteTileTextures(),
        ])
          .then(([tiles, joins, units, sites, columns]) => {
            if (!disposed) made?.setTextures(tiles, joins, units, sites, columns);
          })
          .catch((err: unknown) => console.error("world textures failed to load", err));
      });
    return () => {
      disposed = true;
      made?.destroy();
      setRenderer(null);
      rendererRef.current(null);
    };
  }, [map]);

  useEffect(() => {
    renderer?.setScene({
      state: view.state,
      viewer: view.your_slot,
      visible: new Set(view.visible.map((c) => hexKey(c.q, c.r))),
    });
  }, [renderer, view]);

  useEffect(() => {
    renderer?.setOverlay(overlay);
  }, [renderer, overlay]);

  useEffect(() => {
    if (panTo === null || renderer === null) return;
    if (panTo.reveal === true) renderer.reveal(panTo.hex);
    else renderer.panTo(panTo.hex);
  }, [renderer, panTo]);

  return <div ref={host} className="absolute inset-0 touch-none select-none" />;
}
