import { Card, Centered, ErrorBanner, Loading } from "@/components/ui";

import type { MeState } from "@/auth/useMe";

/**
 * Shows ``children`` (the admin screen) only to an admin, per `GET /me`; anyone
 * else gets a plain notice. The API refuses a non-admin with a 403 regardless,
 * so this gate only spares them a page of failing controls. Kept free of API
 * imports so a unit test can render it.
 */
export function AdminGate({ me, children }: { me: MeState; children: React.ReactNode }) {
  if (me.status === "loading") {
    return <Loading fill label="Checking your account…" />;
  }
  if (me.status === "error") {
    return (
      <Centered fill>
        <ErrorBanner message={me.message} />
      </Centered>
    );
  }
  if (!me.me.is_admin) {
    return (
      <Centered fill>
        <Card className="max-w-md text-center">
          <h1 className="font-display text-xl uppercase tracking-wide">Admin</h1>
          <p className="mt-2 text-sm">This page is not for this account.</p>
        </Card>
      </Centered>
    );
  }
  return <>{children}</>;
}
