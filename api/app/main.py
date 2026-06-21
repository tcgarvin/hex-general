from fastapi import FastAPI

from app import __version__

app = FastAPI(title="Hex General API", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for Cloud Run and uptime checks. No external dependencies."""
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    """Return the running API version."""
    return {"version": __version__}
