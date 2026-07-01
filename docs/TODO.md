# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## `api.yml` deploys on frontend-only pushes (missing/broken path filter)

The API (Cloud Run) workflow fired for a **frontend-only** push — commit `9d3eb02`
(regen river/bridge tiles under `frontend/src/board/tiles/`), API run
[`28533612068`](https://github.com/tcgarvin/hex-general/actions/runs/28533612068).
Per our convention deploys are path-filtered (`api/**` → API, `frontend/**` →
frontend), so `api.yml` should not run here. Check `.github/workflows/api.yml` for
a proper `paths: [api/**]` filter (frontend.yml has one; api.yml likely lacks or
misconfigures it). Harmless but causes needless API redeploys on every frontend
commit.
