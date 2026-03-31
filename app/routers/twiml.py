"""
Twilio TwiML webhook + WebSocket media stream bridge.

Flow:
  1. Twilio calls POST /twiml/answer when recipient picks up
  2. We return TwiML that opens a Media Stream WebSocket to /twiml/ws
  3. /twiml/ws bridges audio bidirectionally between Twilio and ElevenLabs:
       Phone ──mulaw 8kHz──► Twilio ──WebSocket──► FastAPI ──WebSocket──► ElevenLabs AI
       Phone ◄─mulaw 8kHz── Twilio ◄─WebSocket──  FastAPI ◄─WebSocket──  ElevenLabs AI

Audio format notes:
  - Twilio sends/receives: mulaw 8kHz (G.711 u-law), base64 encoded
  - ElevenLabs is configured with ASR input format "ulaw_8000" so we forward
    Twilio audio directly — no conversion needed on the input side.
  - ElevenLabs sends back PCM 16kHz audio which we must convert to mulaw 8kHz
    before forwarding to Twilio.
"""

import asyncio
import base64
import json
import logging

import websockets
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

from app.config import settings
from app.services.elevenlabs_service import get_or_create_agent, get_signed_websocket_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twiml", tags=["twiml"])

# ── Audio conversion helpers ──────────────────────────────────────────────────

try:
    import audioop  # built-in up to Python 3.12

    _HAS_AUDIOOP = True
except ImportError:
    try:
        import audioop_lts as audioop  # pip install audioop-lts (Python 3.13+)

        _HAS_AUDIOOP = True
    except ImportError:
        _HAS_AUDIOOP = False
        logger.warning(
            "audioop not available — ElevenLabs audio output will NOT be converted. "
            "Install audioop-lts: pip install audioop-lts"
        )


def pcm16k_to_mulaw8k(pcm_bytes: bytes, resample_state=None):
    """
    Convert PCM 16-bit 16kHz (ElevenLabs output) → mulaw 8kHz (Twilio input).
    Returns (mulaw_bytes, new_resample_state).
    """
    if not _HAS_AUDIOOP:
        return pcm_bytes, None  # pass-through (will sound wrong, but won't crash)

    # Downsample 16kHz → 8kHz
    pcm_8k, new_state = audioop.ratecv(pcm_bytes, 2, 1, 16000, 8000, resample_state)
    # Linear PCM → G.711 mulaw
    mulaw = audioop.lin2ulaw(pcm_8k, 2)
    return mulaw, new_state


# ── TwiML webhook ─────────────────────────────────────────────────────────────


@router.post("/answer")
async def twiml_answer(request: Request):
    """
    Twilio calls this endpoint when the recipient answers the call.
    We return TwiML that starts a bidirectional Media Stream WebSocket.
    """
    ws_url = settings.twiml_websocket_url
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="direction" value="both"/>
    </Stream>
  </Connect>
</Response>"""
    logger.info(f"Returning TwiML with Media Stream URL: {ws_url}")
    return Response(content=twiml, media_type="application/xml")


# ── WebSocket bridge ──────────────────────────────────────────────────────────


@router.websocket("/ws")
async def media_stream_bridge(twilio_ws: WebSocket):
    """
    Bidirectional bridge between Twilio Media Stream and ElevenLabs Conversational AI.

    Twilio ──► [this WebSocket] ──► ElevenLabs  (sends user speech)
    Twilio ◄── [this WebSocket] ◄── ElevenLabs  (receives AI speech)
    """
    await twilio_ws.accept()
    logger.info("Twilio Media Stream WebSocket connected")

    # Shared state across the two concurrent tasks
    stream_sid: str | None = None
    call_sid: str | None = None
    resample_state = None  # stateful resampler for audio continuity

    try:
        # ── Resolve ElevenLabs agent & get a signed WebSocket URL ─────────────
        agent_id = await get_or_create_agent()
        signed_url = await get_signed_websocket_url(agent_id)
        logger.info(f"Connecting to ElevenLabs agent {agent_id}")

        async with websockets.connect(signed_url) as el_ws:
            logger.info("ElevenLabs WebSocket connected")

            # ── ElevenLabs init message ───────────────────────────────────────
            # The agent prompt is already configured on the agent, but we can
            # override first_message and other config per-call if needed.
            init_msg = {
                "type": "conversation_initiation_client_data",
                "custom_llm_extra_body": {},
            }
            await el_ws.send(json.dumps(init_msg))

            # ── Task 1: Twilio → ElevenLabs ───────────────────────────────────
            async def forward_twilio_to_elevenlabs():
                nonlocal stream_sid, call_sid
                try:
                    while True:
                        raw = await twilio_ws.receive_text()
                        msg = json.loads(raw)
                        event = msg.get("event")

                        if event == "start":
                            stream_sid = msg["start"]["streamSid"]
                            call_sid = msg["start"]["callSid"]
                            logger.info(f"Stream started: streamSid={stream_sid} callSid={call_sid}")

                        elif event == "media":
                            # Twilio sends mulaw 8kHz base64 — forward directly to ElevenLabs
                            # (agent is configured with ASR input format ulaw_8000)
                            audio_b64 = msg["media"]["payload"]
                            await el_ws.send(
                                json.dumps({"user_audio_chunk": audio_b64})
                            )

                        elif event == "stop":
                            logger.info(f"Stream stopped: callSid={call_sid}")
                            break

                except WebSocketDisconnect:
                    logger.info("Twilio WebSocket disconnected")
                except Exception as e:
                    logger.error(f"Error in Twilio→ElevenLabs task: {e}")

            # ── Task 2: ElevenLabs → Twilio ───────────────────────────────────
            async def forward_elevenlabs_to_twilio():
                nonlocal resample_state
                try:
                    async for raw_msg in el_ws:
                        msg = json.loads(raw_msg)
                        msg_type = msg.get("type")

                        if msg_type == "audio":
                            # ElevenLabs sends PCM 16kHz audio — convert to mulaw 8kHz for Twilio
                            audio_b64 = msg.get("audio_event", {}).get("audio_base_64", "")
                            if audio_b64 and stream_sid:
                                pcm_bytes = base64.b64decode(audio_b64)
                                mulaw_bytes, resample_state = pcm16k_to_mulaw8k(
                                    pcm_bytes, resample_state
                                )
                                mulaw_b64 = base64.b64encode(mulaw_bytes).decode()
                                await twilio_ws.send_text(
                                    json.dumps(
                                        {
                                            "event": "media",
                                            "streamSid": stream_sid,
                                            "media": {"payload": mulaw_b64},
                                        }
                                    )
                                )

                        elif msg_type == "interruption":
                            # AI was interrupted — clear Twilio's audio buffer
                            if stream_sid:
                                logger.debug("AI interrupted — clearing Twilio buffer")
                                await twilio_ws.send_text(
                                    json.dumps({"event": "clear", "streamSid": stream_sid})
                                )
                            resample_state = None  # reset resampler

                        elif msg_type == "agent_response":
                            response_text = (
                                msg.get("agent_response_event", {}).get("agent_response", "")
                            )
                            if response_text:
                                logger.info(f"[Amara]: {response_text}")

                        elif msg_type == "user_transcript":
                            transcript = (
                                msg.get("user_transcription_event", {}).get("user_transcript", "")
                            )
                            if transcript:
                                logger.info(f"[User]: {transcript}")

                        elif msg_type == "conversation_initiation_metadata":
                            conv_id = (
                                msg.get("conversation_initiation_metadata_event", {})
                                .get("conversation_id", "")
                            )
                            logger.info(f"ElevenLabs conversation started: {conv_id}")

                        elif msg_type == "error":
                            logger.error(f"ElevenLabs error: {msg}")

                except Exception as e:
                    logger.error(f"Error in ElevenLabs→Twilio task: {e}")

            # ── Run both directions concurrently ──────────────────────────────
            await asyncio.gather(
                forward_twilio_to_elevenlabs(),
                forward_elevenlabs_to_twilio(),
            )

    except Exception as e:
        logger.error(f"WebSocket bridge error: {e}", exc_info=True)
    finally:
        logger.info(f"Call ended (callSid={call_sid})")
