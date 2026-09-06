import uuid
from config import OWNER_IDS, SUDO_USERS


def is_owner(user_id, owner_ids=OWNER_IDS):
    return user_id in owner_ids


def is_owner_or_sudo(user_id, owner_ids=OWNER_IDS, sudo_users=SUDO_USERS):
    return user_id in owner_ids or user_id in sudo_users


async def is_admin(update, context):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ('administrator', 'creator') or is_owner_or_sudo(user.id)


async def safe_reply_error(message_obj, public_text='Gomen ne~ 🥺 Something went wrong! (⁠✿⁠☉⁠｡⁠☉⁠)\nReference ID: {cid}'):
    cid = uuid.uuid4().hex[:12]
    try:
        if hasattr(message_obj, 'reply_text'):
            await message_obj.reply_text(public_text.format(cid=cid))
        elif hasattr(message_obj, 'message') and hasattr(message_obj.message, 'reply_text'):
            await message_obj.message.reply_text(public_text.format(cid=cid))
    except Exception:
        pass
    return cid
