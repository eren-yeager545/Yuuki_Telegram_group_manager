import html
import random
import re
from typing import Optional, Tuple, List
from telegram import Update, MessageEntity
from telegram.ext import ContextTypes
import config
from helpers import is_owner
import store

# Feminine name suffixes and common female name tokens
FEMALE_NAME_SUFFIXES = (
    'a', 'ia', 'ina', 'ella', 'elle', 'ette', 'ine', 'ita',
    'ko', 'mi', 'ka', 'na', 'ri', 'ne', 'sa', 'e'
)

COMMON_FEMALE_NAMES = {
    # Anime & Japanese common female names
    'yuki', 'sakura', 'hinata', 'mikasa', 'asuna', 'rem', 'ram', 'emilia', 'zero two',
    'rukia', 'orihime', 'yor', 'anya', 'nezuko', 'shinobu', 'mitsuri', 'kanao',
    'miku', 'rin', 'mai', 'chika', 'kaguya', 'marin', 'megumin', 'aqua', 'darkness',
    'nami', 'robin', 'hancock', 'vivi', 'yamato', 'nobara', 'maki', 'akane', 'ai',
    'aoi', 'yui', 'mio', 'ritsu', 'tsumugi', 'azusa', 'taiga', 'rias', 'akeho',
    # Global / Western common female names
    'alice', 'emma', 'olivia', 'sophia', 'isabella', 'charlotte', 'amelia', 'mia',
    'harper', 'evelyn', 'abigail', 'emily', 'elizabeth', 'mila', 'ella', 'avery',
    'sofia', 'camila', 'aria', 'scarlett', 'victoria', 'madison', 'luna', 'grace',
    'chloe', 'penelope', 'layla', 'riley', 'zoey', 'nora', 'lily', 'eleanor',
    'hannah', 'lillian', 'addison', 'aubrey', 'ellie', 'stella', 'natalie', 'zoe',
    'leah', 'hazel', 'violet', 'aurora', 'savannah', 'audrey', 'brooklyn', 'bella',
    'claire', 'skylar', 'lucy', 'paisley', 'everly', 'anna', 'caroline', 'nova',
    'genesis', 'emilia', 'kennedy', 'samantha', 'maya', 'willow', 'kinsley', 'naomi',
    'aaliyah', 'elena', 'sarah', 'ariana', 'allison', 'gabriella', 'alice', 'madelyn',
    'cora', 'ruby', 'eva', 'serenity', 'autumn', 'adeline', 'hailey', 'gianna',
    'valentina', 'isla', 'eliana', 'quinn', 'nevaeh', 'ivy', 'sadie', 'piper',
    'lydia', 'alexa', 'josephine', 'emery', 'julia', 'delilah', 'arianna', 'vivian',
    'kaylee', 'sophie', 'brielle', 'madeline', 'peyton', 'rylee', 'clara', 'hadley',
    'melanie', 'mackenzie', 'reagan', 'adaline', 'ashley', 'mary', 'jessica', 'amanda',
    'kate', 'katie', 'kat', 'cathy', 'jenny', 'jennifer', 'rachel', 'monica', 'phoebe',
    'lisa', 'rose', 'jisoo', 'jennie', 'wendy', 'irene', 'seulgi', 'joy', 'yeri',
    'sana', 'momo', 'mina', 'tzuyu', 'nayeon', 'jeongyeon', 'jihyo', 'dahyun', 'chaeyoung'
}


def is_likely_female_name(name: Optional[str]) -> bool:
    """
    Heuristic to determine if a display name appears to be a girl's name.
    Checks common names, keywords, and linguistic suffixes.
    """
    if not name:
        return False

    clean = re.sub(r'[^\w\s]', ' ', name.lower()).strip()
    if not clean:
        return False

    words = clean.split()
    first_word = words[0] if words else ''

    # Check against known female names
    if first_word in COMMON_FEMALE_NAMES or clean in COMMON_FEMALE_NAMES:
        return True

    for w in words:
        if w in COMMON_FEMALE_NAMES:
            return True

    # Check female indicator words/prefixes/titles
    female_indicators = {'miss', 'ms', 'mrs', 'queen', 'princess', 'lady', 'girl', 'chan', 'san', 'senpai'}
    if any(ind in words for ind in female_indicators):
        return True

    # Morphological ending check for first name (minimum 3 chars)
    if len(first_word) >= 3:
        for suffix in ('ina', 'ella', 'elle', 'ette', 'ita', 'ia', 'ika', 'ka', 'ko', 'mi', 'na', 'ri'):
            if first_word.endswith(suffix):
                return True

    return False


def render_progress_bar(percentage: int, length: int = 10) -> str:
    """
    Renders an attractive visual progress bar with filled and empty block characters.
    e.g. ▰▰▰▰▰▱▱▱▱▱
    """
    clamped = max(0, min(100, percentage))
    filled_count = round((clamped / 100.0) * length)
    empty_count = length - filled_count
    return ("▰" * filled_count) + ("▱" * empty_count)


def format_mention(user_id: int, name: str, username: Optional[str] = None) -> str:
    """Returns a clickable Telegram HTML mention for a user."""
    safe_name = html.escape(name or "User")
    if user_id > 0:
        return f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    elif username:
        clean_un = username.lstrip('@')
        return f'@{html.escape(clean_un)}'
    return safe_name


class MeterUser:
    def __init__(self, user_id: int, first_name: str, username: Optional[str] = None, is_bot: bool = False):
        self.id = user_id
        self.first_name = first_name or "User"
        self.username = username
        self.is_bot = is_bot

    @property
    def mention(self) -> str:
        return format_mention(self.id, self.first_name, self.username)


async def resolve_single_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[MeterUser]:
    """
    Resolves a target user from:
    1) Reply to a message
    2) Text mention entity
    3) First command argument (@username or numerical user ID)
    Returns None if no target is provided or cannot be parsed.
    """
    msg = update.effective_message
    if not msg:
        return None

    # 1. Reply to message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return MeterUser(u.id, u.first_name or u.full_name or "User", u.username, bool(u.is_bot))

    # 2. Text mention entity
    if msg.entities:
        for ent in msg.entities:
            if ent.type == MessageEntity.TEXT_MENTION and getattr(ent, 'user', None):
                u = ent.user
                return MeterUser(u.id, u.first_name or u.full_name or "User", u.username, bool(u.is_bot))

    # 3. Command arguments (@username or user_id)
    if context.args:
        first_arg = context.args[0].strip()
        if first_arg.startswith('@'):
            clean_un = first_arg.lstrip('@')
            u_info = store.get_user_by_username(clean_un)
            if u_info:
                return MeterUser(u_info[0], u_info[1] or clean_un, u_info[2])
            else:
                return MeterUser(0, clean_un, clean_un)
        elif first_arg.isdigit() or (first_arg.startswith('-') and first_arg[1:].isdigit()):
            uid = int(first_arg)
            u_info = store.get_user_by_id(uid)
            if u_info:
                return MeterUser(u_info[0], u_info[1] or f"User {uid}", u_info[2])
            else:
                return MeterUser(uid, f"User {uid}", None)

    return None


async def get_random_couple(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Tuple[Optional[MeterUser], Optional[MeterUser]]:
    """
    Selects two distinct eligible users from the group chat.
    Filters out bots, the bot itself, and ensures user1 != user2.
    Uses store.get_group_members first, and falls back to get_chat_administrators if needed.
    """
    chat = update.effective_chat
    if not chat or chat.type not in ('group', 'supergroup'):
        return None, None

    bot_id = context.bot.id if context and getattr(context, 'bot', None) else 0

    eligible_pool: List[MeterUser] = []
    seen_ids = set()

    # 1. Fetch from group members recorded in DB
    try:
        db_members = store.get_group_members(chat.id)
        for uid, name, uname, is_b in db_members:
            if is_b or (bot_id and uid == bot_id):
                continue
            if uid not in seen_ids:
                seen_ids.add(uid)
                eligible_pool.append(MeterUser(uid, name or f"User {uid}", uname, False))
    except Exception:
        pass

    # 2. Fetch admins if pool is too small (< 2)
    if len(eligible_pool) < 2:
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            for a in admins:
                u = a.user
                if not u or u.is_bot or (bot_id and u.id == bot_id):
                    continue
                if u.id not in seen_ids:
                    seen_ids.add(u.id)
                    eligible_pool.append(MeterUser(u.id, u.first_name or u.full_name or "User", u.username, False))
        except Exception:
            pass

    # 3. If sender is not in pool, add sender if not bot
    sender = update.effective_user
    if sender and not sender.is_bot and sender.id != bot_id and sender.id not in seen_ids:
        seen_ids.add(sender.id)
        eligible_pool.append(MeterUser(sender.id, sender.first_name or "User", sender.username, False))

    if len(eligible_pool) < 2:
        return None, None

    chosen = random.sample(eligible_pool, 2)
    return chosen[0], chosen[1]


# ---------------------------------------------------------------------------
# Creative Anime-style Yuki Personality Comments
# ---------------------------------------------------------------------------

LOVE_COMMENTS = {
    (0, 20): [
        "Brrr... complete ice age vibes! Maybe stick to being polite strangers desu~ ❄️💔",
        "Aww, not even a tiny spark! Like water and oil, but friendships can still happen desu~! 🌧️",
        "Ouch! Even Yuki's love radar couldn't detect any chemistry here~ 🥶"
    ],
    (21, 40): [
        "Just a slight breeze of warmth! Casual classmates or chill friends at best~ ☕🌸",
        "Ehehe, there's a long road ahead if you want sparks to fly! Don't lose hope~ 🍃",
        "A little awkward silence in the air, but hey, miracles happen in anime! 💫"
    ],
    (41, 60): [
        "Cute friendship potential! A shared coffee or manga chat might unlock more feelings~ 🍵✨",
        "Right in the cozy middle zone! A little push could turn this into something sweet~ 🌸💕",
        "Balanced and sweet! Like two characters who slowly realize they care for each other~ 🎀"
    ],
    (61, 80): [
        "Ooh la la! Definitely sparks crackling in the air desu~! So doki-doki! 💖🔥",
        "Such lovely chemistry! Yuki's heart is skipping a beat just watching you two~ (⁠≧⁠▽⁠≦⁠)🌸",
        "Heavy romantic tension! Who's going to confess first under the cherry blossoms? 🌸💌"
    ],
    (81, 100): [
        "Kyaaa~! Soulmates detected! An anime romance written in the stars desu~! 💍✨💘",
        "100% destined perfection! True love is definitely in full bloom here~ 🌸💖(⁠人⁠*⁠´⁠∀⁠｀⁠)",
        "Legendary compatibility! Even cupid is taking notes from you two desu~! 🏹💕✨"
    ]
}

LOOK_COMMENTS = {
    (0, 20): [
        "Aww, maybe you woke up with bedhead today! Inner beauty is what truly sparkles desu~ 🪞🌸",
        "A bit messy right now, but a quick smile will brighten everything right up! 🌷",
        "Needs a little polish and coffee, but everyone has off days desu~! ☕"
    ],
    (21, 40): [
        "Casual cozy vibes! Looking neat and comfy for a relaxing day at home~ 🛋️✨",
        "Everyday cute! A comfy hoodie look that feels super approachable desu~ 🧸",
        "Simple and sweet! Nothing flashy, just good natural charm~ 🌿"
    ],
    (41, 60): [
        "Looking super sharp and delightful! You definitely catch people's eye in the hallways~ 🌸✨",
        "Pretty and charming! An effortlessly cute everyday aesthetic desu~ 💕",
        "Radiant natural grace! Yuki gives your look a solid high-five! 🎀"
    ],
    (61, 80): [
        "Whoa! Total main character energy right here! Turning heads everywhere you go~ 🌟✨",
        "Dazzling glow-up! You look like you just walked out of a high-fashion shoujo manga! 💃💖",
        "Extremely charming! Your style is on point today desu~ (⁠≧⁠∇⁠≦⁠)🌸"
    ],
    (81, 100): [
        "Kyaaa~! Heavenly perfection! A certified anime idol level beauty desu~! 👑✨💖",
        "Flawless elegance! People stop and stare whenever you walk into the room~ 🌸💎",
        "Absolute masterpiece! The camera loves you and so does everyone else desu~! 📸✨💕"
    ]
}

BRAIN_COMMENTS = {
    (0, 20): [
        "Brain cell loading at 2G speeds... please insert coffee to reboot desu~! ☕🧠",
        "Head empty, no thoughts, just happy vibes floating around~ 🎈☁️",
        "Currently running on 1 single brain cell, but hey, ignorance is bliss desu~! 🌸"
    ],
    (21, 40): [
        "A bit sleepy today! The gears are slowly turning like an old windmill~ ⚙️🍃",
        "Thinking is hard before lunch! Grab a snack and try again desu~ 🍙",
        "Casual thinking mode active! Don't overcomplicate things today~ 💭"
    ],
    (41, 60): [
        "Solid practical intelligence! Common sense is strong with this one desu~ 📚✨",
        "Smart and resourceful! You always figure out a workaround when needed~ 💡",
        "Good logical balance! Not a mad scientist, but definitely sharp enough! 🌸"
    ],
    (61, 80): [
        "Big brain moves! You're playing 4D chess while others play checkers desu~! ♟️🧠✨",
        "Super sharp intellect! Yuki would definitely want you on her study team~ 📖🎓",
        "Impressive deduction skills! You catch details that nobody else sees~ 🔍✨"
    ],
    (81, 100): [
        "GALAXY BRAIN! A true mastermind whose calculations transcend normal reality desu~! 🌌🧠💡",
        "Quantum-level brilliance! Einstein himself would ask for your notes~ 📝✨",
        "Super genius aura! You solve problems before other people even realize they exist desu~! 🌟"
    ]
}

STUPID_COMMENTS = {
    (0, 20): [
        "Zero clown moments detected! Calm, collected, and totally sensible desu~ 🎓✨",
        "Remarkably sane and dignified! You rarely fall for silly tricks~ 🛡️",
        "Pure logic reigns supreme! No goofy monkey business here desu~ 🌸"
    ],
    (21, 40): [
        "Just a pinch of silliness! The kind of cute clumsiness that makes people smile~ 🍌💫",
        "Harmless goofy vibes! Occasionally forgets where the glasses are while wearing them~ 👓",
        "A tiny bit of airhead charm, but overall completely harmless desu~! 🎈"
    ],
    (41, 60): [
        "Certified goofy goober! Prone to walking into screen doors while waving~ 🚪🙃",
        "Medium-grade clown energy! Always keeps the chat entertained with hilarious antics desu~ 🤡",
        "Half mastermind, half chaotic gremlin! Never let them cook unsupervised~ 🍳💥"
    ],
    (61, 80): [
        "High-voltage silliness! Push-pull doors are your greatest arch-nemesis desu~! 🚪🤦",
        "Living life on pure cartoon physics! Where is the instruction manual for this human? 📖🌀",
        "Epic level derpiness! But honestly, life is so much more fun with chaotic clowns around desu~! 🎪✨"
    ],
    (81, 100): [
        "MAXIMUM BRAINROT! Absolutely legendary levels of funny goofy nonsense desu~! 🤪💫🍿",
        "Ascended beyond standard logic! A pure cosmic comedian spreading chaos everywhere~ 🌪️🤡",
        "100% pure unfiltered silly sauce! We love you anyway desu~! (⁠≧⁠▽⁠≦⁠)🌸💖"
    ]
}

CRUSH_COMMENTS = {
    (0, 20): [
        "Friendzoned into another dimension! They see you like a houseplant desu~ 🪴💔",
        "No butterflies here, just friendly tumbleweeds blowing in the wind~ 🌾🍃",
        "Zero romantic interest detected! Safe in the cozy platonic buddy bunker desu~ 🛋️"
    ],
    (21, 40): [
        "A passing glance in the hallway! They know your name, but that's about it~ 🏫🌸",
        "Casual acquaintance tier! Might smile if you say hi, so take the first step desu~ ☕",
        "Mild curiosity! Keep chatting and maybe a spark will ignite~ 💬✨"
    ],
    (41, 60): [
        "They definitely notice you! A secret glance when you're not looking, perhaps? 👀💕",
        "Warm crush potential! You might be living rent-free in their thoughts sometimes~ 💭🌸",
        "Cute mutual vibe! A little bold move could change everything desu~! 💌"
    ],
    (61, 80): [
        "Doki-doki level high! Their heart skips a beat whenever your name pops up desu~! 💓🔥",
        "Heavy crush alert! They definitely check their phone hoping for your notification~ 📱💖",
        "Major feelings in the air! Yuki senses some blushing happening right now desu~ (⁠*⁠´⁠ω⁠｀⁠*⁠)🌸"
    ],
    (81, 100): [
        "HEAD OVER HEELS! Completely and hopelessly infatuated with you desu~! 💘💍✨",
        "Total obsession tier! You are their ultimate anime crush and number one daydream~ 🌸💖(⁠人⁠*⁠´⁠∀⁠｀⁠)",
        "Destined romance! Confess right now, victory is 100% guaranteed desu~! 💌🎉💕"
    ]
}


def pick_tier_comment(comment_dict: dict, score: int) -> str:
    """Selects a random comment based on score percentage range."""
    clamped = max(0, min(100, score))
    for (low, high), comments in comment_dict.items():
        if low <= clamped <= high:
            return random.choice(comments)
    return random.choice(list(comment_dict.values())[-1])


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

async def love_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /love command – Show love compatibility meter (0–100%).
    Supports:
    - /love @username or reply: checks compatibility between sender and target.
    - /love in group without target: selects a random eligible couple from the group.
    """
    msg = update.effective_message
    if not msg:
        return

    sender = update.effective_user
    chat = update.effective_chat
    target = await resolve_single_target(update, context)

    # 1. Single target mode
    if target:
        if target.id == sender.id:
            user_mention = target.mention
            score = random.randint(70, 100)
            bar = render_progress_bar(score)
            text = (
                f"💖 <b>Self-Love Meter</b> 💖\n\n"
                f"👤 <b>Target:</b> {user_mention}\n"
                f"📊 <b>Score:</b> {score}%\n"
                f"[{bar}]\n\n"
                f"🌸 <i>Loving yourself is the greatest romance of all desu~! Always shine bright!</i> ✨ (⁠人⁠*⁠´⁠∀⁠｀⁠)"
            )
            await msg.reply_html(text)
            return

        sender_mention = format_mention(sender.id, sender.first_name, sender.username)
        target_mention = target.mention

        score = random.randint(5, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(LOVE_COMMENTS, score)

        text = (
            f"💖 <b>Love Compatibility Meter</b> 💖\n\n"
            f"👩‍❤️‍👨 <b>Couple:</b> {sender_mention} ❤️ {target_mention}\n"
            f"📊 <b>Compatibility:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 2. Random couple mode (Group chat without target)
    if chat and chat.type in ('group', 'supergroup'):
        user1, user2 = await get_random_couple(update, context)
        if not user1 or not user2:
            await msg.reply_html("🌸 <i>Uwaa~ I need at least two members in this group to calculate a love match!</i> 💕")
            return

        score = random.randint(10, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(LOVE_COMMENTS, score)

        text = (
            f"💖 <b>Random Group Love Match!</b> 💖\n\n"
            f"👩‍❤️‍👨 <b>Selected Couple:</b> {user1.mention} ❤️ {user2.mention}\n"
            f"📊 <b>Compatibility:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 3. Private chat with no arguments
    score = random.randint(60, 100)
    bar = render_progress_bar(score)
    user_mention = format_mention(sender.id, sender.first_name, sender.username)
    text = (
        f"💖 <b>Yuki's Love Meter for You!</b> 💖\n\n"
        f"👤 <b>Beloved Member:</b> {user_mention}\n"
        f"📊 <b>Affection Level:</b> {score}%\n"
        f"[{bar}]\n\n"
        f"🌸 <i>Yuki loves having you around! Thank you for being such a wonderful friend desu~!</i> 💕 (⁠≧⁠▽⁠≦⁠)"
    )
    await msg.reply_html(text)


async def look_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /look command – Show a looks meter (0–100%).
    Owner ID Special Configuration:
    - Always show INFINITY instead of a percentage.
    - Add a cool, powerful comment.
    Example: Looks: ♾️ INFINITY — Some legends are simply beyond measurement.
    """
    msg = update.effective_message
    if not msg:
        return

    sender = update.effective_user
    chat = update.effective_chat
    target = await resolve_single_target(update, context)

    # 1. Single target mode
    if target:
        is_target_owner = is_owner(target.id, config.OWNER_IDS)
        if is_target_owner:
            bar = "▰" * 10
            text = (
                f"✨ <b>Looks Meter</b> ✨\n\n"
                f"👤 <b>Target:</b> {target.mention}\n"
                f"📊 <b>Looks:</b> ♾️ <b>INFINITY</b>\n"
                f"[{bar}]\n\n"
                f"👑 <i>Some legends are simply beyond measurement. Pure divine aura and perfection!</i> ⚡🌟"
            )
            await msg.reply_html(text)
            return

        score = random.randint(15, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(LOOK_COMMENTS, score)

        text = (
            f"✨ <b>Looks Meter</b> ✨\n\n"
            f"👤 <b>Target:</b> {target.mention}\n"
            f"📊 <b>Charm Score:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 2. Random couple mode (Group chat without target)
    if chat and chat.type in ('group', 'supergroup'):
        user1, user2 = await get_random_couple(update, context)
        if not user1 or not user2:
            await msg.reply_html("🌸 <i>Uwaa~ I need at least two members in this group to compare looks!</i> ✨")
            return

        # Check owner presence in couple
        u1_owner = is_owner(user1.id, config.OWNER_IDS)
        u2_owner = is_owner(user2.id, config.OWNER_IDS)

        if u1_owner or u2_owner:
            bar = "▰" * 10
            text = (
                f"✨ <b>Random Duo Looks Showcase!</b> ✨\n\n"
                f"👥 <b>Featured Members:</b> {user1.mention} & {user2.mention}\n"
                f"📊 <b>Combined Style:</b> ♾️ <b>INFINITY</b>\n"
                f"[{bar}]\n\n"
                f"👑 <i>A legendary presence graces this duo! Their radiance eclipses the stars desu~!</i> ⚡🌟"
            )
            await msg.reply_html(text)
            return

        score = random.randint(30, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(LOOK_COMMENTS, score)

        text = (
            f"✨ <b>Random Duo Looks Showcase!</b> ✨\n\n"
            f"👥 <b>Featured Members:</b> {user1.mention} & {user2.mention}\n"
            f"📊 <b>Duo Charm Score:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 3. Private chat with no arguments -> evaluates sender
    is_sender_owner = is_owner(sender.id, config.OWNER_IDS)
    if is_sender_owner:
        bar = "▰" * 10
        text = (
            f"✨ <b>Looks Meter</b> ✨\n\n"
            f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
            f"📊 <b>Looks:</b> ♾️ <b>INFINITY</b>\n"
            f"[{bar}]\n\n"
            f"👑 <i>Some legends are simply beyond measurement. Pure divine aura and perfection!</i> ⚡🌟"
        )
        await msg.reply_html(text)
        return

    score = random.randint(40, 100)
    bar = render_progress_bar(score)
    comment = pick_tier_comment(LOOK_COMMENTS, score)
    text = (
        f"✨ <b>Looks Meter</b> ✨\n\n"
        f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
        f"📊 <b>Charm Score:</b> {score}%\n"
        f"[{bar}]\n\n"
        f"💬 <i>{comment}</i>"
    )
    await msg.reply_html(text)


async def brain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /brain command – Show an intelligence meter (0–100%).
    Owner ID Special Configuration:
    - Always show above 80% (81–100%).
    - Generate a cool intelligence-related comment.
    """
    msg = update.effective_message
    if not msg:
        return

    sender = update.effective_user
    chat = update.effective_chat
    target = await resolve_single_target(update, context)

    # 1. Single target mode
    if target:
        is_target_owner = is_owner(target.id, config.OWNER_IDS)
        if is_target_owner:
            score = random.randint(95, 100)
            bar = render_progress_bar(score)
            text = (
                f"🧠 <b>Intelligence Meter</b> 🧠\n\n"
                f"👤 <b>Target:</b> {target.mention}\n"
                f"📊 <b>IQ Score:</b> {score}%\n"
                f"[{bar}]\n\n"
                f"⚡ <i>A supreme mind of visionary strategic genius. Boundless wisdom that commands reality!</i> 🌌🧠"
            )
            await msg.reply_html(text)
            return

        score = random.randint(5, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(BRAIN_COMMENTS, score)

        text = (
            f"🧠 <b>Intelligence Meter</b> 🧠\n\n"
            f"👤 <b>Target:</b> {target.mention}\n"
            f"📊 <b>Brainpower:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 2. Random couple mode (Group chat without target)
    if chat and chat.type in ('group', 'supergroup'):
        user1, user2 = await get_random_couple(update, context)
        if not user1 or not user2:
            await msg.reply_html("🌸 <i>Uwaa~ I need at least two members in this group to test combined brainpower!</i> 🧠")
            return

        u1_owner = is_owner(user1.id, config.OWNER_IDS)
        u2_owner = is_owner(user2.id, config.OWNER_IDS)

        if u1_owner or u2_owner:
            score = random.randint(90, 100)
            bar = render_progress_bar(score)
            text = (
                f"🧠 <b>Random Brainpower Duo!</b> 🧠\n\n"
                f"👥 <b>Think Tank:</b> {user1.mention} & {user2.mention}\n"
                f"📊 <b>Combined Intellect:</b> {score}%\n"
                f"[{bar}]\n\n"
                f"⚡ <i>Elevated by a supreme strategic genius! This duo's calculations leave the universe in awe!</i> 🌌🧠"
            )
            await msg.reply_html(text)
            return

        score = random.randint(15, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(BRAIN_COMMENTS, score)

        text = (
            f"🧠 <b>Random Brainpower Duo!</b> 🧠\n\n"
            f"👥 <b>Think Tank:</b> {user1.mention} & {user2.mention}\n"
            f"📊 <b>Combined Intellect:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 3. Private chat with no arguments -> evaluates sender
    is_sender_owner = is_owner(sender.id, config.OWNER_IDS)
    if is_sender_owner:
        score = random.randint(95, 100)
        bar = render_progress_bar(score)
        text = (
            f"🧠 <b>Intelligence Meter</b> 🧠\n\n"
            f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
            f"📊 <b>IQ Score:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"⚡ <i>A supreme mind of visionary strategic genius. Boundless wisdom that commands reality!</i> 🌌🧠"
        )
        await msg.reply_html(text)
        return

    score = random.randint(20, 100)
    bar = render_progress_bar(score)
    comment = pick_tier_comment(BRAIN_COMMENTS, score)
    text = (
        f"🧠 <b>Intelligence Meter</b> 🧠\n\n"
        f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
        f"📊 <b>Brainpower:</b> {score}%\n"
        f"[{bar}]\n\n"
        f"💬 <i>{comment}</i>"
    )
    await msg.reply_html(text)


async def stupid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stupid command – Show a funny stupidity meter (0–100%).
    Owner ID Special Configuration:
    - Never insult the owner.
    - Instead, respond with creative lines such as:
      "An intelligent person can never be stupid. Some minds are simply beyond ordinary calculations."
    """
    msg = update.effective_message
    if not msg:
        return

    sender = update.effective_user
    chat = update.effective_chat
    target = await resolve_single_target(update, context)

    # 1. Single target mode
    if target:
        is_target_owner = is_owner(target.id, config.OWNER_IDS)
        if is_target_owner:
            bar = "▱" * 10
            text = (
                f"🤡 <b>Stupidity Meter</b> 🤡\n\n"
                f"👤 <b>Target:</b> {target.mention}\n"
                f"📊 <b>Score:</b> 0% (Immune)\n"
                f"[{bar}]\n\n"
                f"✨ <i>An intelligent person can never be stupid. Some minds are simply beyond ordinary calculations.</i> 🛡️🧠"
            )
            await msg.reply_html(text)
            return

        score = random.randint(0, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(STUPID_COMMENTS, score)

        text = (
            f"🤡 <b>Funny Stupidity Meter</b> 🤡\n\n"
            f"👤 <b>Target:</b> {target.mention}\n"
            f"📊 <b>Goofiness Level:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 2. Random couple mode (Group chat without target)
    if chat and chat.type in ('group', 'supergroup'):
        user1, user2 = await get_random_couple(update, context)
        if not user1 or not user2:
            await msg.reply_html("🌸 <i>Uwaa~ I need at least two members in this group to test goofy synergy!</i> 🤡")
            return

        u1_owner = is_owner(user1.id, config.OWNER_IDS)
        u2_owner = is_owner(user2.id, config.OWNER_IDS)

        if u1_owner or u2_owner:
            bar = "▱" * 10
            text = (
                f"🤡 <b>Random Goofiness Duo!</b> 🤡\n\n"
                f"👥 <b>Comedy Partners:</b> {user1.mention} & {user2.mention}\n"
                f"📊 <b>Duo Score:</b> 0% (Protected)\n"
                f"[{bar}]\n\n"
                f"✨ <i>An intelligent person can never be stupid. Some minds are simply beyond ordinary calculations!</i> 🛡️👑"
            )
            await msg.reply_html(text)
            return

        score = random.randint(5, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(STUPID_COMMENTS, score)

        text = (
            f"🤡 <b>Random Goofiness Duo!</b> 🤡\n\n"
            f"👥 <b>Comedy Partners:</b> {user1.mention} & {user2.mention}\n"
            f"📊 <b>Duo Goofiness:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 3. Private chat with no arguments -> evaluates sender
    is_sender_owner = is_owner(sender.id, config.OWNER_IDS)
    if is_sender_owner:
        bar = "▱" * 10
        text = (
            f"🤡 <b>Stupidity Meter</b> 🤡\n\n"
            f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
            f"📊 <b>Score:</b> 0% (Immune)\n"
            f"[{bar}]\n\n"
            f"✨ <i>An intelligent person can never be stupid. Some minds are simply beyond ordinary calculations.</i> 🛡️🧠"
        )
        await msg.reply_html(text)
        return

    score = random.randint(10, 100)
    bar = render_progress_bar(score)
    comment = pick_tier_comment(STUPID_COMMENTS, score)
    text = (
        f"🤡 <b>Funny Stupidity Meter</b> 🤡\n\n"
        f"👤 <b>Target:</b> {format_mention(sender.id, sender.first_name, sender.username)}\n"
        f"📊 <b>Goofiness Level:</b> {score}%\n"
        f"[{bar}]\n\n"
        f"💬 <i>{comment}</i>"
    )
    await msg.reply_html(text)


async def crush_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /crush command – Show a crush compatibility meter (0–100%).
    Owner ID Special Configuration:
    - If the target's display name appears to be a girl's name, always generate a score above 90% (91–100%).
    - Make the response playful and creative.
    """
    msg = update.effective_message
    if not msg:
        return

    sender = update.effective_user
    chat = update.effective_chat
    target = await resolve_single_target(update, context)

    # 1. Single target mode
    if target:
        is_sender_owner = is_owner(sender.id, config.OWNER_IDS)
        target_is_girl = is_likely_female_name(target.first_name)

        if is_sender_owner and target_is_girl:
            score = random.randint(92, 100)
            bar = render_progress_bar(score)
            text = (
                f"💘 <b>Crush Compatibility Meter</b> 💘\n\n"
                f"👩‍❤️‍👨 <b>Match:</b> {format_mention(sender.id, sender.first_name, sender.username)} & {target.mention}\n"
                f"📊 <b>Crush Score:</b> {score}%\n"
                f"[{bar}]\n\n"
                f"🌸 <i>Kyaaa~! A legendary heart-fluttering romance in full bloom! Her eyes light up every time you smile, commander~!</i> 💕✨(⁠人⁠*⁠´⁠∀⁠｀⁠)"
            )
            await msg.reply_html(text)
            return

        score = random.randint(5, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(CRUSH_COMMENTS, score)

        text = (
            f"💘 <b>Crush Compatibility Meter</b> 💘\n\n"
            f"👩‍❤️‍👨 <b>Match:</b> {format_mention(sender.id, sender.first_name, sender.username)} & {target.mention}\n"
            f"📊 <b>Crush Score:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 2. Random couple mode (Group chat without target)
    if chat and chat.type in ('group', 'supergroup'):
        user1, user2 = await get_random_couple(update, context)
        if not user1 or not user2:
            await msg.reply_html("🌸 <i>Uwaa~ I need at least two members in this group to test secret crush vibes!</i> 💘")
            return

        u1_owner = is_owner(user1.id, config.OWNER_IDS)
        u2_owner = is_owner(user2.id, config.OWNER_IDS)

        # Check if owner + girl name in couple
        if (u1_owner and is_likely_female_name(user2.first_name)) or (u2_owner and is_likely_female_name(user1.first_name)):
            score = random.randint(92, 100)
            bar = render_progress_bar(score)
            text = (
                f"💘 <b>Secret Group Crush Reveal!</b> 💘\n\n"
                f"👩‍❤️‍👨 <b>Secret Admirers:</b> {user1.mention} & {user2.mention}\n"
                f"📊 <b>Crush Affinity:</b> {score}%\n"
                f"[{bar}]\n\n"
                f"🌸 <i>Kyaaa~! Pure anime magic! The chemistry is undeniable and completely off the charts desu~!</i> 💕✨(⁠人⁠*⁠´⁠∀⁠｀⁠)"
            )
            await msg.reply_html(text)
            return

        score = random.randint(10, 100)
        bar = render_progress_bar(score)
        comment = pick_tier_comment(CRUSH_COMMENTS, score)

        text = (
            f"💘 <b>Secret Group Crush Reveal!</b> 💘\n\n"
            f"👩‍❤️‍👨 <b>Secret Admirers:</b> {user1.mention} & {user2.mention}\n"
            f"📊 <b>Crush Affinity:</b> {score}%\n"
            f"[{bar}]\n\n"
            f"💬 <i>{comment}</i>"
        )
        await msg.reply_html(text)
        return

    # 3. Private chat with no arguments
    score = random.randint(50, 100)
    bar = render_progress_bar(score)
    user_mention = format_mention(sender.id, sender.first_name, sender.username)
    text = (
        f"💘 <b>Yuki's Secret Crush Meter</b> 💘\n\n"
        f"👤 <b>Admirer:</b> {user_mention}\n"
        f"📊 <b>Charm Magnetism:</b> {score}%\n"
        f"[{bar}]\n\n"
        f"🌸 <i>Ehehe~ You definitely have that charismatic secret sparkle that makes people blush! Keep dazzling everyone desu~!</i> 💕 (⁠*⁠´⁠ω⁠｀⁠*⁠)"
    )
    await msg.reply_html(text)
