import { Navigate, Route, Routes } from "react-router";

import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { DevProfileSwitcher } from "@/dev/DevProfileSwitcher";
import { Dispatcher } from "@/routes/Dispatcher";
import { GameShell } from "@/routes/GameShell";
import { Join } from "@/routes/Join";
import { Login } from "@/routes/Login";
import { Stats } from "@/routes/Stats";

/**
 * Route table (FRONTEND.md). One-game-per-user keeps it tiny: a public login gate
 * and stats page, then the authenticated dispatcher / join / game-shell. The dev
 * switcher overlay rides on top for allowlisted devs.
 */
export function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/stats" element={<Stats />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<Dispatcher />} />
          <Route path="/join/:id" element={<Join />} />
          <Route path="/game/:id" element={<GameShell />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <DevProfileSwitcher />
    </>
  );
}
