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
from fastapi import APIRouter, Form, Request, Response, WebSocket, WebSocketDisconnect

from app.config import settings
from app.state import call_metadata
from app.services.elevenlabs_service import (
    get_or_create_agent,
    get_elevenlabs_ws_url,
    get_elevenlabs_ws_headers,
)

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
        return pcm_bytes, None

    # Downsample 16kHz → 8kHz
    pcm_8k, new_state = audioop.ratecv(pcm_bytes, 2, 1, 16000, 8000, resample_state)
    # Linear PCM → G.711 mulaw
    mulaw = audioop.lin2ulaw(pcm_8k, 2)
    return mulaw, new_state


def mulaw8k_to_pcm16k(mulaw_bytes: bytes, resample_state=None):
    """
    Convert Twilio mulaw 8kHz (phone audio) → PCM 16-bit 16kHz (ElevenLabs input).
    Returns (pcm_bytes, new_resample_state).
    """
    if not _HAS_AUDIOOP:
        return mulaw_bytes, None

    # G.711 mulaw → linear PCM at 8kHz
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
    # Upsample 8kHz → 16kHz
    pcm_16k, new_state = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, resample_state)
    return pcm_16k, new_state


# ── TwiML webhook ─────────────────────────────────────────────────────────────


@router.post("/answer")
async def twiml_answer(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
    Direction: str = Form(default=""),
):
    """
    Twilio calls this endpoint when a call is answered — both inbound and outbound.

    - Outbound: patient_name/days_since_delivery are pre-stored in call_metadata by /call/outbound.
    - Inbound:  call_metadata will have no entry for this CallSid, so we use graceful defaults.
      The caller's number (From) is logged so you can identify who called in.
    """
    ws_url = settings.twiml_websocket_url

    # Look up the call variables stored when the call was initiated (outbound only)
    meta = call_metadata.pop(CallSid, {})
    patient_name = meta.get("patient_name", "there")
    days_since_delivery = meta.get("days_since_delivery", "0")

    call_type = "inbound" if Direction == "inbound" else "outbound"
    logger.info(
        f"[{call_type.upper()}] callSid={CallSid} from={From!r} "
        f"patient_name={patient_name!r} days={days_since_delivery}"
    )

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="patient_name" value="{patient_name}"/>
      <Parameter name="days_since_delivery" value="{days_since_delivery}"/>
      <Parameter name="caller_number" value="{From}"/>
    </Stream>
  </Connect>
</Response>"""
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
    patient_name: str = "there"
    days_since_delivery: str = "0"
    resample_state = None     # ElevenLabs PCM 16kHz → Twilio mulaw 8kHz
    upsample_state = None     # Twilio mulaw 8kHz → ElevenLabs PCM 16kHz

    try:
        # ── Connect directly to ElevenLabs WebSocket with API key header ─────
        agent_id = await get_or_create_agent()
        el_ws_url = get_elevenlabs_ws_url(agent_id)
        el_ws_headers = get_elevenlabs_ws_headers()
        logger.info(f"Connecting to ElevenLabs agent {agent_id}")

        async with websockets.connect(el_ws_url, additional_headers=el_ws_headers) as el_ws:
            logger.info("ElevenLabs WebSocket connected")

            # ── Read the Twilio 'start' event first so we know patient_name ───
            # Twilio sends: connected → start → media...
            # We drain messages until we get 'start', then send the ElevenLabs
            # init so patient_name is available from the very first message.
            twilio_buffer: list[dict] = []
            while True:
                raw = await twilio_ws.receive_text()
                msg = json.loads(raw)
                event = msg.get("event")
                if event == "connected":
                    continue  # skip the connected handshake
                if event == "start":
                    stream_sid = msg["start"]["streamSid"]
                    call_sid = msg["start"]["callSid"]
                    custom = msg["start"].get("customParameters", {})
                    patient_name = custom.get("patient_name", "there")
                    days_since_delivery = custom.get("days_since_delivery", "0")
                    logger.info(
                        f"Stream started: streamSid={stream_sid} "
                        f"callSid={call_sid} patient_name={patient_name!r} days={days_since_delivery}"
                    )
                    break
                twilio_buffer.append(msg)  # hold any early media frames

            # ── ElevenLabs init message ───────────────────────────────────────
            # Supply dynamic_variables required by the agent's first_message
            # template. The agent config controls the prompt and first_message;
            # we only inject the runtime variables (e.g. patient_name).
            init_msg = {
                "type": "conversation_initiation_client_data",
                "dynamic_variables": {
                    "patient_name": patient_name,
                    "days_since_delivery": days_since_delivery,
                },
            }
            await el_ws.send(json.dumps(init_msg))
            logger.info(f"Sent ElevenLabs init with patient_name={patient_name!r} days={days_since_delivery}")

            # Flush any early audio frames buffered before the start event
            for buffered in twilio_buffer:
                if buffered.get("event") == "media":
                    mulaw_bytes = base64.b64decode(buffered["media"]["payload"])
                    pcm_bytes, upsample_state = mulaw8k_to_pcm16k(mulaw_bytes, upsample_state)
                    await el_ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm_bytes).decode()}))

            # ── Task 1: Twilio → ElevenLabs ───────────────────────────────────
            async def forward_twilio_to_elevenlabs():
                nonlocal stream_sid, call_sid, patient_name, days_since_delivery, upsample_state
                try:
                    while True:
                        raw = await twilio_ws.receive_text()
                        msg = json.loads(raw)
                        event = msg.get("event")

                        if event == "media":
                            # Convert Twilio mulaw 8kHz → PCM 16kHz for ElevenLabs
                            mulaw_bytes = base64.b64decode(msg["media"]["payload"])
                            pcm_bytes, upsample_state = mulaw8k_to_pcm16k(mulaw_bytes, upsample_state)
                            pcm_b64 = base64.b64encode(pcm_bytes).decode()
                            await el_ws.send(json.dumps({"user_audio_chunk": pcm_b64}))

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
                                logger.info(f"[Bloom]: {response_text}")

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
