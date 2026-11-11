/**
 * Short, memorable names for worlds. The API only gives a world a UUID, so the
 * world list and account page derive a two-word codename from it ("Iron
 * Meadow"), paired with the id's first characters (`worldTag`) so two worlds
 * that happen to share a codename are still told apart.
 */

// 32 x 32 words: 1,024 codenames, plenty for the handful of worlds that run at once.
const FIRST_WORDS = [
  "Iron",
  "Amber",
  "Copper",
  "Granite",
  "Silver",
  "Ashen",
  "Cedar",
  "Oaken",
  "Russet",
  "Slate",
  "Northern",
  "Southern",
  "Eastern",
  "Western",
  "High",
  "Broad",
  "Quiet",
  "Bitter",
  "Golden",
  "Red",
  "Gray",
  "Black",
  "White",
  "Green",
  "Still",
  "Distant",
  "Frozen",
  "Long",
  "Old",
  "Bright",
  "Windy",
  "Stony",
] as const;

const SECOND_WORDS = [
  "Meadow",
  "Ridge",
  "Ford",
  "Vale",
  "Crossing",
  "March",
  "Reach",
  "Heath",
  "Fen",
  "Moor",
  "Downs",
  "Pass",
  "Bluff",
  "Basin",
  "Plain",
  "Forest",
  "Hills",
  "Field",
  "Valley",
  "Crest",
  "Gate",
  "Marsh",
  "Glen",
  "Steppe",
  "Bend",
  "Lowlands",
  "Hollow",
  "Orchard",
  "Spur",
  "Weald",
  "Barrens",
  "Frontier",
] as const;

/** 32-bit FNV-1a: tiny, deterministic and well spread for short ids. */
function fnv1a(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/** The world's two-word codename, e.g. "Iron Meadow". Same id, same name. */
export function worldName(gameId: string): string {
  const hash = fnv1a(gameId);
  const first = FIRST_WORDS[hash % FIRST_WORDS.length];
  const second = SECOND_WORDS[Math.floor(hash / FIRST_WORDS.length) % SECOND_WORDS.length];
  return `${first} ${second}`;
}

/** The id's first four characters, shown beside the codename to keep it unambiguous. */
export function worldTag(gameId: string): string {
  return gameId.slice(0, 4).toUpperCase();
}
