import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
OWNER_IDS = [int(x) for x in os.getenv('OWNER_IDS', '').split(',') if x.strip().isdigit()]
OWNER_ID = OWNER_IDS[0] if OWNER_IDS else 0
SUDO_USERS = [int(x) for x in os.getenv('SUDO_USERS', '').split(',') if x.strip().isdigit()]
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '0') or 0)
QUIZ_INTERVAL_SECONDS = int(os.getenv('QUIZ_INTERVAL_SECONDS', '3600') or 3600)
SEEN_UPDATE_COOLDOWN_SECONDS = int(os.getenv('SEEN_UPDATE_COOLDOWN_SECONDS', '21600') or 21600)

SUPPORT_GROUP_URL = os.getenv('SUPPORT_GROUP_URL', 'https://t.me/your_support_group')
UPDATE_CHANNEL_URL = os.getenv('UPDATE_CHANNEL_URL', 'https://t.me/your_update_channel')

PORT = int(os.getenv('PORT', '8080') or 8080)
WEBHOOK_URL = os.getenv('WEBHOOK_URL', os.getenv('RENDER_EXTERNAL_URL', ''))
WEBHOOK_PATH = os.getenv('WEBHOOK_PATH', BOT_TOKEN)
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')

MONGO_URI = os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or os.getenv('MONGODB_URI') or ''
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME', 'yuuki_bot')

# AI Configuration
AI_ENABLED = os.getenv('AI_ENABLED', 'true').lower() in ('true', '1', 'yes')
AI_PROVIDER_ORDER = [x.strip().lower() for x in os.getenv('AI_PROVIDER_ORDER', 'gemini,groq,openrouter').split(',') if x.strip()]
raw_gemini_keys = os.getenv('GEMINI_API_KEYS') or os.getenv('GEMINI_API_KEY') or ''
GEMINI_API_KEYS = [x.strip() for x in raw_gemini_keys.split(',') if x.strip()]
GROQ_API_KEYS = [x.strip() for x in os.getenv('GROQ_API_KEYS', '').split(',') if x.strip()]
raw_openrouter_keys = os.getenv('OPENROUTER_API_KEYS') or os.getenv('OPENROUTER_API_KEY') or ''
OPENROUTER_API_KEYS = [x.strip() for x in raw_openrouter_keys.split(',') if x.strip()]
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip() or 'gemini-2.5-flash'
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'google/gemma-4-31b').strip() or 'google/gemma-4-31b'

AI_MAX_CONTEXT_MESSAGES = int(os.getenv('AI_MAX_CONTEXT_MESSAGES', '10') or 10)
AI_MAX_OUTPUT_TOKENS = int(os.getenv('AI_MAX_OUTPUT_TOKENS', '150') or 150)
AI_REQUEST_TIMEOUT = float(os.getenv('AI_REQUEST_TIMEOUT', '20') or 20)
AI_USER_COOLDOWN = float(os.getenv('AI_USER_COOLDOWN', '5') or 5)
AI_CHAT_COOLDOWN = float(os.getenv('AI_CHAT_COOLDOWN', '2') or 2)
AI_MAX_CONCURRENT_REQUESTS = int(os.getenv('AI_MAX_CONCURRENT_REQUESTS', '10') or 10)
