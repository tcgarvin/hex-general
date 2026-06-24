/** Small shared UI primitives. Polish budget is low (playtest tool) — keep it plain. */

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
  return (
    <div
      className={`flex flex-col items-center justify-center gap-6 p-8 ${
        fill ? "flex-1" : "min-h-dvh"
      }`}
    >
      {children}
    </div>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
};

/** Primary action button. */
export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonProps) {
  const base =
    "rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const styles =
    variant === "primary"
      ? "bg-indigo-600 text-white hover:bg-indigo-500"
      : "border border-zinc-700 text-zinc-200 hover:bg-zinc-800";
  return <button className={`${base} ${styles} ${className}`} {...props} />;
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
      <p className="text-sm text-zinc-400">{label}</p>
    </Centered>
  );
}

/** Inline error banner. */
export function ErrorBanner({ message }: { message: string }) {
  return (
    <p className="rounded-md border border-red-900 bg-red-950/50 px-4 py-2 text-sm text-red-300">
      {message}
    </p>
  );
}
