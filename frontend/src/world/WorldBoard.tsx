// Hosts the Pixi canvas for a world and hands it to `WorldRenderer`.
//
// React mounts the application once per map and pushes scene updates; panning
// and zooming happen inside the renderer without re-rendering React.

import { Application } from "pixi.js";
import { useEffect, useRef, useState } from "react";

import { hexKey, type Axial } from "@/board/hex";
import { loadTerrainTextures } from "@/board/tiles";
import { loadUnitTextures } from "@/board/units";

import type { WorldView } from "./useWorld";
import { WorldRenderer } from "./WorldRenderer";
import type { WorldMap } from "./worldMap";

export function WorldBoard({
  map,
  view,
  focus,
  onHexClick,
}: {
  map: WorldMap;
  view: WorldView;
  /** Where to centre the camera when the board first opens (e.g. the player's base). */
  focus: Axial | null;
  onHexClick: (hex: Axial) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [renderer, setRenderer] = useState<WorldRenderer | null>(null);
  const clickRef = useRef(onHexClick);
  useEffect(() => {
    clickRef.current = onHexClick;
  }, [onHexClick]);
  const focusRef = useRef(focus);

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
        made = new WorldRenderer(app, map, { onHexClick: (hex) => clickRef.current(hex) });
        if (focusRef.current !== null) made.centerOn(focusRef.current);
        setRenderer(made);
        void Promise.all([loadTerrainTextures(), loadUnitTextures()])
          .then(([tiles, units]) => {
            if (!disposed) made?.setTextures(tiles, units);
          })
          .catch((err: unknown) => console.error("world textures failed to load", err));
      });
    return () => {
      disposed = true;
      made?.destroy();
      setRenderer(null);
    };
  }, [map]);

  useEffect(() => {
    renderer?.setScene({
      state: view.state,
      viewer: view.your_slot,
      visible: new Set(view.visible.map((c) => hexKey(c.q, c.r))),
    });
  }, [renderer, view]);

  return <div ref={host} className="absolute inset-0 touch-none select-none" />;
}
