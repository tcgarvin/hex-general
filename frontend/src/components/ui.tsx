/** Small shared UI primitives. Polish budget is low (playtest tool) — keep it plain. */

/** Full-viewport centered container for the auth/dispatcher screens. */
export function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-6 p-8">
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

/** Centered loading state. */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <Centered>
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
