import { Navigate, Route, Routes } from "react-router";

import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { Account } from "@/routes/Account";
import { ChromeLayout } from "@/routes/ChromeLayout";
import { Dispatcher } from "@/routes/Dispatcher";
import { GameShell } from "@/routes/GameShell";
import { Join } from "@/routes/Join";
import { Login } from "@/routes/Login";
import { Stats } from "@/routes/Stats";

/**
 * Route table (FRONTEND.md). One-game-per-user keeps it tiny: a public login gate
 * and stats page, then the authenticated pages. The dispatcher / account / join
 * pages share the site header (ChromeLayout); the full-bleed game shell carries
 * its own header. Both headers host the same account menu (with the dev switcher).
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
          <Route path="/join/:id" element={<Join />} />
        </Route>
        <Route path="/game/:id" element={<GameShell />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
