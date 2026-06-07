"""ANSIO token server — FastAPI /token endpoint for the three-panel web demo.

Bridge design 04 §A.3 (selection A): a tiny standalone FastAPI signs a LiveKit
JWT for the browser so the static demo can join a real room. The agent worker
(registered name ``agent-py``) is dispatched explicitly via RoomConfiguration,
matching ``frontend/app/api/token/route.ts`` and ``app/bridge.js``.

Secret discipline (task rule ①): ONLY variable names appear here. Values live in
the gitignored agent ``.env`` (run with ``ENV_FILE=/abs/path/.env``). The three
LiveKit secrets are read from the environment, never hard-coded.

Run (reuses the agent venv which already has livekit + dotenv; fastapi/uvicorn
pulled in transiently by ``uv --with``)::

    cd /Users/expansioai/project/moss/hack/ANSIO/agent-py
    ENV_FILE=.env uv run --with fastapi --with "uvicorn[standard]" \
        uvicorn token_server:app \
        --app-dir /Users/expansioai/project/moss/hack/ANSIO-conversational \
        --host 0.0.0.0 --port 8790

The frontend points at it via ``window.ANSIO_TOKEN_ENDPOINT`` (bridge.js default
``http://localhost:8787/token`` — set it to this port, or run on 8787).
bridge.js consumes ``serverUrl`` + ``participantToken``.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import timedelta

from dotenv import load_dotenv

# Load the agent .env first (ENV_FILE convention), then a local .env if present.
# The agent .env is the source of truth for LIVEKIT_* secrets.
_DEFAULT_AGENT_ENV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ANSIO",
    "agent-py",
    ".env",
)
load_dotenv(os.getenv("ENV_FILE", _DEFAULT_AGENT_ENV))
load_dotenv(".env", override=False)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from livekit import api  # noqa: E402

logger = logging.getLogger("ansio.token_server")

# Registered worker name (agent.py: @server.rtc_session(agent_name="agent-py")).
# Three places must agree: this default, bridge.js AGENT_NAME, the worker reg.
AGENT_NAME = os.getenv("AGENT_NAME", "agent-py")

app = FastAPI(title="ANSIO token server")

# Demo: wide-open CORS so the static page (any localhost port / file://) can call
# us. Tighten allow_origins to the deploy domain for any public deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _required_env() -> tuple[str, str, str]:
    """Read the three LiveKit secrets by NAME. Raises if any is missing."""
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    url = os.environ.get("LIVEKIT_URL")
    missing = [
        n
        for n, v in (
            ("LIVEKIT_API_KEY", key),
            ("LIVEKIT_API_SECRET", secret),
            ("LIVEKIT_URL", url),
        )
        if not v
    ]
    if missing:
        raise RuntimeError(f"missing env: {', '.join(missing)}")
    return key, secret, url  # type: ignore[return-value]


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness + config probe (no secret values, only presence booleans)."""
    have_key = bool(os.environ.get("LIVEKIT_API_KEY"))
    have_secret = bool(os.environ.get("LIVEKIT_API_SECRET"))
    have_url = bool(os.environ.get("LIVEKIT_URL"))
    return JSONResponse(
        {
            "ok": have_key and have_secret and have_url,
            "agent_name": AGENT_NAME,
            "env_present": {
                "LIVEKIT_API_KEY": have_key,
                "LIVEKIT_API_SECRET": have_secret,
                "LIVEKIT_URL": have_url,
            },
        }
    )


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    """Sign a participant JWT and dispatch the ANSIO agent into a fresh room.

    Mirrors route.ts: VideoGrants(room_join, can_publish, can_publish_data,
    can_subscribe) + RoomConfiguration(RoomAgentDispatch). Never raises to the
    client without a friendly message (degradation discipline).
    """
    try:
        key, secret, url = _required_env()
    except RuntimeError as e:
        logger.error("token request rejected: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    # Optional client-supplied room_config (bridge.js sends one). We accept its
    # agent_name override but always guarantee a dispatch entry exists.
    agent_name = AGENT_NAME
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        agents = (body or {}).get("room_config", {}).get("agents", [])
        if agents and agents[0].get("agent_name"):
            agent_name = agents[0]["agent_name"]
    except Exception:
        pass

    # Unique identity + room per visit (route.ts pattern) so repeated demos do
    # not collide and the agent dispatch fires fresh each time.
    suffix = random.randint(0, 9999)
    participant_identity = f"voice_assistant_user_{suffix}"
    participant_name = "Founder"
    room_name = f"ansio_demo_{suffix}"

    try:
        grant = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_publish_data=True,  # required for the evidence DataChannel
            can_subscribe=True,
        )
        at = (
            api.AccessToken(key, secret)
            .with_identity(participant_identity)
            .with_name(participant_name)
            .with_grants(grant)
            .with_ttl(timedelta(minutes=15))
        )
        # Explicit agent dispatch (RoomAgentDispatch). metadata carries user_id
        # so the agent can scope its Moss memory, matching route.ts.
        at = at.with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=agent_name,
                        metadata=json.dumps({"user_id": participant_identity}),
                    )
                ]
            )
        )
        jwt = at.to_jwt()
    except Exception as e:
        logger.exception("failed to mint token")
        return JSONResponse({"error": f"token mint failed: {e}"}, status_code=500)

    return JSONResponse(
        {
            "serverUrl": url,
            "roomName": room_name,
            "participantName": participant_name,
            "participantToken": jwt,
        },
        headers={"Cache-Control": "no-store"},
    )
