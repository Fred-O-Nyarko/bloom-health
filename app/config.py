from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str

    # ElevenLabs
    elevenlabs_api_key: str
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel — warm & calm
    elevenlabs_agent_id: str = ""

    # Server
    public_base_url: str = "http://localhost:8000"

    # Bloom portal — post-call ingestion target (Area 2)
    portal_base_url: str = "http://localhost:8001"

    # Anthropic — used for post-call severity classification.
    # Optional: if absent, severity falls back to rule-based detection.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    @property
    def twiml_answer_url(self) -> str:
        return f"{self.public_base_url}/twiml/answer"

    @property
    def twiml_websocket_url(self) -> str:
        # Twilio Media Streams requires wss:// — swap http(s) scheme
        base = self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/twiml/ws"


settings = Settings()
