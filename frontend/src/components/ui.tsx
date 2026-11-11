/**
 * Small shared UI primitives in the Field-HQ look (docs/UI-STYLE.md): parchment
 * cards with ink text, brass buttons, ivory text on the dark page. The world
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

// The primary matches the in-game Submit button, sized up a step for pages.
const BUTTON_BASE =
  "inline-flex items-center justify-center rounded-sm border px-4 py-1.5 font-label text-sm font-semibold uppercase tracking-wider transition-colors disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-brass-600 bg-brass-600 text-wood-900 hover:bg-brass-400",
  // An ink outline for secondary actions on a parchment card.
  ghost: "border-wood-500 bg-transparent text-ink-900 hover:bg-parchment-400/40",
};

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
};

/** Action button: brass primary, or an ink-outlined ghost for parchment cards. */
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

/** Parchment card, the same face as the in-game panels. */
export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`w-full rounded-sm border border-wood-700 bg-parchment-200 p-5 text-ink-900 shadow-lg shadow-black/40 ${className}`}
    >
      {children}
    </section>
  );
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
      className="rounded-sm border border-invalid bg-parchment-200 px-4 py-2 text-sm text-invalid shadow"
    >
      {message}
    </p>
  );
}
