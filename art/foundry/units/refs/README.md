# Unit-sheet style reference crops

Source image: `docs/concept-art/concept1.png` (1448x1086). Rectangles are PIL
crop boxes `(left, top, right, bottom)` in source pixels.

| File | Source rect | Contents |
| --- | --- | --- |
| `ref-tank-blue.png` | (150, 70, 430, 330) | Blue medium tank on grass near the top-left mountains; strength pips above the sprite, no badges or ZOC borders. |
| `ref-infantry-blue.png` | (350, 62, 620, 320) | Blue three-man infantry squad on grass, with a second blue tank at right; strength pips only. |
| `ref-terrain-quiet.png` | (672, 565, 932, 825) | Quiet terrain register: grass and forest hexes, river with a bridge, dirt road; no units, no UI chrome. |
| `ref-terrain-register2.png` | (160, 620, 460, 820) | Second terrain register for round 4: town rooflines across the top, grass hexes and a dirt road below; no units, no UI chrome. Added so round 4 could drop the unit-bearing refs (they overrode prompt facing/camera in rounds 1-3) while keeping palette range. |

Selection rules for round 1 (2026-07-05): blue exemplars only (cobalt is the
team-colour key; red would fight it); no HUD/panel areas; terrain crop kept
free of unit sprites and badges.
