import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button, Centered, ErrorBanner } from "@/components/ui";

/**
 * "Join Game" spoke (hub → this): paste a game id + invite token to join an
 * existing match. This hands off to the `/join/:id#invite=…` auto-join route
 * (`Join.tsx`), which actually claims the slot — the token rides in the URL hash
 * so it never lands in a server log as a query param.
 */
export function JoinForm() {
  const navigate = useNavigate();
  const [joinId, setJoinId] = useState("");
  const [joinToken, setJoinToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  function gotoJoin() {
    const id = joinId.trim();
    const token = joinToken.trim();
    if (id === "" || token === "") {
      setError("Enter both the game id and the invite token.");
      return;
    }
    navigate(`/join/${id}#invite=${encodeURIComponent(token)}`);
  }

  return (
    <Centered fill>
      <h1 className="text-2xl font-semibold">Join a game</h1>

      <div className="flex w-full max-w-sm flex-col gap-3 rounded-lg border border-zinc-800 p-5">
        <input
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
          placeholder="Game id"
          value={joinId}
          onChange={(e) => setJoinId(e.target.value)}
        />
        <input
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
          placeholder="Invite token"
          value={joinToken}
          onChange={(e) => setJoinToken(e.target.value)}
        />
        <div className="flex items-center justify-between">
          <Link to="/" className="text-sm text-zinc-400 hover:underline">
            ← Back
          </Link>
          <Button onClick={gotoJoin}>Join game</Button>
        </div>
      </div>

      {error !== null && <ErrorBanner message={error} />}
    </Centered>
  );
}
