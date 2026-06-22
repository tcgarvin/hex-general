import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router";

import { Centered, ErrorBanner, Loading } from "@/components/ui";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";

/**
 * Invite entry point (journey J2): `/join/:id#invite=<token>`. Claims the open slot
 * gated by the invite token, then redirects into the game. The token rides in the
 * URL hash so it never hits a server log as a query param.
 */
export function Join() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const inviteToken = new URLSearchParams(location.hash.replace(/^#/, "")).get(
    "invite",
  );

  const hasInvite = id !== undefined && inviteToken !== null;
  const [error, setError] = useState<string | null>(null);
  const [joinedGameId, setJoinedGameId] = useState<string | null>(null);

  useEffect(() => {
    if (!hasInvite) {
      return;
    }
    let cancelled = false;
    api
      .POST("/games/{game_id}/join", {
        params: { path: { game_id: id } },
        body: { invite_token: inviteToken },
      })
      .then(({ data, error: apiError }) => {
        if (cancelled) {
          return;
        }
        if (apiError) {
          setError(describeApiError(apiError));
        } else {
          setJoinedGameId(data.game_id);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(describeApiError(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [hasInvite, id, inviteToken]);

  if (joinedGameId !== null) {
    return <Navigate to={`/game/${joinedGameId}`} replace />;
  }

  const message = !hasInvite
    ? "This invite link is missing its token."
    : error;
  if (message !== null) {
    return (
      <Centered>
        <ErrorBanner message={message} />
        <Link to="/" className="text-sm text-indigo-400 hover:underline">
          ← Back to dispatcher
        </Link>
      </Centered>
    );
  }
  return <Loading label="Joining game…" />;
}
