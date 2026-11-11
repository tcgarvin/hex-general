import { glyphUrl, type GlyphName } from "@/ui-art/glyphs";

/**
 * One UI glyph (docs/UI-STYLE.md §4 "Icons"), drawn in the current text colour:
 * ivory on wood, ink on parchment, or a state colour from the parent's class.
 *
 * Give `label` when the glyph stands alone (it becomes an image with that name and
 * a tooltip); leave it out when text naming the thing sits beside it, and the
 * glyph is hidden from assistive tech.
 */
export function Glyph({
  name,
  size = 16,
  label,
  className = "",
}: {
  name: GlyphName;
  /** CSS px, square. The art is 48 px, so 16 to 24 stays crisp at 2x. */
  size?: number;
  label?: string;
  className?: string;
}) {
  const url = `url("${glyphUrl(name)}")`;
  const style = { width: size, height: size, maskImage: url, WebkitMaskImage: url };
  return label === undefined ? (
    <span aria-hidden className={`fhq-glyph ${className}`} style={style} />
  ) : (
    <span role="img" aria-label={label} title={label} className={`fhq-glyph ${className}`} style={style} />
  );
}
