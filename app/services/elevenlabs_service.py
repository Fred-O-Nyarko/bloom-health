"""
ElevenLabs Conversational AI service.

Handles:
- Auto-creating the postpartum care agent on first run
- Providing the agent_id for WebSocket connections
"""

import httpx
import logging

from app.config import settings
from app.prompts.postpartum import SYSTEM_PROMPT, FIRST_MESSAGE

logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


def _headers() -> dict:
    return {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}


async def get_or_create_agent() -> str:
    """
    Returns the ElevenLabs Agent ID.

    If ELEVENLABS_AGENT_ID is already set in .env, it is returned immediately.
    Otherwise, a new agent is created via the ElevenLabs API, and the ID is
    printed so the user can save it in their .env for future runs.
    """
    if settings.elevenlabs_agent_id:
        logger.info(f"Using existing ElevenLabs agent: {settings.elevenlabs_agent_id}")
        return settings.elevenlabs_agent_id

    logger.info("No ELEVENLABS_AGENT_ID found — creating a new Postpartum Care agent...")
    agent_id = await _create_agent()
    logger.warning(
        f"\n{'=' * 60}\n"
        f"  ✅ ElevenLabs agent created!\n"
        f"  Agent ID: {agent_id}\n"
        f"  👉 Add this to your .env file:\n"
        f"     ELEVENLABS_AGENT_ID={agent_id}\n"
        f"{'=' * 60}"
    )
    # Cache in memory for this session
    settings.elevenlabs_agent_id = agent_id
    return agent_id


async def _create_agent() -> str:
    """Create a new ElevenLabs Conversational AI agent via the API."""
    payload = {
        "name": "Postpartum Care Advisor — Amara",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": SYSTEM_PROMPT,
                    "llm": "claude-3-5-sonnet",
                    "temperature": 0.6,
                    "max_tokens": 300,  # Keep responses concise for phone calls
                },
                "first_message": FIRST_MESSAGE,
                "language": "en",
            },
            "tts": {
                "model_id": "eleven_turbo_v2_5",  # Low-latency model
                "voice_id": settings.elevenlabs_voice_id,
                "optimize_streaming_latency": 3,
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
            "asr": {
                "quality": "high",
                "provider": "elevenlabs",
                "user_input_audio_format": "ulaw_8000",  # Twilio native format — no conversion needed
            },
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{ELEVENLABS_BASE_URL}/convai/agents/create",
            headers=_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["agent_id"]


async def get_signed_websocket_url(agent_id: str) -> str:
    """
    Get a short-lived signed WebSocket URL for ElevenLabs Conversational AI.
    This avoids exposing the API key in WebSocket headers.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{ELEVENLABS_BASE_URL}/convai/conversation/get_signed_url",
            headers=_headers(),
            params={"agent_id": agent_id},
        )
        response.raise_for_status()
        return response.json()["signed_url"]
