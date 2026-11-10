# Concept read: terrain art direction from `docs/concept-art/concept1.png`

Independent visual read, 2026-11-09. Every number is **measured** with PIL/numpy unless it is marked
**(eye)**. The measurement code is in this directory: `measure.py` and `hexgeo.py`.

Conventions:
- **L** is luminance, 0.299R + 0.587G + 0.114B, on a 0-255 scale.
- **HSV** is hue in degrees, then saturation and value as 0-1.
- **Lsd** is the standard deviation of L inside the hex mask.
- **fine** is the mean |L − blur(L, σ=1.5)|. It measures grain at the 1-3 px scale.
- **mid** is the mean |blur(σ=1.5) − blur(σ=W/16)|. It measures mottle and blob scale.
- Texture metrics are computed after the concept hex is resampled to the tile geometry (128 px point-to-point). They are also given at 64 px, which is the board scale.
- Concept crop boxes are (x0, y0, x1, y1) in concept-image pixels. Hex IDs `(k,n)` index the fitted grid; see `labels.png` and `hexsheet.png`.

## 0. Geometry of the concept grid (measured)

- The hexes are flat-top. Horizontal grid edges in the same row repeat every 212.5 px, which is 1.5 W. That gives **W ≈ 141.7 px point to point**, measured over 12 rows.
- The vertical period is 115.2 px, so the hex is **115.2 px flat to flat**.
- **The concept hexes are squashed vertically.** H/W is 0.813, against 0.866 for a regular hex, which is about 6% flatter. To map a concept crop onto tile geometry, scale it by ×0.903 horizontally and ×0.962 vertically. Do not hand raw crops to a model as a "hex shape" reference, or it will reproduce the squash.
- **The concept is almost 1:1 with a 128 px tile** (1 concept px ≈ 0.9 tile px), so concept detail sizes carry over nearly unchanged to 128 px tiles. Board scale (64 px) is about 0.45× the concept.
- Fitted hex centres: x = 27 + 106.3·k. For odd k, y = 184.6 + 115.2·n. For even k, y = 127.0 + 115.2·n. The magenta overlay in `labels.png` confirms the fit.

## A. Concept anatomy per terrain

### A1. Ground / open grass
- **Crops:** hex (3,0) box (300,140,395,230), plus the grass portions of hexes (4,1), (9,2), (12,2), (11,3), (5,1), (2,2) and (10,3), with road pixels excluded by hue.
- **Palette:** median **RGB (104,105,47)**, **HSV 61° / .56 / .41**, **L 98**. The per-hex medians span L 97-106 (3,0 is the lightest).
- **Hue:** **R ≈ G.** This is an olive, ochre-leaning green, not a lime one.
- **Map-wide grass field:** grass-only medians over 80×60 px patches range **L 90-107**, drifting slowly. The drift has no steps at hex lines and no relation to the grid.
- **Radial profile inside a plains hex:** flat, apart from the grid-line highlight bleeding in. There is no vignette and no rim.
- **Texture:**
  - At 128 scale: Lsd 10-15, fine 4.6-6.0, mid 3.4-6.6.
  - At 64 scale: Lsd 7-14, **fine 4.1-6.3**, mid 2.5-4.7.
  - The luminance autocorrelation half-width is **2-3 px (≈2% of W)**.
  - (eye) The texture is short dry-brush strokes 1-3 px wide and 3-6 px long, loosely diagonal, plus scattered single-pixel yellow flecks. It is uniform across the hex.
- **Character:** calm because the grain is *uniform, isotropic and low-amplitude* (about ±13 L). It is not smooth. There are no features in plain hexes.

### A2. Forest
- **Crops:** canopy cores (hex shrunk 35%) of hexes (4,2), (6,0), (7,3), (8,4), (8,5), (12,3) and (12,5). Reference boxes: `forest_single` (395,300,520,420) and `forest_cluster` (815,540,940,760).
- **Palette:** canopy **RGB (52,61,29)**, **HSV 77° / .52 / .24**, **L 54**, p10 34, **p90 82**.
- **Value relative to grass:** **0.56× grass L (−44)**. **Even the brightest 10% of canopy (82) is darker than the grass (98).** Forest is the darkest mass on the map.
- **Hue relative to grass:** about 15° greener.
- **Radial profile, outside to centre**, as median L in bands at 3-10%, 10-20%, 20-30%, 30-50% and 50-100% of the hex inset:

  | Band | Median L |
  |---|---|
  | Outermost (3-10% inset) | 98-104, plain grass |
  | 10-20% | 60-90, ragged canopy edge |
  | Core | 53-57, flat |

  So **each forest is a hex-contained mass with a grass rim of about 5-10% of W.** The edge is a lumpy line of crowns that loosely follows the hexagon. It is not cut by the hex.
- **Texture:**
  - At 128 scale: Lsd 18-19.6, fine 9-10, mid 7.8-8.8.
  - At 64 scale: fine 11.2-12.2, mid 3.9-5.1.
- **Crown spacing:** highlight peaks average **8.8-9.5 px apart at 128 scale (≈7% of W)**. (eye) Crowns are round, about 8-11 px across (6-8% of W), overlapping, and form a closed canopy with no ground visible between them.
- **Lighting:** (eye) each crown is lit on its upper-left with a slightly lighter moss-olive, and dark olive sits under its lower-right.
- **Perspective:** (eye) slight. A few trunks and a soft shadow show along the south rim of the mass.
- **Tree types:** (eye) almost all rounded deciduous crowns. Conifers appear mainly in the scattered-tree hexes (11,4) and (10,4) and in mixed hex (1,1), as individual trees with trunks and shadows cast to the right.

### A3. Hills / ridges (two distinct registers in the concept)
- **Tan rocky ridges, top-left.**
  - **Crops:** hexes (1,0), (2,1) and (1,1), box `hills_tan_ridge` (60,170,300,300).
  - **Whole-hex palette:** RGB (97-106, 93-100, 45-51), L 89-97, Lsd 30-33.
  - **Rock pixels** are 9-19% of the hex: **RGB (162,141,100), HSV 40° / .38 / .64, L 143 (+45 vs grass)**. The lit faces reach (180,160,122), about L 163.
  - **Shade** (darkest 15% of pixels): **(49-61, 43-54, 25-29), L 43-54. Warm umber, R > G.** It is not green.
  - **Texture** at 128: fine 8.5-9.8, mid 11-14.
  - **Form:** (eye) pyramidal peaks with a sharp ridge line. The west face is lit pale tan and the east face is in dark umber shadow, with eroded gullies drawn as pale streaks. Green grassy aprons skirt the peaks.
  - **Footprint:** (eye) about 60-80% of the hex. **The mass spills across hex lines.** The (1,0) ridge continues into its neighbours.
- **Green grassy ridges, top-right.**
  - **Crops:** hexes (11,0) and (12,0), box `hills_green_ridge` (1150,62,1448,180).
  - **Whole-hex palette:** RGB (84-101, 85-101, 39-47), **L 80-95, slightly darker than the plains.**
  - **Rock:** 0-9% of the hex, at the crests.
  - **Shade:** (58-70, 55-67, 28-34), L 53-64, again warm.
  - **Texture** at 128: Lsd 20-23, fine 6.3-7.5.
  - **Form:** (eye) long ridge spurs running diagonally NW-SE, with dark fine creases for gullies and small ochre outcrops on the crests. They fade into the plain with no contour line.
- **Lighting for both registers:** from the west / upper-left.

### A4. River and banks
- **Crops:** hexes (5,3), (7,0), (7,4), (6,2), (5,2) and (6,1). Reference boxes: `river_upper` (660,62,790,240), `river_middle` (500,380,600,560) and `river_lake` (700,590,840,700).
- **Water palette:** **RGB (67,81,77), HSV 163° / .17 / .32, L 76 (0.78× grass, −22).**
  - The colour is a grey-teal or slate. It is *not* saturated blue.
  - (eye) A lighter current streak runs down the middle, and the water is darker at the banks.
- **Width:**
  - Horizontal runs of bright water on near-vertical stretches measure 21-28 px. Including the darker edge water, I estimate (eye) the channel at **22-30 px of 141.7, about 15-21% of W**. That is roughly 30-42% of the flat edge.
  - It widens to a pond about 60 px across in (7,4).
- **Banks:** measured in a ring 3 px wide around the water.
  - **Dark undercut: (34,42,26), L 38**, in a 1-2 px line right at the water.
  - **Pale khaki lip: (138,134,91), L 130.** It is broken, not continuous.
  - (eye) The bank line is ragged and crenellated, at a 2-5 px scale, and grass tufts overhang it.
- **Course:** the river meanders freely and crosses hex edges at arbitrary points and angles. It is one continuous system that ignores the grid.

### A5. Bridges
- **Crops:** `bridge_north` (595,300,700,370) and `bridge_south` (690,690,800,790).
- **Deck palette:** **RGB (125,111,79), HSV 42° / .37 / .49, L 111**. The lit parapet is (169,152,112) and the dark joints and shadow are (48,45,32).
- **Side face and arches:** L ≈ 60.
- **Size:** (eye) the span is about 68 px, **≈48% of W**. The width including parapets and side face is about 25-30 px, **≈18-21% of W, or 2.5-3× the road width.**
- **Construction:** (eye) low parapets on both sides, a visible south side face with dark arch openings, and a shadow on the water. It is in slight 3/4 view from the south.
- **Placement:** the north bridge's west abutment sits *on* a hex line, so the bridge straddles two hexes.

### A6. Dirt roads
- **Crops:** road pixels in hexes (3,1), (2,2), (12,2), (11,3), (4,1), (5,1), (9,2) and (10,3). Reference box `grass_roads` (300,170,510,300).
- **Palette:** **RGB (161,140,96), HSV 41° / .40 / .63, L 141, which is +43 vs grass, so lighter than the grass.** Lsd 8.
- **Width:** the light core measures 7-14 px wide in row cross-sections. (eye) Typically **8-11 px including the soft fringe, ≈6-8% of W.**
- **Detail:** (eye) darker wheel-rut streaks, tiny dark pebbles, a soft frayed edge where grass encroaches, and a faint shadow lip on the lower side.
- **Course:** roads curve, cross hex edges anywhere, and fork mid-hex.

### A7. Town (village)
- **Crops:** hex (9,1), plus parts of (10,1) and (10,2). Box `town` (930,234,1120,365).
- **Palette:** whole hex RGB (100,94,49), **L 91 (−7 vs grass)**, Lsd 32. Roofs are (101,64,41).
- **Built-up share:** 62% of hex (9,1). The cluster spreads across about three hexes.
- **Character:** (eye) 10-16 detached houses and a church tower, strung loosely along a crossroads, with gardens, hedges and green plots between them.
  - Walls are cream or whitewash with dark window dots.
  - Roofs are muted terracotta-brown.
  - We see south facades, in slight oblique view, and soft shadows fall to the lower-right.
  - House footprints are about 10-15 px (7-11% of W).
  - The village edge dissolves into the grass through gardens and single trees.

### A8. City
- **Crops:** hexes (3,4), (2,5), (1,4) and (2,4). Box `city` (20,590,470,720).
- **Palette:**
  - Whole hex RGB (80-88, 69-76, 46-58), **L 70-77 (0.75× grass)**, Lsd 30-34, fine 12.5-15.5 at 128.
  - Roofs are 8-12% of pixels and **(86-92, 55-58, 34-38)**, a dark, muted brick colour.
  - Streets and grey stone are 17-31% of pixels, at (110,110,97).
  - Green is 3-26% of pixels.
- **Built-up share:** **75-95% of each hex. The city fills the hex edge to edge and continues across about 7 hexes as one footprint** with a ragged outer edge.
- **Layout:** (eye) a **rectilinear street grid**, oriented N-S/E-W with a few wider avenues. Streets are 4-6 px wide (3-4% of W). Blocks are closed perimeter blocks with dark courtyards, and building units are about 8-12 px. A grey stone cathedral sits at the centre. We see south facades, in slight oblique view.

### A9. Crop fields (bottom right; no shipped equivalent)
- **Crops:** hex (9,5). Box `fields` (830,694,1160,815).
- **Palette:** RGB (80,82,37), **L 76 (−22 vs grass)**. Dark furrows are (41,50,25) and light ridges are (111,109,51).
- **Furrow period:** **6-7 px (≈4.5% of W)**, measured on a column profile.
- **Layout:** (eye) a patchwork of strips running in different directions, which also crosses hex lines.

### A10. Grid lines
- **Cross-section:** a **1 px dark olive-brown core at about (76,74,36), L ≈ 70, flanked on both sides by about 2 px of pale khaki at about (155,148,92), L 135-165.** The total width is about 5 px, ≈3.5% of W.
- (eye) The effect is an engraved double rule. It is not black, white or a semi-transparent line.
- This is renderer work, not tile work, but it is a large part of the look.

### Lighting and projection (eye, consistent everywhere)
- **Light** comes from the upper-left (west-northwest). Lit faces point west, shadows fall to the east and southeast, and they are short.
- **Projection** is almost top-down with a slight tilt toward a viewer in the south. South faces of buildings and bridges show, as do trunks at the south edge of forests. Hills are drawn as relief, not as side-view cones.

### Value ladder (concept, L)
- forest 54
- hill shade 43-54
- city 70-77
- fields 76
- water 76
- town 91
- **grass 98**
- bridge 111
- road 141
- tan rock 143, lit faces 163

Everything is in an olive / ochre / umber family. The most saturated mass is the grass itself (S .56). Water (S .17) and roads and rock (S .38-.40) are desaturated.

## B. What makes the concept read as one continuous painted map

1. **One ground with no reset at hex lines.** The grass hue, grain and slow tonal drift (L 90-107 across the map) run straight under the grid. Tonal patches are larger than a hex and do not align with it. No hex has its own rim, vignette or edge darkening (the radial profile is flat).
2. **The grid is a separate engraved overlay.** The double khaki-and-dark rule sits *on* the painting and gives every hex the same frame, so tile seams, if any, are hidden under it.
3. **Linear features ignore the grid.** Roads and the river cross hex edges at arbitrary points and angles, curve continuously and fork mid-hex. The eye follows them across many hexes.
4. **Multi-hex features.** The city covers about 7 hexes and the village about 3. The tan ridge spans 3-4 hexes, the green ridge 2-3, and the field patchwork 3. One bridge straddles a hex line. Edges of large masses are ragged, not hex-shaped.
5. **Forests are the deliberate exception, and they still read continuously.** Each forest is a hex-contained mass, but adjacent forest hexes form clusters separated only by a thin grass rim along the grid line. That reads as one wood divided by rides. The rim is *normal grass* (L ≈ 98), not a dark moat.
6. **Shared light, palette and value discipline.**
   - One light direction, one shadow colour family (warm umber, never green-black), and one saturation ceiling.
   - Nothing is brighter *and* more saturated than the grass.
   - Bright elements (roads at 141, rock at 143) are desaturated. Dark elements (forest, water, city, fields) cluster at L 54-77.
   - The result is a narrow, harmonious ladder.
7. **Soft transitions.** Hill aprons fade into grass through mottled olive, the city outskirts have gardens and yards, banks have grass tufts, and scattered trees and hedgerows (hexes 11,4 and 10,4) blend forest into open ground.
8. **Same grain everywhere.** Grass, canopy, rock and roofs share the same 1-3 px dry-brush grain size, so no element looks rendered at a different resolution.

## C. Divergence audit of the shipped tiles (ranked by board-scale harm)

All shipped numbers are measured on `frontend/src/board/tiles/**` with the same masks.

| # | Terrain | Concept | Shipped | Why it hurts at 64 px |
|---|---|---|---|---|
| 1 | **Plains: colour key** | RGB (104,105,47), hue 61°, V .41, **L 98** | RGB (106-110, 126-130, 55-58), **hue 77-79°**, V .49-.51, **L 112-116** | Plains are most of the board. The board is +16 L lighter and lime instead of olive, so every other terrain is keyed against the wrong ground. |
| 2 | **Woods: value structure inverted** | Core L 54, **p90 82 (< grass)**, Lsd 19. Outer band = grass (98-104). Closed canopy. | Core L 67-76, **p90 129-134 (> plains 114)**, **Lsd 35-36 (1.9×)**, mid 17-19 vs 8 (2.2×). Band at 10-20% inset is L 63-67 and outer band L 74-80: **a dark moat ring**. The outer 6% of grass is L 90-97 against plains at 112-116, a **~20 L seam step**. | Woods read as yellow "cauliflower" puffs on dark gaps with a dark halo. They are the busiest, highest-contrast object on the board and compete with unit sprites. The ring makes each forest a sticker. |
| 3 | **Roads: value inverted** | Tan (161,140,96), S .40, **L 141 (+43)**; width (eye) 6-8% of W | Brown (111-120, 81-88, 43-48), **S .61, L 86-93 (−23 vs plains)**; width 13-14 px = **10-11% of W**; hard parallel edges, plank-like streaks | The concept roads recede as pale tracks. The shipped ones are dark, saturated bands that look like timber or trenches and dominate the network. |
| 4 | **River: isoluminant and narrow** | Water (67,81,77), **L 76 (−22 vs grass)**, S .17; width ≈15-21% of W (eye-assisted); two-tone ragged bank (dark undercut L 38 plus pale lip L 130) | Water (109-112, 121-124, 96-105), **L 114-118, the same as plains**; straight_v channel **11-12 px ≈ 9% of W**; smooth uniform width; continuous brown dirt band (L 80-86) on one bank and sand stripe (L 167) on the other | Without a value drop, the river reads as a grey paved channel or road. It is half the concept width, and the one-sided brown/sand stripes look like a drawn ribbon. |
| 5 | **City and town: radial discs** | City built-up **75-95%**, rectilinear grid, L 70-77, continuous across hexes. Town built-up 62%, organic cluster. | City built-up **61-63%**, **six-spoke radial wheel with a central plaza**, L 102. Town built-up **31%**, small radial disc, L 113-114, in a uniform grass ring. | The wheel or star is the strongest "this is a tile" signal on the board. Adjacent city hexes cannot merge into one city, and towns read as badges, not settlements. |
| 6 | **Plains: texture** | fine **4.1-6.3** at 64 px; Lsd 7-14; grain half-width 2-3 px | fine **0.35-0.52** at 64 px (**about 12× less**); Lsd 2-3.5; blotch half-width 6-11 px (soft airbrushed clouds) | It looks like felt or airbrush. Next to it, every feature tile looks pasted on. The concept's calm comes from even grain, not from absence of grain. |
| 7 | **Hills** | Ridge masses fill about 60-80% of the hex and can span hexes. Rock 9-19% (tan) or 0-9% (green) of area. **Umber shade (49-61, 43-54, 25-29).** | 2-4 separate small mounds floating on flat plains (whole-hex median equals plains, L 111-114). Rock 1-6%. **Green shade (47-58, 70-76, 35-38).** | They read as "little bumps on a plains tile". Green-black shadows break the warm palette. There is no ridge continuity. |
| 8 | **Bridge** | Span ≈48% of W, 2.5-3× road width, warm taupe deck (125,111,79), parapets, arched south face, shadow on water | Flat grey slab, **S .05, 16-20 px (13-16% of W)**, no side face or shadow | At 64 px it reads as a grey square dot, not as a bridge. |
| 9 | **Legacy fallbacks** `river/00.png`, `bridge/00.png` | — | Old 64-px pixel art with **black outlines**, saturated blue water (93,120,144) and bright flat grass | Completely off-register wherever an unmodelled river shape falls back to them. |

The structural property worth keeping: every shipped non-woods tile shares the plains base exactly (the outer-band grass of plains, hills, town, city, river, road and bridge tiles is L 111-116 at hue 76-78). Woods is the only terrain that breaks it.

Images: `cmp_pairs_2x.png` (shipped beside concept, per terrain), `cmp_woods_3x.png`, `m_base.png` / `m_river.png` / `m_road.png` (montages), `mockboard_64.png` (shipped tiles assembled at board scale) and `concept_board64.png` (the concept at the same 64 px hex scale).

## D. Prompt-ready direction per terrain

**Shared preamble for every tile:**
- A top-down painted wargame map of 1940s rural Western Europe in summer: one flat-top hexagon seen almost straight down, with a very slight tilt from the south.
- Hand-painted dry-brush texture with a fine, even grain of 1-3 px strokes. The palette is muted olive, ochre and umber.
- Light comes from the upper-left (west-northwest), and short, soft shadows fall to the lower-right in warm umber.
- The ground is olive yellow-green grass (about RGB 104,105,47; R and G nearly equal), identical at every edge so the tile joins any neighbour.
- No outline, border, frame, grid line, vignette, edge darkening, text or units.

**Global must-not:**
- lime or spring green grass
- airbrushed, smooth or cloudy gradients
- any dark or light rim inside the hex edge
- black outlines
- pixel-art stair-stepping
- glossy or vector look
- saturated blue water
- cool green-black shadows

### Plains
Open, flat summer pasture with no features. The whole hex is the shared olive grass, given a uniform fine grain of short dry-brush strokes and sparse single-pixel yellow-ochre flecks. Tone may drift very slightly (within about ±5% brightness) over areas larger than half the hex, never in hex-sized or smaller blotches. Variants differ only in the stroke pattern and fleck placement.
- **Acceptance:** median L 96-102, hue 58-65°, fine-grain energy 4-6 at 64 px, Lsd 8-13.
- **Must not:** paths, trees, bushes, rocks, flower patches, mown stripes, cloud-like blotches, a smooth or blurred surface, or any brightness change toward the edges.

### Woods
- A dense, closed-canopy deciduous wood fills the hexagon, inset so a band of ordinary grass (about 6-8% of the hex width) shows all around.
- The wood's outline is a lumpy line of round crowns that loosely follows the hexagon.
- Crowns are small (about 1/12-1/14 of the hex width), packed and overlapping, with no ground visible between them.
- The canopy is dark olive-green (about RGB 52,61,29), each crown lit on its upper-left with a slightly lighter moss-olive and shaded lower-right. Highlights stay darker than the surrounding grass.
- Along the southern edge, a few trunk bases and a soft umber shadow fall on the grass. A few darker conifer crowns (at most 15%) are mixed in.
- **Acceptance:** core L 50-60, core p90 below 90, Lsd 16-22, outer band within ±3 L of plains.
- **Must not:** yellow or lime sunlit crowns, cauliflower or broccoli puffs, dark gaps between trees, a dark moat or shadow ring inside the hex edge, clearings, or crowns cut by the hex edge.

### Hills
- One low ridge (or two connected spurs) runs diagonally across the hex. The ridge mass covers about 60-75% of the area and is painted as shaded relief, not as cones.
- Slopes are olive grass. West faces are lit warm olive-ochre; east faces fall into warm umber shadow (about RGB 55,50,28).
- Fine dark creases mark erosion gullies. Small patches of exposed ochre-tan rock (about RGB 162,141,100) sit along the crest and in gullies, covering at most about 10% of the area.
- The foot of the slopes melts into the surrounding grass over about a tenth of the hex width, with no contour line, and the edge band is plain grass.
- **Acceptance:** whole-hex L 82-95 (slightly darker than plains), shade pixels with R > G, rock at most 10%.
- **Must not:** isolated pyramids, cones or bumps sitting on flat grass, green or blue-black shadows, slopes brighter or greener than the plains, snow, cliffs, or several tiny separate mounds.
- See E(2) for the tan-peak alternative.

### River (edge-connected)
- A slate grey-teal river (about RGB 67,81,77) is about a fifth darker than the grass, with a faint lighter current streak down its middle. It is about 16% of the hex width, and up to a third of a flat edge.
- Inside the hex it meanders in soft S-bends. Where it meets a hex edge, it crosses that edge's midpoint at right angles, at full width, for the last few pixels.
- Each bank is drawn the same way on both sides: an irregular, crenellated edge, a 1-2 px dark undercut shadow right at the water (about RGB 34,42,26), and a broken pale khaki lip (about RGB 138,134,91) just above it, with occasional grass tufts overhanging.
- **Acceptance:** water L 70-82, S at most .22, ΔL to grass at least −15.
- **Must not:** water as light as the grass, grey-green or bright blue water, parallel ruler-straight banks, a continuous brown dirt stripe or sand ribbon along one bank, or a channel thinner than about an eighth of the hex.

### Road (on plains or hills)
- An unpaved country track of pale sandy tan (about RGB 161,140,96), clearly lighter than the grass. It is about 7% of the hex width (9-10 px at 128).
- Edges are soft and frayed, with grass encroaching. Two faint darker wheel ruts run along it, scattered with tiny dark pebbles, and a slight shadow lip sits on the lower edge.
- It enters and leaves at edge midpoints and curves gently, with no hub or blob where branches meet.
- **Acceptance:** road L 130-150, S .35-.45.
- **Must not:** dark or orange-brown earth, plank or timber texture, hard parallel edges, a width over a tenth of the hex, or a raised embankment look.

### Bridge
- A small stone bridge carries the pale track over the river. It spans about half the hex width and is about 2.5× the road's width.
- Warm grey-taupe stone (deck about RGB 125,111,79) with low parapets on both sides. Its south side face shows two or three dark arch openings and casts a soft shadow on the water below.
- It is drawn in a slight 3/4 view from the south. Abutments land on both banks, where the road continues.
- **Must not:** a flat grey square or slab, no visible side face, a cold grey or concrete colour, black outlines, or a bridge narrower than the road.

### Town
- A loose village of 10-16 detached houses and a church tower, strung along crossing tracks, occupies about 60-70% of the hex. Gardens, hedges, small green plots and single trees separate the houses.
- Walls are cream or whitewash with dark window dots; roofs are muted terracotta-brown (about RGB 100,64,41). South facades show in slight oblique view, with short shadows to the lower-right.
- The village edge dissolves into grass through garden plots rather than ending in a clean ring.
- **Acceptance:** whole-hex L 85-95.
- **Must not:** radial spokes, a circular or star plan, a central plaza hub, a uniform ring of empty grass around a compact disc, or bright orange or pink roofs.

### City
- A dense European town core, built up edge to edge over at least 85% of the hex, so neighbouring city hexes merge into one city.
- A rectilinear block grid of narrow grey streets (about 3-4% of the hex width), oriented north-south and east-west, has one or two wider avenues crossing the centre.
- Closed perimeter blocks carry dark brick-terracotta roofs (about RGB 90,57,36) and cream facades around dark courtyards. Add an occasional grey stone church or civic building and a few small green yards.
- Overall the hex reads clearly darker than grass (L about 70-78).
- **Must not:** a radial or star street plan, a circular footprint, a grass ring around the city, bright saturated roofs, or a light overall value.

### Crop fields (if ever adopted, as their own terrain; see E1)
- A patchwork of 3-5 cultivated strips in different directions, with furrows every 6-7 px (about 4.5% of the hex width).
- Furrows are dark olive (41,50,25) and ridges muted olive-ochre (111,109,51); overall L is about 76.
- Thin hedgerows with single trees run along the strip boundaries.
- **Must not:** bright yellow wheat, checkerboard regularity, or saturated green.

## E. User rulings against the concept

1. **Plains: calm through low detail, not paleness; variants are mild.**
   - **Agrees** on not pale (concept S .56) and on having no features.
   - **Partial conflict on "low internal detail":** the concept grass has *uniform* grain about 12× the shipped fine-grain energy, at a darker L (98) and a more olive hue (61°) than the shipped plains (114, 77°).
   - **What I would do:** keep the ruling, but reword it as "calm = no features and even grain", and set the colour and texture targets from section D. The concept's crop fields would violate "no distinct features" as a plains variant. Keep them out of plains.
2. **Hills: green, grassy, blends with plains, NOT rocky (June).**
   - **Partial conflict.** The concept has *both* registers. The tan rocky peaks (rock 9-19%) are the more prominent; the green ridges with small crest outcrops (rock 0-9%) are the other.
   - **What I would do:** make the default hill the concept's green ridge, which satisfies the June ruling. Allow about 10% ochre crest rock, umber (not green) shadows, and a slightly darker hex than plains. Offer the tan-peak register to the user as an explicit choice, or as a "rough/mountain" terrain later. Do not silently switch to it.
3. **Woods: complete individual trees, deciduous + conifer mix, slight perspective, none sliced by the edge.**
   - **Agrees** on no slicing (the concept has a grass rim of 5-10% of W) and on slight perspective (trunks on the south rim only).
   - **Conflicts** on "individual complete trees": the concept is a closed canopy of small crowns with no ground between, legible only as bumps.
   - **Conflicts** on the mix: the dense forest is about 85%+ round crowns; conifers live in the scattered-tree hexes.
   - **What I would do:** keep no-slice and perspective. Replace "complete trees" with "individually legible crowns in a closed canopy". Cap conifers at about 15%.
4. **Rivers: edge-midpoint crossings, dead straight at the crossing, width ≤ about 1/3 of the flat edge.**
   - **The geometry conflicts with the concept**, which crosses edges anywhere, but the midpoint rule is structurally necessary for independent tiles. **Keep it.** Limit the straight run to the last few pixels and meander inside.
   - **On width,** the concept (≈30-42% of the flat edge, eye-assisted) sits *at or slightly above* the cap. The shipped rivers (about 18% of the flat edge) are far below it. Raise the width to the cap (about 21 px at 128).
5. **City/town: streets radiate to the six edge midpoints.**
   - **Conflicts directly.** The concept has a rectilinear city grid and an organic village, and the radial wheel is the biggest "tile" tell on the board.
   - **What I would do:** drop the radiating requirement. Make the city fill the hex edge to edge with a grid, so any road arriving at any edge reads as entering a street. Make the town an open cluster on a simple crossroads.
6. **Structures get slight 3/4 perspective.** **Agrees.** Concept bridges and buildings show their south faces. Keep it.
7. **No baked outline, grid or vignette; the renderer draws the grid.**
   - **Agrees.** The concept has no hex vignette; its grid is an overlay. The shipped woods violate this rule with their dark moat ring.
   - **Recommend:** restyle the renderer's grid to the concept's engraved double rule (1 px dark olive core, 2 px khaki highlight each side), which is a separate renderer task.

## F. Risks when the concept is cut into independent tiles

1. **Multi-hex features (city of about 7 hexes, ridges of 3-4, fields, village spill).** Independent tiles cannot share one footprint.
   - **Mitigation:** design city tiles to fill edge to edge (≥85% built-up), so adjacent city hexes fuse into one fabric and the hex boundary vanishes under the grid. Give hills tiles a ridge that runs out to two opposite edges at grass value, so neighbouring hills hexes suggest a range. Accept that a lone city hex next to plains will have a hard edge; a thin garden or yard band in its outer 5-6% softens it.
   - **Longer term:** consider edge-masked "outskirts" variants, like rivers.
2. **Ground continuity.** The concept's slow tonal drift spans several hexes. If each tile carries its own mottle, seams and repeating patches appear.
   - **Mitigation:** tiles carry only fine, uniform grain. Lock every tile's border-band grass to the plains median, within ±2 L, as an automated gate.
   - Generate the cross-hex drift in the renderer instead, as a world-space, very low-frequency multiply (about ±5% L, a period of 3-6 hexes), so it runs under the grid as in the concept.
3. **Linear networks at midpoints.** Midpoint-locked roads and rivers produce a hub-and-spoke rhythm that the concept doesn't have.
   - **Mitigation:** curve inside the hex, keep crossings short, never paint a junction blob, and keep roads pale and narrow so the network recedes.
   - Rivers must have identical width and bank treatment at every crossing, or seams show. Make "width at edge = N px, bank style identical" a gate.
4. **Image-model drift.** gpt-image tends to drift toward saturated, smooth and clean (lime grass, glossy water, bright sunlit crowns), which are exactly the shipped failure modes. Downsampling a 1024 px generation to a 128 px hex also erases the 1-3 px grain.
   - **Mitigation:** colour-transfer every candidate onto the palette anchors after generation. Gate on measured L, hue and fine-grain energy at 64 px, using the numbers in D.
   - Generate at a scale where the target grain survives the downsample, at about 4-8 px strokes in a 512-1024 px hex, and verify it.
5. **Dark forests and city against unit sprites.** Concept forest at L 54 and city at L 70-77 are much darker than the shipped tiles. Sprites that rely on contrast with light ground may lose legibility; the concept's units have dark outlines.
   - **Mitigation:** check a few sprites on candidate woods and city tiles before ratifying.
6. **Concept reference geometry.** The concept hexes are about 6% squashed, and its crops include grid lines.
   - **Mitigation:** use the anisotropically corrected crops, and tell the model the grid lines in the reference are *not* to be painted.
7. **Lighting and perspective consistency across separate generations.** Light direction and tilt must match across every terrain.
   - **Mitigation:** add a shared preamble to every prompt, plus a montage review on a mixed mock board (not per-terrain sheets), because seams and light mismatches only show next to neighbours.

## Unit-free concept crops for style reference

All are in `/tmp/claude-1000/-home-timg-code-hex-general/c4c03e3a-b6c3-4f54-9a60-93e2010d6069/scratchpad/direction/refs/`. Each `ref_X.png` is at native size and has a `ref_X_3x.png` nearest-neighbour zoom.

| File | Box | Shows |
|---|---|---|
| `ref_grass_plain.png` | (300,140,395,230) | Plain grass, hex (3,0), with grid |
| `ref_grass_roads.png` | (300,170,510,300) | Grass with a dirt road fork crossing hex lines |
| `ref_forest_single.png` | (395,300,520,420) | One hex-contained forest mass with its grass rim |
| `ref_forest_cluster.png` | (815,540,940,760) | Two stacked forest hexes, showing the cluster reading |
| `ref_hills_tan_ridge.png` | (60,170,300,300) | Tan rocky pyramidal ridges (2,1) and (1,1), with tree fringe |
| `ref_hills_green_ridge.png` | (1150,62,1448,180) | Green grassy ridges with ochre crest rock, (11,0) and (12,0) |
| `ref_river_upper.png` | (660,62,790,240) | River with ragged two-tone banks, next to forest |
| `ref_river_middle.png` | (500,380,600,560) | Meander, the widest stretch |
| `ref_river_lake.png` | (700,590,840,700) | Pond or oxbow, hex (7,4) |
| `ref_bridge_north.png` | (595,300,700,370) | Stone bridge in 3/4 view (best bridge reference) |
| `ref_bridge_south.png` | (690,690,800,790) | Second bridge, partial |
| `ref_town.png` | (930,234,1120,365) | Village cluster on a crossroads. A tiny orange ZOC-line tip sits on the far right edge, so crop 5 px if used. |
| `ref_city.png` | (20,590,470,720) | City grid fabric across several hexes, with outskirts on the left and right |
| `ref_fields.png` | (830,694,1160,815) | Crop-field patchwork with hedgerows and forest |
| `ref_scattered_trees.png` | (1020,500,1250,600) | Individual deciduous and conifer trees in slight perspective, plus a road |

**Existing `art/foundry/refs/`:**
- Unit-free: `forest_a.png` and `ref_1_x384_y256.png` (the same area, with a bridge and river), `hills_b.png` and `town_a.png`.
- `city_a.png` has a few rows of unit-HUD fragments along its top edge.
- `hills_a.png` has a small dark HUD chip at its top-right corner.
- `ref_river_concept.png` is an upscaled river crop *with orange ZOC overlay* in its lower-right, so it is not overlay-free.

Measurement scaffolding is in the same scratch directory:
- `hexgeo.py`, `measure.py`
- `hexes/` (every whole concept hex cut out)
- `hexsheet.png`, `labels.png`
- `cmp_*.png`, `mockboard_*.png`, `concept_board64.png`

`art/codex/terrain/` holds earlier Codex terrain work. I deliberately did not read its prompts, to avoid anchoring.
