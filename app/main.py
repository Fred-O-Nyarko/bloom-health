"""
FastAPI application entrypoint — Postpartum AI Call Agent.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import calls, twiml
from app.services.elevenlabs_service import get_or_create_agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup: ensure the ElevenLabs agent exists.
    If ELEVENLABS_AGENT_ID is not set, auto-create the agent and log the ID.
    """
    logger.info("🚀 Starting Postpartum AI Call Agent...")
    logger.info(f"   Public URL: {settings.public_base_url}")
    logger.info(f"   Twilio →  Answer webhook: {settings.twiml_answer_url}")
    logger.info(f"   Twilio →  Media stream:   {settings.twiml_websocket_url}")

    # Ensure agent exists (creates it if missing)
    agent_id = await get_or_create_agent()
    logger.info(f"   ElevenLabs agent: {agent_id}")
    logger.info("✅ Ready — trigger a call via POST /call/outbound")

    yield

    logger.info("Shutting down...")


app = FastAPI(
    title="Postpartum AI Call Agent",
    description=(
        "An AI-powered phone call agent that provides postpartum care support. "
        "Powered by Twilio (telephony) + ElevenLabs Conversational AI (STT → LLM → TTS)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calls.router)
app.include_router(twiml.router)


@app.get("/", tags=["health"])
async def root():
    return {
        "name": "Postpartum AI Call Agent",
        "status": "running",
        "docs": "/docs",
        "agent": settings.elevenlabs_agent_id or "auto-creating on first call...",
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
