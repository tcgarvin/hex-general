/**
 * Small shared UI primitives in the Field-HQ look (docs/UI-STYLE.md): framed
 * parchment cards with ink text, brass buttons, ivory text on the dark page. The
 * chrome itself is CSS in index.css (`.fhq-*`), shared with the world screen. The world
 * screen reuses `Centered`, `Loading` and `ErrorBanner` over its map backdrop.
 */
import { Link, type LinkProps } from "react-router";

/**
 * Centered container for the auth/dispatcher screens. Defaults to full-viewport
 * height; pass `fill` when it sits inside a flex column (e.g. under the shared
 * page header) so it grows to the remaining space instead of forcing 100dvh.
 */
export function Centered({
  children,
  fill = false,
}: {
  children: React.ReactNode;
  fill?: boolean;
}) {
  // Standalone it is the whole page, so it brings the page's dark ground with it.
  return (
    <div
      className={`flex flex-col items-center justify-center gap-6 px-4 py-8 text-ivory-100 ${
        fill ? "flex-1" : "min-h-dvh bg-[#1a1d22]"
      }`}
    >
      {children}
    </div>
  );
}

type ButtonVariant = "primary" | "ghost";

// The kit's button construction (index.css `.fhq-btn`), a size up for pages: brass
// for the primary action, a quiet parchment face for secondary ones.
const BUTTON_BASE = "fhq-btn px-4 py-1.5 text-sm tracking-wider";
const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "",
  ghost: "fhq-btn-quiet",
};

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
};

/** Action button: brass primary, or a quiet parchment-faced ghost for secondary actions. */
export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  return (
    <button className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${className}`} {...props} />
  );
}

/** A router link dressed as a `Button`, for actions that navigate. */
export function ButtonLink({
  variant = "primary",
  className = "",
  ...props
}: LinkProps & { variant?: ButtonVariant }) {
  return <Link className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${className}`} {...props} />;
}

/** Parchment card in the wood-and-brass frame, the same as the in-game panels a step heavier. */
export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`fhq-frame fhq-frame-lg w-full p-4 ${className}`}>{children}</section>;
}

/** Centered loading state. `fill` is forwarded to `Centered` (see above). */
export function Loading({
  label = "Loading…",
  fill = false,
}: {
  label?: string;
  fill?: boolean;
}) {
  return (
    <Centered fill={fill}>
      <p className="font-label text-sm uppercase tracking-wider text-ivory-400">{label}</p>
    </Centered>
  );
}

/** Inline error banner: invalid red on parchment, readable on any ground. */
export function ErrorBanner({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="fhq-note border-invalid! px-4 py-2 text-sm text-invalid"
    >
      {message}
    </p>
  );
}
