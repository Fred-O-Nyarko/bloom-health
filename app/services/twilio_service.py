"""
Twilio service — initiates outbound calls and configures inbound webhooks.
"""

import logging
from twilio.rest import Client

from app.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_twilio_client() -> Client:
    global _client
    if _client is None:
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def initiate_call(to: str) -> dict:
    """
    Trigger an outbound Twilio call to `to`.
    When the recipient answers, Twilio will POST to /twiml/answer which
    returns TwiML that starts a Media Stream WebSocket to our bridge.
    """
    client = get_twilio_client()
    call = client.calls.create(
        to=to,
        from_=settings.twilio_from_number,
        url=settings.twiml_answer_url,  # Twilio fetches TwiML here on answer
        method="POST",
    )
    logger.info(f"Initiated call SID={call.sid} to={to}")
    return {"call_sid": call.sid, "status": call.status, "to": to}


def configure_inbound_webhook(answer_url: str) -> dict:
    """
    Auto-configure the Twilio phone number so that inbound calls are routed
    to our /twiml/answer endpoint.

    This is idempotent — safe to call on every startup.
    Returns a dict with the phone number SID and the URL that was set.
    """
    client = get_twilio_client()

    # Find the phone number resource matching our configured FROM number
    numbers = client.incoming_phone_numbers.list(
        phone_number=settings.twilio_from_number, limit=1
    )
    if not numbers:
        raise RuntimeError(
            f"No Twilio phone number found matching {settings.twilio_from_number}. "
            "Check your TWILIO_FROM_NUMBER in .env."
        )

    number = numbers[0]
    number.update(
        voice_url=answer_url,
        voice_method="POST",
    )
    logger.info(
        f"Twilio inbound webhook configured: {settings.twilio_from_number} → {answer_url}"
    )
    return {"phone_number_sid": number.sid, "voice_url": answer_url}
