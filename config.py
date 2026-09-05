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
