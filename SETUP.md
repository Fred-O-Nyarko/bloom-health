# Bloom Call Service — Setup

FastAPI service that bridges Twilio (phone calls) ↔ ElevenLabs (conversational AI). Receives `POST /call/outbound` from the Bloom portal, places the call via Twilio, opens a Media Stream WebSocket when the recipient answers, and brokers audio to/from ElevenLabs Conversational AI in real time. After the call ends, it classifies severity (rule-based, or Claude if `ANTHROPIC_API_KEY` is set) and posts the verdict back to the portal.

This service is **only** needed if you want to place real phone calls. The portal demos cleanly without it (using seeded data and the `/alerts` page). Skip this guide if you're not running live calls.

---

## Prerequisites

- **Python 3.12 or 3.13.** On 3.13, `audioop-lts` is auto-installed (the stdlib `audioop` was removed). Verify: `python3 --version`.
- **Twilio account** — Account SID, Auth Token, and a Twilio phone number with Voice capability.
  - Trial accounts work, but each destination number must be added to **Verified Caller IDs** in the console first.
  - Trial accounts also play a "*you have received a trial account call, press any key to accept*" preamble — the recipient has to press a key.
- **ElevenLabs account** — API key. Free tier works briefly but burns out quickly during demos; the cheapest paid tier ($5/mo) gives meaningful headroom.
- **Public HTTPS URL** pointing at this service's port (`ngrok`, Cloudflare Tunnel, or similar). Twilio cannot reach `localhost` — it has to fetch TwiML from a publicly-resolvable URL.
- **(Optional) Anthropic API key** for the post-call severity classifier. Without it, the classifier falls back to a rule-based scan that catches obvious red-flag phrases.

---

## Setup

```sh
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — every Twilio + ElevenLabs field is required
```

### `.env` reference

| Variable | Required | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | yes | Starts with `AC...`. Console home → Account Info. |
| `TWILIO_AUTH_TOKEN` | yes | Same Account Info card. Treat as a secret. |
| `TWILIO_FROM_NUMBER` | yes | E.164 (`+1...`). Phone Numbers → Manage → Active numbers. Buy one if your account has none. |
| `ELEVENLABS_API_KEY` | yes | ElevenLabs profile → API Keys. |
| `ELEVENLABS_VOICE_ID` | no | Defaults to Rachel (`21m00Tcm4TlvDq8ikWAM`). Browse other voices at elevenlabs.io/voice-library. |
| `ELEVENLABS_AGENT_ID` | no | Leave empty on first run — the service will auto-create an agent and log the ID; paste it back into `.env` to reuse it on subsequent runs. |
| `PUBLIC_BASE_URL` | yes | Your public tunnel URL — e.g. `https://abc123.ngrok-free.dev`. **Must be HTTPS.** |
| `PORTAL_BASE_URL` | no | Defaults to `http://localhost:8001`. Where to POST post-call severity verdicts. |
| `ANTHROPIC_API_KEY` | no | If set, post-call severity classification uses Claude. Otherwise falls back to rule-based scanning. |
| `ANTHROPIC_MODEL` | no | Defaults to `claude-sonnet-4-6`. |

---

## Twilio side — verify a destination number (trial accounts)

Trial accounts can only call **verified** numbers. Verify the demo phone before placing your first call:

1. **Console → Phone Numbers → Manage → [Verified Caller IDs](https://console.twilio.com/us1/develop/phone-numbers/manage/verified) → Add a new Caller ID.**
2. Pick country (e.g. **Ghana (+233)**) and type the **9-digit local number, no leading 0** (e.g. `203287763`, not `0203287763`).
3. **Send verification code via: Call** (Ghana SMS is on Twilio's block-list — SMS verification will fail).
4. Twilio dials the number and reads a 6-digit code. Type it into the dialog.
5. Confirm via API:
   ```sh
   SID=$(grep TWILIO_ACCOUNT_SID .env | cut -d= -f2)
   TOKEN=$(grep TWILIO_AUTH_TOKEN .env | cut -d= -f2)
   curl -s -u "$SID:$TOKEN" \
     "https://api.twilio.com/2010-04-01/Accounts/$SID/OutgoingCallerIds.json" \
     | python3 -m json.tool | grep phone_number
   ```
   The stored `phone_number` must be the clean 12-digit `+233...` form. If it shows `+2330...` (extra 0), delete and re-verify — Twilio compares the dial target against this string and a leading-0 mismatch causes error 21219 ("number is unverified") even though the console looks correct.

---

## Public URL — ngrok

Twilio fetches TwiML from `PUBLIC_BASE_URL/twiml/answer` when calls are answered, and opens a WebSocket to `PUBLIC_BASE_URL/twiml/ws`. Both must be reachable from the public internet.

```sh
# In a separate terminal
ngrok http 8000
```

Copy the `https://...ngrok-free.dev` URL it prints. Paste into `.env`:
```
PUBLIC_BASE_URL=https://your-tunnel.ngrok-free.dev
```

ngrok URLs change on every restart — update `.env` and restart the service whenever the tunnel rotates. The service auto-configures Twilio's inbound webhook on startup against `PUBLIC_BASE_URL`, so no manual Twilio console fiddling is needed.

---

## Run

```sh
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

On startup you should see:
```
✅ Ready — call <your-twilio-number> to speak with the agent, or POST /call/outbound to initiate a call
Inbound webhook set: <your-twilio-number> → https://<tunnel>/twiml/answer
```

If the inbound webhook line is missing, run `curl -X POST http://localhost:8000/call/configure-inbound` to retry.

---

## Verify

**1. Service is up:**
```sh
curl -s http://127.0.0.1:8000/docs -o /dev/null -w "%{http_code}\n"
# → 200
```

**2. Public tunnel works:**
```sh
curl -s "$PUBLIC_BASE_URL/docs" -o /dev/null -w "%{http_code}\n"
# → 200 (NOT an ngrok interstitial page)
```
If you get an ngrok warning interstitial, free-tier ngrok is fronting an HTML splash that breaks Twilio. Either pay for ngrok (removes the splash) or use Cloudflare Tunnel.

**3. End-to-end call** (from the Bloom portal): open a mother profile, click **Place call**. You should see, in order:
```
POST /call/outbound 200
POST /twiml/answer  (~5s later, when the phone is answered)
Twilio Media Stream WebSocket connected
ElevenLabs WebSocket connected
[Bloom]: Hello <name>, it's Bloom calling from <hospital>...
```
And on hangup:
```
Severity (rule|Claude): L<n> — <summary>
Posted post-call ingest to portal (call_sid=..., severity=L<n>)
```

---

## Architecture (one-paragraph)

`app/main.py` is the entry point. `app/routers/calls.py` exposes `POST /call/outbound` — accepts a phone number plus a rich `mother_context` blob, stashes the context keyed by Twilio's CallSid in `app/state.py`, and asks Twilio to dial. `app/routers/twiml.py` handles Twilio's TwiML callback (`/twiml/answer`) and the bidirectional Media Stream WebSocket (`/twiml/ws`) — it converts mulaw 8kHz ↔ PCM 16kHz audio, streams to/from ElevenLabs, and accumulates the transcript. `app/prompts/postpartum.py` renders a 5-phase clinically-bounded system prompt per call, personalized by `mother_context`. On call end, `app/services/severity.py` classifies severity (Anthropic if available, rule-based otherwise) and `app/services/post_call.py` POSTs the verdict back to the portal.

---

## Common issues

**`This request exceeds your quota limit.` (WebSocket close 1002).** ElevenLabs API quota exhausted. Check via `curl -s "https://api.elevenlabs.io/v1/user/subscription" -H "xi-api-key: $ELEVENLABS_API_KEY"`. Fix: upgrade plan, swap to a fresh API key, or wait for monthly reset.

**Call status `no-answer`, `duration: 0`.** Twilio rang for ~30s and the recipient phone never went off-hook. Check: phone is on, off silent/DND, and reachable. Trial-account US numbers calling Ghana are also commonly filtered by Ghanaian carriers — try a UK Twilio number instead, or upgrade the Twilio account.

**Twilio error 21219 ("number is unverified").** The destination isn't in your verified caller IDs, OR it is but with a different format (leading 0 issue — see Twilio setup section above).

**Phone rings, you pick up, line goes silent.** Twilio reached you but the WebSocket bridge to ElevenLabs failed. Most common cause: ngrok interstitial page on free tier blocking Twilio's TwiML fetch, OR `audioop` missing on Python 3.13 (`pip install audioop-lts`).

**Call connects, AI greets, then drops mid-conversation.** ElevenLabs quota cap mid-call. See first issue above.

**`POST /call/outbound 200` but no `POST /twiml/answer` ever arrives.** Twilio accepted the request but couldn't reach your `PUBLIC_BASE_URL`, or the recipient never answered. Run the call-status curl in the portal's troubleshooting section, or check Twilio Console → Monitor → Logs → Calls.
