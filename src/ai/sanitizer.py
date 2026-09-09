import re
from typing import List, Optional, Set
import config


# Compile common secret regex patterns
PATTERN_REDACTIONS = [
    # Authorization header
    (re.compile(r'(?i)authorization:\s*[^\s\n\r]+'), 'Authorization: [REDACTED]'),
    # Bearer tokens
    (re.compile(r'(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*'), 'Bearer [REDACTED]'),
    # Telegram bot tokens: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789
    (re.compile(r'\b[0-9]{8,10}:[A-Za-z0-9_-]{35}\b'), '[REDACTED]'),
    # Gemini / Google API keys: AIza...
    (re.compile(r'\bAIza[0-9A-Za-z_-]{30,45}\b'), '[REDACTED]'),
    # OpenAI / OpenRouter / Groq keys: sk-... or gsk_... or or_key_...
    (re.compile(r'\b(?:sk|gsk|or_key)_[A-Za-z0-9_-]{10,}\b'), '[REDACTED]'),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{10,}\b'), '[REDACTED]'),
    # Query parameters containing keys or tokens: key=..., api_key=..., token=...
    (re.compile(r'([?&](?:key|api_key|token|access_token|password|secret|auth)=)([^&\s]+)', re.IGNORECASE), r'\1[REDACTED]'),
    # Connection URLs with credentials (mongodb://user:pass@host..., postgres://...)
    (re.compile(r'mongodb(?:\+srv)?:\/\/[^\s]+'), 'mongodb://[REDACTED]'),
    (re.compile(r'postgres(?:ql)?:\/\/[^\s]+'), 'postgres://[REDACTED]'),
]


def _get_configured_secrets() -> Set[str]:
    """Collect all active secret values from runtime config."""
    secrets = set()

    bot_token = getattr(config, 'BOT_TOKEN', '')
    if bot_token and len(bot_token) > 3:
        secrets.add(bot_token)

    webhook_secret = getattr(config, 'WEBHOOK_SECRET', '')
    if webhook_secret and len(webhook_secret) > 3:
        secrets.add(webhook_secret)

    mongo_uri = getattr(config, 'MONGO_URI', '')
    if mongo_uri and len(mongo_uri) > 3:
        secrets.add(mongo_uri)

    for keys_attr in ('GEMINI_API_KEYS', 'OPENROUTER_API_KEYS', 'GROQ_API_KEYS'):
        keys = getattr(config, keys_attr, [])
        if isinstance(keys, list):
            for k in keys:
                if k and isinstance(k, str) and len(k.strip()) > 3:
                    secrets.add(k.strip())

    return secrets


def sanitize_text(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    """
    Sanitizes raw text or error messages to ensure NO confidential information is exposed.
    Redacts configured secrets, environment secrets, and regex patterns for credentials.
    """
    if not text:
        return text

    sanitized = str(text)

    # 1. Redact explicit configured secrets
    known_secrets = _get_configured_secrets()
    if extra_secrets:
        for s in extra_secrets:
            if s and isinstance(s, str) and len(s.strip()) > 3:
                known_secrets.add(s.strip())

    # Sort secrets by length descending to replace longer secrets first
    sorted_secrets = sorted([s for s in known_secrets if s], key=len, reverse=True)

    for sec in sorted_secrets:
        if sec in sanitized:
            sanitized = sanitized.replace(sec, '[REDACTED]')

    # 2. Redact patterns
    for pattern, replacement in PATTERN_REDACTIONS:
        sanitized = pattern.sub(replacement, sanitized)

    return sanitized
