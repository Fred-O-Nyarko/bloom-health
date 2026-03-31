# 🤱 Postpartum AI Call Agent

A real-time AI-powered phone call agent that provides postpartum care support to new mothers.

**Built with:** FastAPI · Twilio Media Streams · ElevenLabs Conversational AI

---

## How It Works

```
POST /call/outbound {"to": "+1234567890"}
        │
        ▼
Twilio initiates outbound call
        │
        ▼ (recipient answers)
Twilio fetches TwiML from POST /twiml/answer
        │
        ▼
TwiML opens Media Stream WebSocket to /twiml/ws
        │
        ▼
FastAPI bridges audio in real time:

  Phone ──mulaw 8kHz──► Twilio ──WS──► FastAPI ──WS──► ElevenLabs AI
  Phone ◄─mulaw 8kHz── Twilio ◄─WS──  FastAPI ◄─WS──  ElevenLabs AI
                                                              │
                                                   STT → LLM (Claude) → TTS
```

**The AI agent, Amara**, asks about physical recovery, emotional wellbeing, infant feeding, and postpartum mental health — providing warm, evidence-based guidance and escalating to professional care when needed.

---

## Setup

### 1. Clone & Install

```bash
git clone <repo>
cd hackathon
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Where to find it |
|---|---|
| `TWILIO_ACCOUNT_SID` | [Twilio Console](https://console.twilio.com) → Account Info |
| `TWILIO_AUTH_TOKEN` | Twilio Console → Account Info |
| `TWILIO_FROM_NUMBER` | Your Twilio phone number (must have Voice enabled) |
| `ELEVENLABS_API_KEY` | [ElevenLabs](https://elevenlabs.io) → Profile → API Keys |
| `ELEVENLABS_VOICE_ID` | Optional — defaults to Rachel (warm, calm voice) |
| `ELEVENLABS_AGENT_ID` | Leave **empty** on first run — auto-created! |
| `PUBLIC_BASE_URL` | Your ngrok/tunnel URL (see step 3) |

### 3. Expose Your Server (ngrok)

Twilio requires a **publicly accessible URL** — it cannot reach `localhost`.

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL and set `PUBLIC_BASE_URL` in `.env`.

### 4. Run

```bash
uvicorn app.main:app --reload
```

On **first run**, the app will automatically create the ElevenLabs agent and print its ID:

```
========================================================
  ✅ ElevenLabs agent created!
  Agent ID: agent_xxxxxxxxxxxxxxxxxx
  👉 Add this to your .env file:
     ELEVENLABS_AGENT_ID=agent_xxxxxxxxxxxxxxxxxx
========================================================
```

Copy this ID into `.env` so it isn't recreated on every restart.

---

## Usage

### Trigger a Call

```bash
curl -X POST http://localhost:8000/call/outbound \
  -H "Content-Type: application/json" \
  -d '{"to": "+14155552671"}'
```

Response:
```json
{
  "call_sid": "CA...",
  "status": "queued",
  "to": "+14155552671",
  "message": "📞 Call initiated to +14155552671. Amara will speak when the call is answered."
}
```

### API Docs

Visit **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Conversation Flow

Amara, the AI agent, will:

1. **Greet** the mother warmly and ask how she's feeling overall
2. **Physical check** — bleeding, pain, wound healing, sleep, appetite
3. **Emotional check** — mood, anxiety, baby bonding, baby blues vs. PPD screening
4. **Infant feeding** — breastfeeding support or formula feeding guidance
5. **Newborn care** — answer any care questions
6. **Close** — summarize advice, reaffirm support, invite further questions

### Emergency Escalation

If the mother describes:
- Heavy bleeding, fever, severe pain → Amara advises **immediate medical attention**
- Thoughts of self-harm → Amara firmly redirects to **emergency services**

---

## Project Structure

```
hackathon/
├── app/
│   ├── main.py                    # FastAPI app entrypoint
│   ├── config.py                  # Settings from .env
│   ├── prompts/
│   │   └── postpartum.py          # AI agent system prompt & first message
│   ├── routers/
│   │   ├── calls.py               # POST /call/outbound
│   │   └── twiml.py               # POST /twiml/answer + WebSocket /twiml/ws
│   └── services/
│       ├── twilio_service.py      # Twilio client wrapper
│       └── elevenlabs_service.py  # ElevenLabs agent lifecycle + signed URLs
├── .env.example                   # Credentials template
├── requirements.txt
└── README.md
```

---

## Customization

### Change the AI Voice

Browse [ElevenLabs Voice Library](https://elevenlabs.io/voice-library) and update `ELEVENLABS_VOICE_ID` in `.env`.

| Voice | ID | Character |
|---|---|---|
| Rachel (default) | `21m00Tcm4TlvDq8ikWAM` | Warm, calm |
| Domi | `AZnzlk1XvdvUeBnXmlld` | Strong, confident |
| Bella | `EXAVITQu4vr4xnSDxMaL` | Soft, gentle |

### Modify the System Prompt

Edit `app/prompts/postpartum.py`. The agent is recreated each time `ELEVENLABS_AGENT_ID` is cleared from `.env`.

### Use a Different LLM

In `app/services/elevenlabs_service.py`, change the `"llm"` field in `_create_agent()`:
- `"claude-3-5-sonnet"` (default, best quality)
- `"claude-3-5-haiku"` (faster, lower cost)
- `"gpt-4o"` (OpenAI alternative)

---

## Requirements

- Python 3.10+
- Twilio account with Voice-capable phone number
- ElevenLabs account (Starter plan or above for Conversational AI)
- ngrok (or another tunnel) for local development
