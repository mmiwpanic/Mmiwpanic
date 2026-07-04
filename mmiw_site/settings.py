import os, json
class Settings:
    def __init__(self) -> None:
        self.debug = os.getenv("DEBUG", "0") == "1"
        self.panic_redirect = os.getenv("PANIC_REDIRECT", "https://www.weather.com")
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.office365.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_pass = os.getenv("SMTP_PASS")
        self.vault_key = os.getenv("MMIW_VAULT_KEY")
        # Twilio (optional — SMS delivery disabled gracefully if unset)
        self.twilio_sid = os.getenv("TWILIO_SID")
        self.twilio_token = os.getenv("TWILIO_TOKEN")
        self.twilio_from = os.getenv("TWILIO_FROM")
        # Panic rate limiting (per-user, not per-IP, since a real victim
        # may share an IP with others or be on cellular NAT)
        self.panic_rate_limit = int(os.getenv("PANIC_RATE_LIMIT", "5"))
        self.panic_rate_window_sec = int(os.getenv("PANIC_RATE_WINDOW_SEC", "300"))
        # Anthropic API key for optional AI note-structuring (graceful no-op if unset)
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
settings = Settings()
