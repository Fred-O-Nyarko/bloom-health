"""
POST /call/outbound — trigger an AI postpartum care call to a phone number.
"""

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.services.twilio_service import initiate_call
from app.state import call_metadata

router = APIRouter(prefix="/call", tags=["calls"])


class CallRequest(BaseModel):
    to: str  # E.164 format, e.g. "+14155552671"
    patient_name: str = "there"  # Used in ElevenLabs first message dynamic variable
    days_since_delivery: int = 0  # Days since the mother gave birth

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

    **Prerequisites:**
    - Twilio credentials configured in `.env`
    - ElevenLabs agent created (auto-created on first run)
    - Server accessible via a public URL (ngrok, Cloudflare Tunnel, etc.)
    """
    result = initiate_call(body.to)

    # Store call variables so the TwiML webhook and WebSocket bridge can use them
    call_metadata[result["call_sid"]] = {
        "patient_name": body.patient_name,
        "days_since_delivery": str(body.days_since_delivery),
    }

    return CallResponse(
        **result,
        message=(
            f"📞 Call initiated to {body.to}. "
            "Abena (postpartum AI agent) will speak when the call is answered."
        ),
    )

