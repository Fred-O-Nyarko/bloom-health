"""
POST /call/outbound — trigger an AI postpartum care call to a phone number.
"""

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.services.twilio_service import initiate_call, configure_inbound_webhook
from app.state import call_metadata
from app.config import settings

router = APIRouter(prefix="/call", tags=["calls"])


class MotherContext(BaseModel):
    """Rich per-mother personalization sent by the upstream onboarding backend.

    Used to render a per-call system prompt via ElevenLabs' conversation_config_override.
    """

    preferred_name: str
    preferred_language: str = "english"
    days_since_delivery: int = 0
    delivery_type: str | None = None
    delivery_outcome: str | None = None
    parity: str | None = None
    plurality: str | None = None
    gestational_age_weeks: int | None = None
    feeding_plan: str | None = None
    feeding_challenges_noted: list[str] = []
    mental_health_history: list[str] = []
    chronic_conditions: list[str] = []
    allergies: str | None = None
    discharge_medications: list[dict] = []
    primary_support_name: str | None = None
    primary_support_relationship: str | None = None
    baby_name: str | None = None
    baby_sex: str | None = None
    baby_birth_weight_grams: int | None = None
    nicu_admission: bool = False
    delivery_complications: list[str] = []


class CallRequest(BaseModel):
    to: str  # E.164 format, e.g. "+14155552671"
    patient_name: str = "there"  # Back-compat: used when mother_context is absent
    days_since_delivery: int = 0  # Back-compat: used when mother_context is absent
    hospital_name: str | None = None
    mother_context: MotherContext | None = None

    @field_validator("to")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+"):
            raise ValueError("Phone number must be in E.164 format, e.g. +14155552671")
        return v


class CallResponse(BaseModel):
    call_sid: str
    status: str
    to: str
    message: str


@router.post("/outbound", response_model=CallResponse)
async def outbound_call(body: CallRequest):
    """
    Trigger an outbound Twilio call.

    When the recipient answers, Twilio fetches TwiML from `/twiml/answer`,
    which opens a Media Stream WebSocket to `/twiml/ws` where the
    ElevenLabs Conversational AI agent takes over the conversation.

    If `mother_context` is provided, the WebSocket handler will override the
    agent's system prompt for this conversation only, yielding a personalized
    call. Without it, the default agent prompt is used.

    **Prerequisites:**
    - Twilio credentials configured in `.env`
    - ElevenLabs agent created (auto-created on first run)
    - Server accessible via a public URL (ngrok, Cloudflare Tunnel, etc.)
    """
    result = initiate_call(body.to)

    call_metadata[result["call_sid"]] = {
        "patient_name": body.patient_name,
        "days_since_delivery": str(body.days_since_delivery),
        "hospital_name": body.hospital_name or "your clinic",
        "mother_context": body.mother_context.model_dump() if body.mother_context else None,
    }

    return CallResponse(
        **result,
        message=(
            f"📞 Call initiated to {body.to}. "
            "Bloom (postpartum AI agent) will speak when the call is answered."
        ),
    )


@router.post("/configure-inbound", tags=["calls"])
async def configure_inbound():
    """
    Re-point the Twilio phone number's inbound webhook at the current server URL.

    Call this after restarting ngrok (which gives you a new public URL) to ensure
    Twilio still routes inbound calls correctly — no need to visit the Twilio console.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, configure_inbound_webhook, settings.twiml_answer_url
    )
    return {
        "status": "configured",
        "phone_number": settings.twilio_from_number,
        "voice_url": result["voice_url"],
        "phone_number_sid": result["phone_number_sid"],
        "message": f"📞 Call {settings.twilio_from_number} to speak with the AI agent.",
    }
