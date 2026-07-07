import { Navigate, Route, Routes } from "react-router";

import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { Account } from "@/routes/Account";
import { BotPicker } from "@/routes/BotPicker";
import { ChromeLayout } from "@/routes/ChromeLayout";
import { Dispatcher } from "@/routes/Dispatcher";
import { GameShell } from "@/routes/GameShell";
import { Join } from "@/routes/Join";
import { JoinForm } from "@/routes/JoinForm";
import { Login } from "@/routes/Login";
import { Stats } from "@/routes/Stats";

/**
 * Route table (FRONTEND.md). One-game-per-user keeps it tiny: a public login gate
 * and stats page, then the authenticated pages. The dispatcher / account / join
 * pages share the site header (ChromeLayout); the full-bleed game shell carries
 * its own header. Both headers host the same account menu (with the dev switcher).
 *
 * The game screen is a single route: replay is a `?turn=N` mode of the game
 * shell (not a separate route), so the planning⇄replay cycle never remounts the
 * board — see GameShell.
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
          <Route path="/play/bot" element={<BotPicker />} />
          <Route path="/join" element={<JoinForm />} />
          <Route path="/join/:id" element={<Join />} />
        </Route>
        <Route path="/game/:id" element={<GameShell />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
