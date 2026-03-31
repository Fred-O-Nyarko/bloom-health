"""
Twilio service — initiates outbound calls.
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
