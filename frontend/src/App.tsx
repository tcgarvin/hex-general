import { Navigate, Route, Routes } from "react-router";

import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { Account } from "@/routes/Account";
import { ChromeLayout } from "@/routes/ChromeLayout";
import { Dispatcher } from "@/routes/Dispatcher";
import { Login } from "@/routes/Login";
import { Stats } from "@/routes/Stats";
import { WorldShell } from "@/world/WorldShell";

/**
 * Route table (FRONTEND.md). A public login gate and stats page, then the
 * authenticated pages. The dispatcher and account pages share the site header
 * (ChromeLayout); the full-bleed world shell carries its own header. Both headers
 * host the same account menu (with the dev switcher).
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/stats" element={<Stats />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<ChromeLayout />}>
          <Route path="/" element={<Dispatcher />} />
          <Route path="/account" element={<Account />} />
        </Route>
        <Route path="/world/:id" element={<WorldShell />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
