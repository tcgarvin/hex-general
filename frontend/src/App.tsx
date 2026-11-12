import { Navigate, Route, Routes } from "react-router";

import { AdminPage } from "@/admin/AdminPage";
import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { Account } from "@/routes/Account";
import { ChromeLayout } from "@/routes/ChromeLayout";
import { Dispatcher } from "@/routes/Dispatcher";
import { Login } from "@/routes/Login";
import { Stats } from "@/routes/Stats";
import { Worlds } from "@/routes/Worlds";
import { WorldShell } from "@/world/WorldShell";

/**
 * Route table (FRONTEND.md). A public login gate and stats page, then the
 * authenticated pages. The dispatcher (`/`, which sends a seated player into their
 * world), the world list (`/worlds`, which never redirects) and the account page
 * share the site header (ChromeLayout), as does the admin screen (`/admin`, admins
 * only); the full-bleed world shell carries its own
 * header. Both headers host the same account menu (with the dev switcher).
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/stats" element={<Stats />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<ChromeLayout />}>
          <Route path="/" element={<Dispatcher />} />
          <Route path="/worlds" element={<Worlds />} />
          <Route path="/account" element={<Account />} />
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route path="/world/:id" element={<WorldShell />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
