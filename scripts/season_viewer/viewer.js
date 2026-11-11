// Season viewer: plays back a recorded hex-general world season on a canvas map.
// The data is embedded in the page (#season-data) by scripts/season-viewer.py,
// either as plain JSON or as gzip + base64, and comes from app.world.season.
(function () {
  "use strict";

  // --- Constants mirrored from the game ---------------------------------------
  // frontend/src/board/palette.ts TERRAIN_FILL / ROAD_COLOR and
  // frontend/src/world/worldTiles.ts SITE_OVERVIEW_FILL (the overview colours).
  var TERRAIN_KEYS = ["plains", "woods", "hills", "river", "bridge", "lake", "town", "city"];
  var TERRAIN_BY_CHAR = { ".": 0, w: 1, h: 2, "~": 3, "=": 4, o: 5, t: 6, c: 7 };
  var TERRAIN_FILL = ["#68692e", "#3a4420", "#9c8a5e", "#41504c", "#877353", "#41504c", "#8f7150", "#5a4636"];
  var SITE_OVERVIEW_FILL = { farm: "#b39a4a", oil_field: "#26221c", steel_mill: "#8a4a2e", factory: "#6f6257" };
  var ROAD_COLOR = "#a78e62";
  var RIVER_LINE = "#6c8c90";
  var GARRISON_COLOR = "#4a4f57"; // frontend/src/world/colors.ts NEUTRAL
  var NEUTRAL_SITE = "#d9d5c8";
  var MARK_EDGE = "#1c1c19";
  var FIGHT_PVP = "#ff5b35";
  var FIGHT_GARRISON = "#ffd166";
  var SITE_NAMES = { base: "base", city: "city", farm: "farm", oil_field: "oil field", steel_mill: "steel mill", factory: "factory" };
  var CHUNK = 400; // shapes per canvas path before it is filled or stroked
  var TINT_RADIUS = 6; // hexes around an owned site that take its owner's tint
  var SQ3 = Math.sqrt(3);
  // Axial neighbour offsets (api/app/engine/hex_coords.py AXIAL_DIRECTIONS).
  var DIRS = [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]];
  var EV = { combat: 0, barrage: 1, capture: 2, destroyed: 3, disband: 4, eliminated: 5, recruit: 6, built: 7 };

  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // --- Small helpers -----------------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function median(xs) {
    if (!xs.length) return 0;
    var a = xs.slice().sort(function (x, y) { return x - y; });
    var m = a.length >> 1;
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }
  function hslHex(h, s, l) {
    var a = s * Math.min(l, 1 - l);
    function f(n) {
      var k = (n + h / 30) % 12;
      var c = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
      return Math.round(c * 255);
    }
    return [f(0), f(8), f(4)];
  }
  // frontend/src/world/colors.ts ownerColor: bot index k is treated as slot
  // number k + 1 (b000 = s001), stepped through the R2 sequence over hue and
  // lightness. Hues keep clear of the game's viewer blue (185-255 degrees) for
  // parity with the game, though this observer view has no viewer player.
  function ownerRgb(n) {
    var u = (0.5 + 0.7548776662466927 * n) % 1, v = (0.5 + 0.5698402909980532 * n) % 1;
    var hue = u * 290;
    if (hue >= 185) hue += 70;
    return hslHex(hue, 0.72, 0.34 + v * 0.3);
  }
  function rgbHex(c) { return "#" + ((1 << 24) | (c[0] << 16) | (c[1] << 8) | c[2]).toString(16).slice(1); }

  // --- Data ----------------------------------------------------------------------
  function loadRaw() {
    var el = $("season-data");
    var text = el.textContent.trim();
    if (el.getAttribute("data-encoding") === "json") return Promise.resolve(JSON.parse(text));
    if (typeof DecompressionStream === "undefined") {
      return Promise.reject(new Error("This browser cannot unpack the embedded season. Open it in a current Chrome, Edge, Firefox or Safari."));
    }
    var bin = atob(text);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).text().then(JSON.parse);
  }

  function decode(d) {
    if (d.v !== 1) throw new Error("Unknown season record version " + d.v + "; rebuild the viewer.");
    var W = d.map.w, H = d.map.h, N = W * H;
    var terr = new Uint8Array(N), road = new Uint8Array(N);
    for (var i = 0; i < N; i++) {
      terr[i] = TERRAIN_BY_CHAR[d.map.terrain[i]];
      road[i] = d.map.roads[i] === "#" ? 1 : 0;
    }
    // Neighbour table over the odd-q offset rectangle, -1 off the map.
    var nbr = new Int32Array(N * 6);
    for (var row = 0; row < H; row++) {
      for (var col = 0; col < W; col++) {
        var q = col, r = row - ((col - (col & 1)) >> 1);
        for (var dd = 0; dd < 6; dd++) {
          var nq = q + DIRS[dd][0], nr = r + DIRS[dd][1];
          var ncol = nq, nrow = nr + ((nq - (nq & 1)) >> 1);
          nbr[(row * W + col) * 6 + dd] = ncol >= 0 && ncol < W && nrow >= 0 && nrow < H ? nrow * W + ncol : -1;
        }
      }
    }
    var sf = d.map.sites, S = sf.length / 4;
    var siteHex = new Int32Array(S), siteKind = new Uint8Array(S), siteLevel = new Uint8Array(S), siteGarrison = new Uint8Array(S);
    var siteAt = new Int32Array(N).fill(-1);
    for (var s = 0; s < S; s++) {
      siteHex[s] = sf[4 * s]; siteKind[s] = sf[4 * s + 1]; siteLevel[s] = sf[4 * s + 2]; siteGarrison[s] = sf[4 * s + 3];
      siteAt[siteHex[s]] = s;
    }
    var P = d.players.length, F = d.player_fields.length;
    var fi = {};
    d.player_fields.forEach(function (n, k) { fi[n] = k; });
    var units = new Map();
    var holders = new Int16Array(S).fill(-1);
    var frames = d.frames.map(function (f) {
      for (var k = 0; k < f.x.length; k++) units.delete(f.x[k]);
      for (k = 0; k < f.u.length; k += 6) units.set(f.u[k], f.u.slice(k, k + 6));
      var arr = new Int32Array(units.size * 6), o = 0;
      units.forEach(function (u) { arr.set(u, o); o += 6; });
      for (k = 0; k < f.s.length; k += 2) holders[f.s[k]] = f.s[k + 1];
      var paths = new Map();
      for (k = 0; k < f.m.length;) {
        var uid = f.m[k], n = f.m[k + 1];
        paths.set(uid, f.m.slice(k + 2, k + 2 + n));
        k += 2 + n;
      }
      return { t: f.t, units: arr, holders: holders.slice(), p: f.p, e: f.e, paths: paths, alive: f.alive };
    });
    var palette = [], paletteRgb = [];
    for (var k = 0; k < P; k++) {
      var c = ownerRgb(k + 1);
      paletteRgb.push(c); palette.push(rgbHex(c));
    }
    return {
      raw: d, W: W, H: H, N: N, terr: terr, road: road, nbr: nbr,
      S: S, siteHex: siteHex, siteKind: siteKind, siteLevel: siteLevel, siteGarrison: siteGarrison, siteAt: siteAt,
      kinds: d.site_kinds, types: d.unit_types, buildings: d.buildings,
      P: P, F: F, fi: fi, players: d.players, bases: d.bases, eliminated: d.eliminated,
      frames: frames, T: frames.length - 1, palette: palette, paletteRgb: paletteRgb,
    };
  }

  // Derived per-turn series and the season headlines.
  function analyse(D) {
    var fightsPvp = [], fightsGarrison = [], medianUnits = [], alive = [], captures = [];
    var firstContact = null, biggest = null, totalPvp = 0, totalGarrison = 0;
    D.frames.forEach(function (f, t) {
      var pvp = 0, gar = 0, cap = 0;
      f.e.forEach(function (e) {
        if (e[0] === EV.combat) {
          var owners = new Set(e[2].map(function (p) { return p[1]; }));
          if (owners.has(0)) { gar++; return; }
          pvp++;
          if (firstContact === null) firstContact = { t: t, hex: e[1] };
        } else if (e[0] === EV.capture) cap++;
      });
      battles(D, f).forEach(function (b) {
        if (biggest === null || b.units > biggest.n) biggest = { t: t, hex: b.hex, n: b.units, fights: b.fights };
      });
      fightsPvp.push(pvp); fightsGarrison.push(gar); captures.push(cap);
      totalPvp += pvp; totalGarrison += gar;
      var live = [];
      for (var k = 0; k < D.P; k++) {
        if (D.eliminated[k] === 0 || D.eliminated[k] > f.t) live.push(f.p[k * D.F + D.fi.units]);
      }
      medianUnits.push(median(live));
      alive.push(f.alive);
    });
    var outs = D.eliminated.filter(function (x) { return x > 0; }).length;
    return {
      fightsPvp: fightsPvp, fightsGarrison: fightsGarrison, medianUnits: medianUnits, alive: alive, captures: captures,
      firstContact: firstContact, biggest: biggest, totalPvp: totalPvp, totalGarrison: totalGarrison, eliminations: outs,
      endMedian: medianUnits[medianUnits.length - 1],
    };
  }

  // A battle is a group of one turn's player-vs-player fights, joining fights
  // within BATTLE_REACH hexes of each other; its size is the distinct units in it.
  var BATTLE_REACH = 3;
  function hexDistance(D, a, b) {
    var ca = a % D.W, cb = b % D.W;
    var qa = ca, ra = Math.floor(a / D.W) - ((ca - (ca & 1)) >> 1);
    var qb = cb, rb = Math.floor(b / D.W) - ((cb - (cb & 1)) >> 1);
    var dq = qa - qb, dr = ra - rb;
    return (Math.abs(dq) + Math.abs(dr) + Math.abs(dq + dr)) / 2;
  }
  function battles(D, f) {
    var fights = f.e.filter(function (e) {
      return e[0] === EV.combat && !e[2].some(function (p) { return p[1] === 0; });
    });
    var parent = fights.map(function (_, k) { return k; });
    function find(k) { while (parent[k] !== k) { parent[k] = parent[parent[k]]; k = parent[k]; } return k; }
    for (var a = 0; a < fights.length; a++) {
      for (var b = a + 1; b < fights.length; b++) {
        if (hexDistance(D, fights[a][1], fights[b][1]) <= BATTLE_REACH) parent[find(a)] = find(b);
      }
    }
    var groups = new Map();
    fights.forEach(function (e, k) {
      var root = find(k);
      if (!groups.has(root)) groups.set(root, { hex: e[1], fights: 0, ids: new Set(), top: 0 });
      var g = groups.get(root);
      g.fights++;
      e[2].forEach(function (p) { g.ids.add(p[0]); });
      if (e[2].length > g.top) { g.top = e[2].length; g.hex = e[1]; }
    });
    return Array.from(groups.values()).map(function (g) { return { hex: g.hex, fights: g.fights, units: g.ids.size }; });
  }

  // --- Geometry ------------------------------------------------------------------
  function hexX(D, i) { return 1.5 * (i % D.W); }
  function hexY(D, i) { var col = i % D.W; return SQ3 * (Math.floor(i / D.W) + 0.5 * (col & 1)); }
  function hexAtWorld(D, x, y) {
    var q = (2 / 3) * x, r = (-1 / 3) * x + (SQ3 / 3) * y;
    var cx = q, cz = r, cy = -cx - cz;
    var rx = Math.round(cx), ry = Math.round(cy), rz = Math.round(cz);
    var dx = Math.abs(rx - cx), dy = Math.abs(ry - cy), dz = Math.abs(rz - cz);
    if (dx > dy && dx > dz) rx = -ry - rz; else if (dy <= dz) rz = -rx - ry;
    var col = rx, row = rz + ((rx - (rx & 1)) >> 1);
    if (col < 0 || col >= D.W || row < 0 || row >= D.H) return -1;
    return row * D.W + col;
  }
  function hexPath(ctx, x, y, rad) {
    ctx.moveTo(x + rad, y);
    for (var k = 1; k < 6; k++) {
      var a = (Math.PI / 3) * k;
      ctx.lineTo(x + rad * Math.cos(a), y + rad * Math.sin(a));
    }
    ctx.closePath();
  }
  function hexFill(D, i) {
    var s = D.siteAt[i];
    if (s >= 0) {
      var fill = SITE_OVERVIEW_FILL[D.kinds[D.siteKind[s]]];
      if (fill) return fill;
    }
    return TERRAIN_FILL[D.terr[i]];
  }

  // --- App state -----------------------------------------------------------------
  var D = null, A = null;
  var view = { turn: 0, playing: false, speed: 2, selected: -1, follow: false, sort: "hold", sortDir: -1, filter: "key" };
  var layers = { tint: true, garrison: true, events: true, trails: false };
  var cam = { s: 1, ox: 0, oy: 0, fit: 1 };
  var canvas, ctx, dpr = 1, cw = 0, ch = 0;
  var terrainImage = null, terrainScale = 4;
  var tintCache = new Map();
  var byHex = null; // current frame: hex -> list of unit row offsets
  var flash = { start: 0, hex: -1, hexStart: 0 };
  var tipPinned = false, tipHex = -1;
  var colors = {};

  function readColors() {
    ["--ink", "--muted", "--rule", "--panel", "--bg", "--accent", "--s1", "--s2", "--s3", "--grid", "--hot", "--map-ground"].forEach(function (n) { colors[n] = token(n); });
  }

  function ownerColor(code) { return code > 0 ? D.palette[code - 1] : GARRISON_COLOR; }
  function ownerName(code) { return code > 0 ? D.players[code - 1] : "garrison"; }
  function holderName(code) { return code > 0 ? D.players[code - 1] : code === 0 ? "neutral" : "vacant"; }
  function stat(t, k, field) { return D.frames[t].p[k * D.F + D.fi[field]]; }

  // --- Terrain pre-render ----------------------------------------------------------
  function bounds() {
    return { x0: -1, y0: -SQ3 / 2, x1: 1.5 * (D.W - 1) + 1, y1: SQ3 * (D.H - 0.5) + SQ3 / 2 };
  }
  function buildTerrain() {
    var b = bounds(), bw = b.x1 - b.x0, bh = b.y1 - b.y0;
    terrainScale = clamp(Math.floor(3000 / Math.max(bw, bh)), 2, 12);
    var c = document.createElement("canvas");
    c.width = Math.ceil(bw * terrainScale); c.height = Math.ceil(bh * terrainScale);
    var g = c.getContext("2d");
    g.setTransform(terrainScale, 0, 0, terrainScale, -b.x0 * terrainScale, -b.y0 * terrainScale);
    var groups = new Map();
    for (var i = 0; i < D.N; i++) {
      var f = hexFill(D, i);
      if (!groups.has(f)) groups.set(f, []);
      groups.get(f).push(i);
    }
    // Fill in small batches: one path with tens of thousands of subpaths is
    // very slow to rasterise.
    groups.forEach(function (list, fill) {
      g.fillStyle = fill; g.strokeStyle = fill; g.lineWidth = 0.08;
      for (var k0 = 0; k0 < list.length; k0 += CHUNK) {
        g.beginPath();
        for (var k = k0; k < Math.min(list.length, k0 + CHUNK); k++) hexPath(g, hexX(D, list[k]), hexY(D, list[k]), 1.02);
        g.fill(); g.stroke();
      }
    });
    g.globalAlpha = 0.42;
    drawCourses(g, 0, D.N - 1, 0.32, 0.24);
    g.globalAlpha = 1;
    terrainImage = c;
  }

  // Rivers (river/bridge/lake joins) and roads as lines between hex centres.
  function drawCourses(g, lo, hi, riverWidth, roadWidth, inView) {
    g.lineCap = "round"; g.lineJoin = "round";
    var river = new Path2D(), roads = new Path2D(), segments = 0;
    function flush() {
      g.strokeStyle = RIVER_LINE; g.lineWidth = riverWidth; g.stroke(river);
      g.strokeStyle = ROAD_COLOR; g.lineWidth = roadWidth; g.stroke(roads);
      river = new Path2D(); roads = new Path2D(); segments = 0;
    }
    for (var i = lo; i <= hi; i++) {
      if (inView && !inView(i)) continue;
      var t = D.terr[i], wet = t === 3 || t === 4;
      if (!wet && !D.road[i]) continue;
      var x = hexX(D, i), y = hexY(D, i);
      for (var dd = 0; dd < 6; dd++) {
        var j = D.nbr[i * 6 + dd];
        if (j < 0) continue;
        var tj = D.terr[j];
        if (wet && (tj === 3 || tj === 4 || tj === 5) && (j > i || tj === 5)) {
          river.moveTo(x, y); river.lineTo(hexX(D, j), hexY(D, j)); segments++;
        }
        if (D.road[i] && D.road[j] && j > i) {
          roads.moveTo(x, y); roads.lineTo(hexX(D, j), hexY(D, j)); segments++;
        }
      }
      if (segments > CHUNK) flush();
    }
    flush();
  }

  // --- Territory tint ----------------------------------------------------------------
  function tintFor(t) {
    if (tintCache.has(t)) {
      var hit = tintCache.get(t); tintCache.delete(t); tintCache.set(t, hit); return hit;
    }
    var holders = D.frames[t].holders, N = D.N;
    var own = new Int16Array(N), dist = new Uint8Array(N).fill(255), queue = new Int32Array(N), head = 0, tail = 0;
    for (var s = 0; s < D.S; s++) {
      var h = holders[s];
      if (h > 0) { var i0 = D.siteHex[s]; own[i0] = h; dist[i0] = 0; queue[tail++] = i0; }
    }
    while (head < tail) {
      var i = queue[head++];
      if (dist[i] >= TINT_RADIUS) continue;
      for (var dd = 0; dd < 6; dd++) {
        var j = D.nbr[i * 6 + dd];
        if (j >= 0 && dist[j] === 255) { dist[j] = dist[i] + 1; own[j] = own[i]; queue[tail++] = j; }
      }
    }
    var c = document.createElement("canvas");
    c.width = D.W; c.height = 2 * D.H + 1;
    var g = c.getContext("2d");
    var img = g.createImageData(c.width, c.height), px = img.data;
    for (i = 0; i < N; i++) {
      var o = own[i];
      if (!o) continue;
      var front = false, edge = false;
      for (dd = 0; dd < 6; dd++) {
        var n = D.nbr[i * 6 + dd];
        if (n < 0) continue;
        if (own[n] !== o) { if (own[n] > 0) front = true; else edge = true; }
      }
      var alpha = front ? 205 : edge ? 130 : 78;
      var col = i % D.W, row = Math.floor(i / D.W), y0 = 2 * row + (col & 1), rgb = D.paletteRgb[o - 1];
      for (var k = 0; k < 2; k++) {
        var p = ((y0 + k) * D.W + col) * 4;
        px[p] = rgb[0]; px[p + 1] = rgb[1]; px[p + 2] = rgb[2]; px[p + 3] = alpha;
      }
    }
    g.putImageData(img, 0, 0);
    tintCache.set(t, c);
    if (tintCache.size > 16) tintCache.delete(tintCache.keys().next().value);
    return c;
  }

  // --- Camera ----------------------------------------------------------------------------
  function resize() {
    var rect = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    var first = cw === 0;
    var ocx = cw / 2, ocy = ch / 2;
    cw = Math.max(1, rect.width); ch = Math.max(1, rect.height);
    canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr);
    if (!D) return;
    computeFit();
    if (first) fitView(); else { cam.ox += cw / 2 - ocx; cam.oy += ch / 2 - ocy; }
    requestDraw();
  }
  function computeFit() {
    var b = bounds();
    cam.fit = Math.min(cw / (b.x1 - b.x0), ch / (b.y1 - b.y0)) * 0.97;
  }
  function fitView() {
    var b = bounds();
    cam.s = cam.fit;
    cam.ox = cw / 2 - ((b.x0 + b.x1) / 2) * cam.s;
    cam.oy = ch / 2 - ((b.y0 + b.y1) / 2) * cam.s;
    requestDraw();
  }
  function zoomAt(factor, sx, sy) {
    var s = clamp(cam.s * factor, cam.fit * 0.6, 70);
    var wx = (sx - cam.ox) / cam.s, wy = (sy - cam.oy) / cam.s;
    cam.s = s; cam.ox = sx - wx * s; cam.oy = sy - wy * s;
    requestDraw();
  }
  function centreOn(hex, minScale) {
    if (minScale && cam.s < minScale) cam.s = minScale;
    cam.ox = cw / 2 - hexX(D, hex) * cam.s;
    cam.oy = ch / 2 - hexY(D, hex) * cam.s;
    requestDraw();
  }
  function toWorld(sx, sy) { return [(sx - cam.ox) / cam.s, (sy - cam.oy) / cam.s]; }

  // --- Drawing ---------------------------------------------------------------------------------
  var drawQueued = false;
  function requestDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(function (now) { drawQueued = false; draw(now); });
  }

  function visibleRange() {
    var a = toWorld(0, 0), b = toWorld(cw, ch);
    return {
      c0: clamp(Math.floor((a[0] - 1) / 1.5), 0, D.W - 1), c1: clamp(Math.ceil((b[0] + 1) / 1.5), 0, D.W - 1),
      r0: clamp(Math.floor(a[1] / SQ3 - 1), 0, D.H - 1), r1: clamp(Math.ceil(b[1] / SQ3 + 1), 0, D.H - 1),
    };
  }

  function draw(now) {
    if (!D) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = colors["--map-ground"];
    ctx.fillRect(0, 0, cw, ch);
    var s = cam.s, f = D.frames[view.turn];
    var b = bounds();
    var vector = s > terrainScale * 1.6;
    var vr = visibleRange();

    ctx.save();
    ctx.translate(cam.ox, cam.oy); ctx.scale(s, s);
    if (vector) drawTerrainVector(vr);
    else {
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(terrainImage, b.x0, b.y0, terrainImage.width / terrainScale, terrainImage.height / terrainScale);
    }
    if (layers.tint) {
      ctx.imageSmoothingEnabled = true;
      ctx.globalAlpha = 0.62;
      ctx.drawImage(tintFor(view.turn), -0.75, -SQ3 / 2, D.W * 1.5, (2 * D.H + 1) * SQ3 / 2);
      ctx.globalAlpha = 1;
    }
    if (vector) {
      var inView = function (i) { var c = i % D.W, r = (i / D.W) | 0; return c >= vr.c0 && c <= vr.c1 && r >= vr.r0 && r <= vr.r1; };
      drawCourses(ctx, vr.r0 * D.W, vr.r1 * D.W + D.W - 1, 0.42, Math.max(0.16, 1.4 / s), inView);
    }
    if (layers.trails && view.turn > 0) drawTrails(f);
    ctx.restore();

    var zoomedIn = s >= 7;
    if (zoomedIn) { drawSites(f, vr); drawUnits(f, vr, true); }
    else { drawUnits(f, vr, false); drawSites(f, vr); }
    if (layers.events) drawEvents(f, now || performance.now());
    drawSelection(f);
    drawFlashHex(now || performance.now());
  }

  function drawTerrainVector(vr) {
    var groups = new Map();
    for (var r = vr.r0; r <= vr.r1; r++) {
      for (var c = vr.c0; c <= vr.c1; c++) {
        var i = r * D.W + c, fill = hexFill(D, i);
        if (!groups.has(fill)) groups.set(fill, []);
        groups.get(fill).push(i);
      }
    }
    ctx.lineWidth = 0.05;
    groups.forEach(function (list, fill) {
      ctx.fillStyle = fill; ctx.strokeStyle = cam.s > 16 ? "rgba(0,0,0,0.22)" : fill;
      for (var k0 = 0; k0 < list.length; k0 += CHUNK) {
        ctx.beginPath();
        for (var k = k0; k < Math.min(list.length, k0 + CHUNK); k++) hexPath(ctx, hexX(D, list[k]), hexY(D, list[k]), 1.01);
        ctx.fill(); ctx.stroke();
      }
    });
  }

  function inRange(vr, hex, pad) {
    var c = hex % D.W, r = (hex / D.W) | 0;
    return c >= vr.c0 - pad && c <= vr.c1 + pad && r >= vr.r0 - pad && r <= vr.r1 + pad;
  }
  function sx(hex) { return cam.ox + hexX(D, hex) * cam.s; }
  function sy(hex) { return cam.oy + hexY(D, hex) * cam.s; }

  // A site marker in screen space: shape by kind, fill by holder.
  function siteGlyph(g, kind, x, y, rad, fill, stroke, hollow) {
    g.beginPath();
    if (kind === "base") g.rect(x - rad, y - rad, 2 * rad, 2 * rad);
    else if (kind === "city") g.arc(x, y, rad, 0, Math.PI * 2);
    else if (kind === "oil_field") { g.moveTo(x, y - rad); g.lineTo(x + rad, y); g.lineTo(x, y + rad); g.lineTo(x - rad, y); g.closePath(); }
    else if (kind === "steel_mill") { g.moveTo(x, y - rad); g.lineTo(x + rad, y + rad * 0.8); g.lineTo(x - rad, y + rad * 0.8); g.closePath(); }
    else if (kind === "factory") g.rect(x - rad * 0.85, y - rad * 0.85, 1.7 * rad, 1.7 * rad);
    else g.arc(x, y, rad * 0.85, 0, Math.PI * 2);
    if (!hollow) { g.fillStyle = fill; g.fill(); }
    g.lineWidth = rad > 4 ? 1.4 : 1; g.strokeStyle = stroke; g.stroke();
    if (kind === "base" && !hollow && rad > 3) {
      g.beginPath(); g.arc(x, y, rad * 0.38, 0, Math.PI * 2); g.fillStyle = stroke; g.fill();
    }
  }
  var KIND_SCALE = { base: 1.35, city: 1.15, factory: 1.0, oil_field: 1.0, steel_mill: 1.0, farm: 0.85 };
  function siteRadius(kind) { return clamp(0.5 * cam.s, 1.8, 9) * KIND_SCALE[kind]; }

  function drawSites(f, vr) {
    for (var s = 0; s < D.S; s++) {
      var hex = D.siteHex[s];
      if (!inRange(vr, hex, 1)) continue;
      var kind = D.kinds[D.siteKind[s]], h = f.holders[s];
      var rad = siteRadius(kind);
      if (h === -1) { siteGlyph(ctx, kind, sx(hex), sy(hex), rad, null, "rgba(240,236,224,0.7)", true); continue; }
      if (h === 0 && cam.s < 4) {
        // Unheld sites stay quiet at overview zoom so held ones stand out.
        ctx.globalAlpha = 0.55;
        siteGlyph(ctx, kind, sx(hex), sy(hex), rad * 0.75, NEUTRAL_SITE, MARK_EDGE, false);
        ctx.globalAlpha = 1;
        continue;
      }
      var fill = h > 0 ? D.palette[h - 1] : NEUTRAL_SITE;
      siteGlyph(ctx, kind, sx(hex), sy(hex), rad, fill, MARK_EDGE, false);
    }
  }

  function unitGlyph(g, x, y, rad, fill, column, garrison) {
    g.beginPath();
    if (column) { g.moveTo(x, y - rad * 1.2); g.lineTo(x + rad * 1.2, y); g.lineTo(x, y + rad * 1.2); g.lineTo(x - rad * 1.2, y); g.closePath(); }
    else g.arc(x, y, rad, 0, Math.PI * 2);
    g.fillStyle = fill; g.fill();
    if (rad >= 2) {
      g.lineWidth = column ? 1.2 : 1;
      g.strokeStyle = column ? "rgba(255,255,255,0.85)" : garrison ? "rgba(0,0,0,0.5)" : MARK_EDGE;
      g.stroke();
    }
  }

  function drawUnits(f, vr, spread) {
    var u = f.units, rad = clamp(0.3 * cam.s, 1.2, 6.5);
    var sel = view.selected;
    byHex.forEach(function (rows, hex) {
      if (!inRange(vr, hex, 1)) return;
      var cx = sx(hex), cy = sy(hex);
      var shown = rows;
      if (!layers.garrison) shown = rows.filter(function (o) { return u[o + 1] !== 0; });
      if (!shown.length) return;
      var n = shown.length, onSite = D.siteAt[hex] >= 0;
      for (var k = 0; k < n; k++) {
        var o = shown[k], owner = u[o + 1], column = (u[o + 4] & 1) === 1, gar = owner === 0;
        var x = cx, y = cy;
        if (spread && (n > 1 || onSite)) {
          var ring = onSite ? 0.5 : 0.36, a = (n === 1 ? 0.6 : (k / n) * Math.PI * 2) + 0.4;
          x += Math.cos(a) * ring * cam.s; y += Math.sin(a) * ring * cam.s;
        }
        var r = gar ? rad * 0.72 : rad;
        if (sel >= 0 && owner === sel + 1) {
          ctx.beginPath(); ctx.arc(x, y, r + 2.2, 0, Math.PI * 2); ctx.fillStyle = "#ffffff"; ctx.fill();
        }
        unitGlyph(ctx, x, y, r, gar ? GARRISON_COLOR : D.palette[owner - 1], column, gar);
      }
      if (spread && n > 3 && cam.s >= 14) {
        ctx.font = "600 " + Math.round(clamp(cam.s * 0.28, 9, 13)) + "px " + token("--font-num");
        ctx.fillStyle = "#ffffff"; ctx.strokeStyle = "rgba(0,0,0,0.8)"; ctx.lineWidth = 3;
        ctx.strokeText(String(n), cx + 0.5 * cam.s, cy - 0.45 * cam.s); ctx.fillText(String(n), cx + 0.5 * cam.s, cy - 0.45 * cam.s);
      }
    });
  }

  function drawTrails(f) {
    var prev = D.frames[view.turn - 1], start = new Map();
    for (var o = 0; o < prev.units.length; o += 6) start.set(prev.units[o], [prev.units[o + 5], prev.units[o + 1]]);
    for (o = 0; o < f.units.length; o += 6) if (!start.has(f.units[o])) start.set(f.units[o], [-1, f.units[o + 1]]);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(0.12, 1.6 / cam.s);
    f.paths.forEach(function (steps, uid) {
      var from = start.get(uid);
      if (!from || from[0] < 0) return;
      if (view.selected >= 0 && from[1] !== view.selected + 1) return;
      ctx.beginPath();
      ctx.moveTo(hexX(D, from[0]), hexY(D, from[0]));
      for (var k = 0; k < steps.length; k++) ctx.lineTo(hexX(D, steps[k]), hexY(D, steps[k]));
      ctx.strokeStyle = from[1] > 0 ? D.palette[from[1] - 1] : GARRISON_COLOR;
      ctx.globalAlpha = 0.8;
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }

  function flashPhase(now) {
    if (reducedMotion.matches || !view.playing && now - flash.start > 900) return 1;
    return clamp((now - flash.start) / 650, 0, 1);
  }

  function drawEvents(f, now) {
    var phase = flashPhase(now), base = clamp(0.5 * cam.s, 2.5, 10);
    var e, k;
    ctx.lineCap = "round";
    // Barrages: a dashed line from the gun to its target.
    for (k = 0; k < f.e.length; k++) {
      e = f.e[k];
      if (e[0] !== EV.barrage) continue;
      ctx.beginPath(); ctx.setLineDash([3, 3]);
      ctx.moveTo(sx(e[4]), sy(e[4])); ctx.lineTo(sx(e[1]), sy(e[1]));
      ctx.strokeStyle = "rgba(255,240,200,0.75)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(sx(e[1]), sy(e[1]), Math.max(1.5, base * 0.35), 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,240,200,0.9)"; ctx.fill();
    }
    // Fights: a disc sized by the units involved.
    for (k = 0; k < f.e.length; k++) {
      e = f.e[k];
      if (e[0] !== EV.combat) continue;
      var garrison = e[2].some(function (p) { return p[1] === 0; });
      var rad = base * (0.7 + 0.45 * Math.sqrt(e[2].length));
      ctx.beginPath(); ctx.arc(sx(e[1]), sy(e[1]), rad, 0, Math.PI * 2);
      ctx.fillStyle = garrison ? "rgba(255,209,102,0.28)" : "rgba(255,91,53,0.34)"; ctx.fill();
      ctx.lineWidth = garrison ? 1.2 : 2; ctx.strokeStyle = garrison ? FIGHT_GARRISON : FIGHT_PVP; ctx.stroke();
    }
    // Units destroyed: a small cross.
    for (k = 0; k < f.e.length; k++) {
      e = f.e[k];
      if (e[0] !== EV.destroyed && e[0] !== EV.disband) continue;
      var x = sx(e[1]), y = sy(e[1]), c = Math.max(2, base * 0.45);
      ctx.beginPath(); ctx.moveTo(x - c, y - c); ctx.lineTo(x + c, y + c); ctx.moveTo(x + c, y - c); ctx.lineTo(x - c, y + c);
      ctx.lineWidth = 2.6; ctx.strokeStyle = "rgba(0,0,0,0.8)"; ctx.stroke();
      ctx.lineWidth = 1.2; ctx.strokeStyle = e[0] === EV.disband ? "#c9c3b2" : "#ffffff"; ctx.stroke();
    }
    // Captures: a ring in the captor's colour that opens out as the turn lands.
    for (k = 0; k < f.e.length; k++) {
      e = f.e[k];
      if (e[0] !== EV.capture) continue;
      var r0 = base * 1.3, r1 = base * (1.3 + 1.6 * (1 - phase) + 0.5);
      ctx.beginPath(); ctx.arc(sx(e[1]), sy(e[1]), phase < 1 ? r1 : r0 + base * 0.4, 0, Math.PI * 2);
      ctx.lineWidth = 3.5; ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.stroke();
      ctx.lineWidth = 2; ctx.strokeStyle = D.palette[e[3] - 1]; ctx.stroke();
    }
    // Eliminations: a large cross over the fallen player's base.
    for (k = 0; k < f.e.length; k++) {
      e = f.e[k];
      if (e[0] !== EV.eliminated) continue;
      var ex = sx(e[1]), ey = sy(e[1]), size = Math.max(9, base * 2.2);
      ctx.beginPath(); ctx.moveTo(ex - size, ey - size); ctx.lineTo(ex + size, ey + size); ctx.moveTo(ex + size, ey - size); ctx.lineTo(ex - size, ey + size);
      ctx.lineWidth = 6; ctx.strokeStyle = "rgba(0,0,0,0.85)"; ctx.stroke();
      ctx.lineWidth = 3; ctx.strokeStyle = D.palette[e[2] - 1]; ctx.stroke();
    }
  }

  function drawSelection(f) {
    if (view.selected < 0) return;
    var base = D.bases[view.selected], rad = Math.max(8, cam.s * 1.4);
    ctx.beginPath(); ctx.arc(sx(base), sy(base), rad, 0, Math.PI * 2);
    ctx.lineWidth = 4; ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.stroke();
    ctx.lineWidth = 2; ctx.strokeStyle = "#ffffff"; ctx.stroke();
    for (var s = 0; s < D.S; s++) {
      if (f.holders[s] !== view.selected + 1 || D.siteHex[s] === base) continue;
      var hex = D.siteHex[s];
      ctx.beginPath(); ctx.arc(sx(hex), sy(hex), siteRadius(D.kinds[D.siteKind[s]]) + 2.5, 0, Math.PI * 2);
      ctx.lineWidth = 1.5; ctx.strokeStyle = "#ffffff"; ctx.stroke();
    }
  }

  function drawFlashHex(now) {
    if (flash.hex < 0) return;
    var age = now - flash.hexStart;
    if (age > 1600) { flash.hex = -1; return; }
    var phase = reducedMotion.matches ? 0.5 : age / 1600;
    var rad = Math.max(10, cam.s * 1.2) * (1 + phase);
    ctx.beginPath(); ctx.arc(sx(flash.hex), sy(flash.hex), rad, 0, Math.PI * 2);
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(255,255,255," + (1 - phase).toFixed(2) + ")"; ctx.stroke();
    requestDraw();
  }

  // --- Turn changes -------------------------------------------------------------------------------
  function indexUnits(f) {
    byHex = new Map();
    for (var o = 0; o < f.units.length; o += 6) {
      var h = f.units[o + 5];
      var list = byHex.get(h);
      if (list) list.push(o); else byHex.set(h, [o]);
    }
  }

  function setTurn(t, fromScrub) {
    t = clamp(t, 0, D.T);
    var changed = t !== view.turn || byHex === null;
    view.turn = t;
    indexUnits(D.frames[t]);
    if (!fromScrub) $("sv-turn").value = String(t);
    $("sv-turnlabel").innerHTML = "Turn <b>" + t + "</b> / " + D.T;
    if (changed) flash.start = performance.now();
    if (view.follow && view.selected >= 0) followSelected();
    renderTable();
    renderEvents();
    renderCharts();
    drawHist();
    if (tipHex >= 0 && !tipHidden()) showTip(tipHex, lastTip.x, lastTip.y);
    requestDraw();
    if (changed && !reducedMotion.matches) animateFlash();
  }
  function animateFlash() {
    var until = flash.start + 700;
    (function step(now) {
      requestDraw();
      if (now < until) requestAnimationFrame(step);
    })(performance.now());
  }

  function followSelected() {
    var f = D.frames[view.turn], sumX = 0, sumY = 0, n = 0, code = view.selected + 1;
    for (var o = 0; o < f.units.length; o += 6) {
      if (f.units[o + 1] !== code) continue;
      sumX += hexX(D, f.units[o + 5]); sumY += hexY(D, f.units[o + 5]); n++;
    }
    if (n === 0) { centreOn(D.bases[view.selected]); return; }
    cam.ox = cw / 2 - (sumX / n) * cam.s; cam.oy = ch / 2 - (sumY / n) * cam.s;
  }

  // --- Playback ----------------------------------------------------------------------------------------
  var lastTick = 0;
  function setPlaying(on) {
    view.playing = on && D.T > 0;
    var btn = $("sv-play");
    btn.textContent = view.playing ? "Pause" : "Play";
    btn.setAttribute("aria-pressed", view.playing ? "true" : "false");
    if (view.playing) {
      if (view.turn >= D.T) setTurn(0);
      lastTick = performance.now();
      requestAnimationFrame(tick);
    }
  }
  function tick(now) {
    if (!view.playing) return;
    if (now - lastTick >= 1000 / view.speed) {
      lastTick = now;
      if (view.turn >= D.T) { setPlaying(false); return; }
      setTurn(view.turn + 1);
    }
    requestAnimationFrame(tick);
  }

  // --- Timeline histogram ----------------------------------------------------------------------------------
  function drawHist() {
    var c = $("sv-hist"), rect = c.getBoundingClientRect();
    if (rect.width < 2) return;
    c.width = Math.round(rect.width * dpr); c.height = Math.round(rect.height * dpr);
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var w = rect.width, h = rect.height, pad = 8;
    g.clearRect(0, 0, w, h);
    var max = 1;
    for (var t = 0; t <= D.T; t++) max = Math.max(max, A.fightsPvp[t] + A.fightsGarrison[t]);
    var step = (w - 2 * pad) / Math.max(1, D.T), bw = Math.max(1, Math.min(step * 0.8, 12));
    g.fillStyle = colors["--grid"]; g.fillRect(pad, h - 1, w - 2 * pad, 1);
    for (t = 0; t <= D.T; t++) {
      var x = pad + t * step - bw / 2;
      var hp = (A.fightsPvp[t] / max) * (h - 4), hg = (A.fightsGarrison[t] / max) * (h - 4);
      g.fillStyle = colors["--muted"]; g.globalAlpha = 0.45; g.fillRect(x, h - 1 - hg, bw, hg);
      g.globalAlpha = 1; g.fillStyle = colors["--hot"]; g.fillRect(x, h - 1 - hg - hp, bw, hp);
      if (A.captures[t] > 0) { g.fillStyle = colors["--accent"]; g.fillRect(x, 0, bw, 2); }
    }
    var cx = pad + view.turn * step;
    g.fillStyle = colors["--ink"]; g.fillRect(cx - 1, 0, 2, h);
  }
  function histTurnAt(clientX) {
    var rect = $("sv-hist").getBoundingClientRect(), pad = 8;
    return Math.round(((clientX - rect.left - pad) / Math.max(1, rect.width - 2 * pad)) * D.T);
  }

  // --- Side panels ----------------------------------------------------------------------------------------------
  function sortValue(k, key) {
    var t = view.turn;
    if (key === "name") return D.players[k];
    if (key === "units") return stat(t, k, "units");
    if (key === "hold") return stat(t, k, "holdings");
    if (key === "res") return stat(t, k, "manpower") + stat(t, k, "fuel") + stat(t, k, "steel");
    if (key === "cap") return stat(t, k, "cap");
    var out = D.eliminated[k];
    return out > 0 && out <= t ? out : 1e9;
  }
  function renderTable() {
    var order = [];
    for (var k = 0; k < D.P; k++) order.push(k);
    var key = view.sort, dir = view.sortDir; // -1 descending, 1 ascending
    order.sort(function (a, b) {
      var va = sortValue(a, key), vb = sortValue(b, key);
      if (va < vb) return -dir; if (va > vb) return dir;
      return a - b;
    });
    var t = view.turn, html = [];
    order.forEach(function (k) {
      var out = D.eliminated[k] > 0 && D.eliminated[k] <= t;
      html.push(
        '<tr data-k="' + k + '"' + (out ? ' class="out"' : "") + ' aria-selected="' + (view.selected === k) + '" tabindex="0">' +
        '<td><span class="sv-sw" style="background:' + D.palette[k] + '"></span></td>' +
        "<td>" + esc(D.players[k]) + "</td>" +
        "<td>" + stat(t, k, "units") + "</td>" +
        "<td>" + stat(t, k, "holdings") + "</td>" +
        "<td>" + (stat(t, k, "manpower") + stat(t, k, "fuel") + stat(t, k, "steel")) + "</td>" +
        "<td>" + stat(t, k, "cap") + "</td>" +
        "<td>" + (out ? '<span class="sv-pill">T' + D.eliminated[k] + "</span>" : "") + "</td></tr>"
      );
    });
    $("sv-players").tBodies[0].innerHTML = html.join("");
    document.querySelectorAll(".sv-players th button").forEach(function (b) {
      var active = b.getAttribute("data-sort") === view.sort;
      b.setAttribute("aria-sort", active ? (view.sortDir < 0 ? "descending" : "ascending") : "none");
    });
    $("sv-alive").textContent = D.frames[t].alive + " of " + D.P + " in";
  }

  function selectPlayer(k) {
    view.selected = view.selected === k ? -1 : k;
    if (view.selected >= 0) {
      if (view.follow) followSelected(); else centreOn(D.bases[k], Math.max(cam.s, cam.fit * 4));
    }
    renderTable(); renderCharts(); requestDraw();
  }

  function strip(label, values, color, opts) {
    opts = opts || {};
    var wrap = document.createElement("div");
    wrap.className = "sv-strip";
    var now = values[view.turn];
    wrap.innerHTML = '<div class="sv-striphead"><span class="sv-label">' + esc(label) + "</span><span>" +
      (Math.round(now * 10) / 10) + (opts.suffix || "") + "</span></div>";
    var c = document.createElement("canvas");
    wrap.appendChild(c);
    c.setAttribute("role", "img");
    c.setAttribute("aria-label", label + " by turn, now " + now);
    var entry = { c: c, values: values, color: color, opts: opts };
    strips.push(entry);
    requestAnimationFrame(function () { paintStrip(entry.c, entry.values, entry.color, entry.opts); });
    return wrap;
  }
  var strips = [];
  function repaintStrips() {
    strips.forEach(function (e) { paintStrip(e.c, e.values, e.color, e.opts); });
  }
  function paintStrip(c, values, color, opts) {
    var rect = c.getBoundingClientRect();
    if (rect.width < 2) return;
    c.width = Math.round(rect.width * dpr); c.height = Math.round(rect.height * dpr);
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var w = rect.width, h = rect.height, top = 4, bottom = h - 2, left = 0, right = w - 30;
    var max = Math.max(1, Math.max.apply(null, values), opts.ref ? Math.max.apply(null, opts.ref) : 0);
    var n = values.length;
    function X(t) { return left + (t / Math.max(1, n - 1)) * (right - left); }
    function Y(v) { return bottom - (v / max) * (bottom - top); }
    g.fillStyle = colors["--grid"];
    g.fillRect(left, bottom, right - left, 1);
    g.fillRect(left, top, right - left, 1);
    g.font = "10px " + token("--font-num");
    g.fillStyle = colors["--muted"]; g.textBaseline = "middle";
    g.fillText(String(Math.round(max)), right + 4, top + 1);
    g.fillText("0", right + 4, bottom);
    if (opts.ref) {
      g.beginPath();
      opts.ref.forEach(function (v, t) { if (t) g.lineTo(X(t), Y(v)); else g.moveTo(X(t), Y(v)); });
      g.setLineDash([3, 3]); g.strokeStyle = colors["--muted"]; g.lineWidth = 1; g.stroke(); g.setLineDash([]);
    }
    g.beginPath();
    values.forEach(function (v, t) { if (t) g.lineTo(X(t), Y(v)); else g.moveTo(X(t), Y(v)); });
    g.lineTo(X(n - 1), bottom); g.lineTo(X(0), bottom); g.closePath();
    g.globalAlpha = 0.16; g.fillStyle = color; g.fill(); g.globalAlpha = 1;
    g.beginPath();
    values.forEach(function (v, t) { if (t) g.lineTo(X(t), Y(v)); else g.moveTo(X(t), Y(v)); });
    g.strokeStyle = color; g.lineWidth = 1.6; g.lineJoin = "round"; g.stroke();
    var tx = X(view.turn);
    g.fillStyle = colors["--ink"]; g.globalAlpha = 0.35; g.fillRect(tx - 0.5, top, 1, bottom - top); g.globalAlpha = 1;
    g.beginPath(); g.arc(tx, Y(values[view.turn]), 3, 0, Math.PI * 2); g.fillStyle = color; g.fill();
  }
  function series(k, field) { return D.frames.map(function (f, t) { return stat(t, k, field); }); }
  function renderCharts() {
    var box = $("sv-strips");
    box.innerHTML = "";
    strips = [];
    if (view.selected >= 0) {
      var k = view.selected;
      $("sv-chart-who").innerHTML = '<span class="sv-sw" style="background:' + D.palette[k] + '"></span> ' + esc(D.players[k]);
      box.appendChild(strip("Units (dashed: cap)", series(k, "units"), colors["--s1"], { ref: series(k, "cap") }));
      box.appendChild(strip("Sites held", series(k, "holdings"), colors["--s2"]));
      box.appendChild(strip("Stock (manpower + fuel + steel)", D.frames.map(function (f, t) {
        return stat(t, k, "manpower") + stat(t, k, "fuel") + stat(t, k, "steel");
      }), colors["--s3"]));
    } else {
      $("sv-chart-who").textContent = "World";
      box.appendChild(strip("Median units per live player", A.medianUnits, colors["--s1"]));
      box.appendChild(strip("Player-vs-player fights per turn", A.fightsPvp, colors["--hot"]));
      box.appendChild(strip("Players still in", A.alive, colors["--s2"]));
      var note = document.createElement("div");
      note.className = "sv-chartnote";
      note.textContent = "Select a player in the table for their own lines.";
      box.appendChild(note);
    }
  }

  function describe(e) {
    var kinds = D.kinds;
    switch (e[0]) {
      case EV.combat: {
        var owners = [];
        e[2].forEach(function (p) { if (owners.indexOf(p[1]) < 0) owners.push(p[1]); });
        owners.sort(function (a, b) { return (a === 0) - (b === 0); }); // players first, garrison last
        var lost = e[2].filter(function (p) { return p[6]; }).length;
        return { color: owners.indexOf(0) >= 0 ? FIGHT_GARRISON : FIGHT_PVP, owners: owners,
          text: "Fight: " + owners.map(ownerName).join(" vs ") + ", " + e[2].length + " units, " + lost + " lost" };
      }
      case EV.barrage: {
        var hit = e[5][0];
        return { color: "#e8dcc0", owners: [e[3]].concat(hit ? [hit[1]] : []),
          text: "Barrage by " + ownerName(e[3]) + (hit ? " on " + ownerName(hit[1]) + ", " + hit[3] + " damage" + (hit[5] ? ", destroyed" : "") : ", missed an empty hex") };
      }
      case EV.capture:
        return { color: D.palette[e[3] - 1], owners: [e[3], e[4]],
          text: ownerName(e[3]) + " took " + (/^[aeiou]/.test(SITE_NAMES[kinds[D.siteKind[e[2]]]]) ? "an " : "a ") + SITE_NAMES[kinds[D.siteKind[e[2]]]] + (D.siteLevel[e[2]] > 1 ? " (L" + D.siteLevel[e[2]] + ")" : "") + " from " + holderName(e[4]) };
      case EV.destroyed:
        return { color: "#ffffff", owners: [e[3]], text: ownerName(e[3]) + " lost " + D.types[e[4]] + (e[5] ? " to barrage" : " in a fight") };
      case EV.disband:
        return { color: "#c9c3b2", owners: [e[3]], text: ownerName(e[3]) + " disbanded " + D.types[e[4]] + ", out of " + (e[5] ? "manpower" : "fuel") };
      case EV.eliminated:
        return { color: D.palette[e[2] - 1], owners: [e[2]], text: ownerName(e[2]) + " eliminated" };
      case EV.recruit:
        return { color: D.palette[e[3] - 1], owners: [e[3]], text: ownerName(e[3]) + " recruited " + D.types[e[4]] };
      case EV.built:
        return { color: D.palette[e[2] - 1], owners: [e[2]], text: ownerName(e[2]) + " finished " + D.buildings[e[3]] + " level " + e[4] };
    }
    return { color: "#888", owners: [], text: "Event" };
  }
  var FILTERS = {
    key: [EV.combat, EV.capture, EV.eliminated], fights: [EV.combat], captures: [EV.capture], barrages: [EV.barrage],
    losses: [EV.destroyed, EV.disband], growth: [EV.recruit, EV.built], all: [0, 1, 2, 3, 4, 5, 6, 7],
  };
  function eventRank(e) {
    if (e[0] === EV.eliminated) return 0;
    if (e[0] === EV.capture) return D.kinds[D.siteKind[e[2]]] === "base" ? 1 : 2;
    if (e[0] === EV.combat) return e[2].some(function (p) { return p[1] === 0; }) ? 4 - 0.001 * e[2].length : 3 - 0.001 * e[2].length;
    return 5 + e[0];
  }
  var listed = [];
  function renderEvents() {
    var f = D.frames[view.turn], allowed = FILTERS[view.filter];
    listed = f.e.filter(function (e) { return allowed.indexOf(e[0]) >= 0; });
    if (view.selected >= 0) {
      var code = view.selected + 1;
      listed = listed.filter(function (e) { return describe(e).owners.indexOf(code) >= 0; });
    }
    listed.sort(function (a, b) { return eventRank(a) - eventRank(b); });
    var ul = $("sv-events"), limit = 250;
    if (!listed.length) {
      ul.innerHTML = '<li class="sv-events-empty">' + (view.turn === 0 ? "Turn 0 is the starting position." : "Nothing of this kind this turn" + (view.selected >= 0 ? " for " + esc(D.players[view.selected]) : "") + ".") + "</li>";
      return;
    }
    var html = [];
    for (var k = 0; k < Math.min(limit, listed.length); k++) {
      var d = describe(listed[k]);
      html.push('<li><button type="button" data-ev="' + k + '"><span class="kind" style="background:' + d.color + ';border:1px solid rgba(0,0,0,.5)"></span><span>' + esc(d.text) + "</span></button></li>");
    }
    if (listed.length > limit) html.push('<li class="more">and ' + (listed.length - limit) + " more</li>");
    ul.innerHTML = html.join("");
  }

  function renderHeader() {
    var r = D.raw;
    $("sv-facts").textContent = r.slots + " slots, " + r.bots + " bots, seed " + r.seed + ", " + D.T + " turns, " +
      D.W + "×" + D.H + " hexes, " + D.S + " sites";
    var tiles = [
      { label: "First contact", value: A.firstContact ? "T" + A.firstContact.t : "none", note: "first player-vs-player fight", jump: A.firstContact },
      { label: "Fights", value: String(A.totalPvp), note: "player vs player" },
      { label: "Garrison fights", value: String(A.totalGarrison), note: "against neutral sites" },
      { label: "Biggest battle", value: A.biggest ? A.biggest.n + " units" : "none",
        note: A.biggest ? A.biggest.fights + (A.biggest.fights === 1 ? " fight" : " fights") + ", turn " + A.biggest.t : "no fights", jump: A.biggest },
      { label: "Eliminated", value: String(A.eliminations), note: "of " + D.P + " players" },
      { label: "Median units", value: String(Math.round(A.endMedian * 10) / 10), note: "per live player at T" + D.T },
    ];
    var box = $("sv-stats");
    box.innerHTML = "";
    tiles.forEach(function (tile) {
      var el = document.createElement(tile.jump ? "button" : "div");
      el.className = "sv-stat";
      if (tile.jump) { el.type = "button"; el.title = "Go to this fight"; }
      el.innerHTML = '<span class="sv-label">' + esc(tile.label) + "</span><b>" + esc(tile.value) + "</b><small>" + esc(tile.note) + "</small>";
      if (tile.jump) {
        var j = tile.jump;
        el.addEventListener("click", function () { setPlaying(false); setTurn(j.t); centreOn(j.hex, Math.max(cam.s, 10)); flashHex(j.hex); });
      }
      box.appendChild(el);
    });
  }

  function renderKey() {
    var items = [
      ["Base", function (g) { siteGlyph(g, "base", 11, 8, 6, D.palette[0], MARK_EDGE, false); }],
      ["City", function (g) { siteGlyph(g, "city", 11, 8, 5.5, D.palette[1], MARK_EDGE, false); }],
      ["Factory", function (g) { siteGlyph(g, "factory", 11, 8, 5, D.palette[2] || D.palette[0], MARK_EDGE, false); }],
      ["Oil field", function (g) { siteGlyph(g, "oil_field", 11, 8, 5.5, NEUTRAL_SITE, MARK_EDGE, false); }],
      ["Steel mill", function (g) { siteGlyph(g, "steel_mill", 11, 8, 5.5, NEUTRAL_SITE, MARK_EDGE, false); }],
      ["Farm (grey: unheld)", function (g) { siteGlyph(g, "farm", 11, 8, 5, NEUTRAL_SITE, MARK_EDGE, false); }],
      ["Unit, deployed", function (g) { unitGlyph(g, 11, 8, 4.5, D.palette[0], false, false); }],
      ["Unit, in column", function (g) { unitGlyph(g, 11, 8, 4, D.palette[0], true, false); }],
      ["Neutral garrison", function (g) { unitGlyph(g, 11, 8, 3.4, GARRISON_COLOR, false, true); }],
      ["Fight (amber: garrison)", function (g) { g.beginPath(); g.arc(11, 8, 6, 0, 7); g.fillStyle = "rgba(255,91,53,0.34)"; g.fill(); g.lineWidth = 2; g.strokeStyle = FIGHT_PVP; g.stroke(); }],
      ["Capture", function (g) { g.beginPath(); g.arc(11, 8, 6, 0, 7); g.lineWidth = 2; g.strokeStyle = D.palette[1] || D.palette[0]; g.stroke(); }],
      ["Unit lost", function (g) { g.beginPath(); g.moveTo(7, 4); g.lineTo(15, 12); g.moveTo(15, 4); g.lineTo(7, 12); g.lineWidth = 2.6; g.strokeStyle = "#000"; g.stroke(); g.lineWidth = 1.2; g.strokeStyle = "#fff"; g.stroke(); }],
    ];
    var box = $("sv-key");
    box.innerHTML = "";
    items.forEach(function (it) {
      var row = document.createElement("div"), c = document.createElement("canvas");
      c.width = 44; c.height = 32;
      var g = c.getContext("2d");
      g.scale(2, 2);
      g.fillStyle = "#68692e"; g.fillRect(0, 0, 22, 16);
      it[1](g);
      row.appendChild(c);
      var span = document.createElement("span"); span.textContent = it[0]; row.appendChild(span);
      box.appendChild(row);
    });
  }

  // --- Tooltip -------------------------------------------------------------------------------------------------
  var lastTip = { x: 0, y: 0 };
  function tipHidden() { return $("sv-tip").hidden; }
  function hideTip() { $("sv-tip").hidden = true; tipHex = -1; tipPinned = false; }
  function showTip(hex, x, y) {
    var tip = $("sv-tip"), f = D.frames[view.turn];
    tipHex = hex; lastTip = { x: x, y: y };
    var col = hex % D.W, row = (hex / D.W) | 0;
    var html = "<h4>" + TERRAIN_KEYS[D.terr[hex]] + (D.road[hex] ? ", road" : "") + ' <span class="sv-label sv-num">' + col + "," + row + "</span></h4>";
    var s = D.siteAt[hex];
    if (s >= 0) {
      var kind = D.kinds[D.siteKind[s]], h = f.holders[s];
      html += "<div>" + '<span class="sv-sw" style="background:' + (h > 0 ? D.palette[h - 1] : NEUTRAL_SITE) + '"></span> ' +
        esc(SITE_NAMES[kind]) + (kind !== "base" && kind !== "city" ? " level " + D.siteLevel[s] : "") +
        (D.siteGarrison[s] ? ", garrison tier " + D.siteGarrison[s] : "") + ", " + esc(holderName(h)) + "</div>";
    }
    var rows = byHex.get(hex) || [];
    if (rows.length) {
      html += "<ul>";
      rows.forEach(function (o) {
        var u = f.units, owner = u[o + 1];
        html += '<li><span class="sv-sw" style="background:' + ownerColor(owner) + '"></span>' + esc(ownerName(owner)) + " " +
          esc(D.types[u[o + 2]]) + ' <span class="sv-num">' + u[o + 3] + "hp</span>" +
          ((u[o + 4] & 1) ? ", column" : "") + ((u[o + 4] & 2) ? ", disordered" : "") + "</li>";
      });
      html += "</ul>";
    }
    var here = f.e.filter(function (e) { return e[1] === hex && (e[0] === EV.combat || e[0] === EV.capture || e[0] === EV.eliminated); });
    if (here.length) {
      html += "<ul>";
      here.forEach(function (e) { html += "<li>" + esc(describe(e).text) + "</li>"; });
      html += "</ul>";
    }
    tip.innerHTML = html;
    tip.hidden = false;
    var wrap = $("sv-mapwrap").getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    var left = x + 14, top = y + 14;
    if (left + tw > wrap.width - 8) left = x - tw - 14;
    if (top + th > wrap.height - 8) top = y - th - 14;
    tip.style.left = clamp(left, 8, Math.max(8, wrap.width - tw - 8)) + "px";
    tip.style.top = clamp(top, 8, Math.max(8, wrap.height - th - 8)) + "px";
  }
  function flashHex(hex) { flash.hex = hex; flash.hexStart = performance.now(); requestDraw(); }

  // --- Input ---------------------------------------------------------------------------------------------------------
  function bindMap() {
    var pointers = new Map(), moved = 0, pinch = null;
    function local(e) { var r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    canvas.addEventListener("pointerdown", function (e) {
      canvas.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, local(e));
      moved = 0;
      if (pointers.size === 2) {
        var p = Array.from(pointers.values());
        pinch = { d: Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]) };
      }
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", function (e) {
      var pt = local(e);
      if (!pointers.has(e.pointerId)) {
        if (e.pointerType === "mouse" && !tipPinned) {
          var w = toWorld(pt[0], pt[1]), hex = hexAtWorld(D, w[0], w[1]);
          if (hex >= 0) showTip(hex, pt[0], pt[1]); else hideTip();
        }
        return;
      }
      var prev = pointers.get(e.pointerId);
      pointers.set(e.pointerId, pt);
      if (pointers.size === 1) {
        cam.ox += pt[0] - prev[0]; cam.oy += pt[1] - prev[1];
        moved += Math.abs(pt[0] - prev[0]) + Math.abs(pt[1] - prev[1]);
        if (moved > 4) { hideTip(); }
        requestDraw();
      } else if (pointers.size === 2 && pinch) {
        var p = Array.from(pointers.values());
        var d = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]);
        var mx = (p[0][0] + p[1][0]) / 2, my = (p[0][1] + p[1][1]) / 2;
        if (pinch.d > 0) zoomAt(d / pinch.d, mx, my);
        if (pinch.mx !== undefined) { cam.ox += mx - pinch.mx; cam.oy += my - pinch.my; }
        pinch.d = d; pinch.mx = mx; pinch.my = my;
        moved += 10;
      }
    });
    function end(e) {
      if (!pointers.has(e.pointerId)) return;
      var pt = local(e);
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 0) canvas.classList.remove("dragging");
      if (e.type === "pointerup" && moved <= 4 && pointers.size === 0) {
        var w = toWorld(pt[0], pt[1]), hex = hexAtWorld(D, w[0], w[1]);
        if (hex >= 0 && !(tipPinned && tipHex === hex)) { tipPinned = true; showTip(hex, pt[0], pt[1]); }
        else hideTip();
      }
    }
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    canvas.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse" && !tipPinned) hideTip(); });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var pt = local(e);
      var factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018));
      zoomAt(factor, pt[0], pt[1]);
    }, { passive: false });
  }

  function bindControls() {
    $("sv-zoom-in").addEventListener("click", function () { zoomAt(1.5, cw / 2, ch / 2); });
    $("sv-zoom-out").addEventListener("click", function () { zoomAt(1 / 1.5, cw / 2, ch / 2); });
    $("sv-fit").addEventListener("click", fitView);
    $("sv-prev").addEventListener("click", function () { setPlaying(false); setTurn(view.turn - 1); });
    $("sv-next").addEventListener("click", function () { setPlaying(false); setTurn(view.turn + 1); });
    $("sv-play").addEventListener("click", function () { setPlaying(!view.playing); });
    $("sv-speed").addEventListener("change", function (e) { view.speed = parseFloat(e.target.value); });
    var range = $("sv-turn");
    range.max = String(D.T);
    range.addEventListener("input", function () { setTurn(parseInt(range.value, 10), true); });
    var hist = $("sv-hist");
    hist.addEventListener("pointerdown", function (e) {
      hist.setPointerCapture(e.pointerId);
      setPlaying(false); setTurn(histTurnAt(e.clientX));
      function move(ev) { setTurn(histTurnAt(ev.clientX)); }
      function up() { hist.removeEventListener("pointermove", move); hist.removeEventListener("pointerup", up); }
      hist.addEventListener("pointermove", move); hist.addEventListener("pointerup", up);
    });
    [["sv-l-tint", "tint"], ["sv-l-garrison", "garrison"], ["sv-l-events", "events"], ["sv-l-trails", "trails"]].forEach(function (pair) {
      var box = $(pair[0]);
      box.checked = layers[pair[1]];
      box.addEventListener("change", function () { layers[pair[1]] = box.checked; requestDraw(); });
    });
    $("sv-follow").addEventListener("change", function (e) {
      view.follow = e.target.checked;
      if (view.follow && view.selected >= 0) { followSelected(); requestDraw(); }
    });
    $("sv-evfilter").addEventListener("change", function (e) { view.filter = e.target.value; renderEvents(); });
    document.querySelectorAll(".sv-players th button").forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.getAttribute("data-sort");
        // Names and elimination turns read best ascending, counts descending.
        if (view.sort === key) view.sortDir = -view.sortDir;
        else { view.sort = key; view.sortDir = key === "name" || key === "out" ? 1 : -1; }
        renderTable();
      });
    });
    var tbody = $("sv-players").tBodies[0];
    tbody.addEventListener("click", function (e) {
      var tr = e.target.closest("tr");
      if (tr) selectPlayer(parseInt(tr.getAttribute("data-k"), 10));
    });
    tbody.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var tr = e.target.closest("tr");
      if (!tr) return;
      e.preventDefault();
      var k = parseInt(tr.getAttribute("data-k"), 10);
      selectPlayer(k);
      var again = tbody.querySelector('tr[data-k="' + k + '"]');
      if (again) again.focus();
    });
    $("sv-events").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-ev]");
      if (!b) return;
      var ev = listed[parseInt(b.getAttribute("data-ev"), 10)];
      centreOn(ev[1], Math.max(cam.s, 12));
      flashHex(ev[1]);
    });
    document.addEventListener("keydown", function (e) {
      var tag = e.target.tagName;
      if (tag === "SELECT" || tag === "TEXTAREA" || (tag === "INPUT" && e.target.type === "range" && e.key !== " ")) return;
      if (e.target.closest && e.target.closest(".sv-players tbody") && (e.key === " " || e.key === "Enter")) return;
      if (e.key === " " && tag !== "BUTTON" && tag !== "INPUT") { e.preventDefault(); setPlaying(!view.playing); }
      else if (e.key === " " && tag === "INPUT" && e.target.type === "range") { e.preventDefault(); setPlaying(!view.playing); }
      else if (e.key === "ArrowLeft" && tag !== "INPUT") { e.preventDefault(); setPlaying(false); setTurn(view.turn - 1); }
      else if (e.key === "ArrowRight" && tag !== "INPUT") { e.preventDefault(); setPlaying(false); setTurn(view.turn + 1); }
      else if ((e.key === "+" || e.key === "=") && tag !== "INPUT") zoomAt(1.5, cw / 2, ch / 2);
      else if (e.key === "-" && tag !== "INPUT") zoomAt(1 / 1.5, cw / 2, ch / 2);
      else if (e.key === "0" && tag !== "INPUT") fitView();
      else if (e.key === "Escape") hideTip();
    });
    // Only a real size change re-lays out; rebuilding panels here could change
    // the layout again and loop.
    var lastSize = "";
    new ResizeObserver(function () {
      var rect = $("sv-mapwrap").getBoundingClientRect(), side = $("sv-strips").getBoundingClientRect();
      var size = Math.round(rect.width) + "x" + Math.round(rect.height) + ":" + Math.round(side.width);
      if (size === lastSize) return;
      lastSize = size;
      resize(); drawHist(); repaintStrips();
    }).observe(document.body);
    function themeChanged() { readColors(); drawHist(); renderCharts(); requestDraw(); }
    var dark = window.matchMedia("(prefers-color-scheme: dark)");
    if (dark.addEventListener) dark.addEventListener("change", themeChanged);
    new MutationObserver(themeChanged).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
  }

  // --- Boot ------------------------------------------------------------------------------------------------------------
  function fail(err) {
    var msg = $("sv-msg");
    msg.hidden = false;
    msg.textContent = "The season could not be shown: " + (err && err.message ? err.message : String(err)) +
      (err && err.stack ? " (" + String(err.stack).split("\n")[1] + ")" : "");
    if (!D) $("sv-facts").textContent = "No season loaded";
  }

  // Any later script error is shown over the map rather than leaving it blank.
  window.addEventListener("error", function (e) { fail(e.error || e.message); });

  function start() {
    canvas = $("sv-map");
    ctx = canvas.getContext("2d");
    readColors();
    loadRaw().then(function (raw) {
      D = decode(raw);
      A = analyse(D);
      buildTerrain();
      renderHeader();
      renderKey();
      bindMap();
      bindControls();
      $("sv-msg").hidden = true;
      resize();
      // #turn-30 opens on turn 30; #turn-30-b012 also selects player b012.
      var m = /^#turn-(\d+)(?:-([A-Za-z0-9]+))?$/.exec(location.hash);
      setTurn(m ? parseInt(m[1], 10) : 0);
      if (m && m[2] && D.players.indexOf(m[2]) >= 0) selectPlayer(D.players.indexOf(m[2]));
      window.__seasonViewer = { turns: D.T, players: D.P, ready: true };
    }).catch(fail);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
