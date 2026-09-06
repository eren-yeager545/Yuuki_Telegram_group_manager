# Yuuki : Telegram Group Manager 🌸✨

**Yuuki** is a cute, stylish, and super powerful Telegram group manager inspired by a cheerful 17-year-old college student from Tokyo! 🌸✨ Built for communities that want top-tier moderation, automated channel logging, flexible note management, and a lovely member experience without bloat.

Instead of being just a simple ban bot, Yuuki is a complete group control system: she helps admins manage spam, organize rules and button notes, purge bad user messages, log real-time events to connected channels, issue Reference IDs on command errors, and keep community engagement high! 💕

## Why Yuuki stands out

- **Cute Tokyo Student Persona**: Every response is sweet, polite, and kaomoji-adorned (⁠≧⁠▽⁠≦⁠)🌸!
- **User Purging & Delete-Ban (`/dban`)**: Purge all messages from a selected user (`/purge`), or ban a user and wipe all their messages from the group simultaneously (`/dban`).
- **Real-Time Channel Logging**: Live logger support for connected group log channels and global channels when users start the bot or add the bot to groups. Parses channel usernames (`@channel`), IDs, and Telegram invite/channel links.
- **Error Reference IDs**: Automatic Reference ID tracking whenever a command encounters an issue or exception.
- **Dual Storage Engine**: Seamlessly switches between local SQLite (`bot.db`) and MongoDB based on environment variables.

## Core features

### Moderation toolkit
- **Ban, Unban, Kick, Mute, Unmute, Warn, Clear Warns**: Complete restriction and warning controls.
- **User Message Purging (`/purge`)**: Delete all messages from a selected user (via reply or mention/ID) in the group.
- **Delete-Ban (`/dban`)**: Ban a user and purge all their messages from the chat at once.
- **Moderation Logs (`/modlog`)**: Audit trail for admin actions.

### Real-Time Logger System
- Automatically posts formatted logger notifications when users start the bot or when the bot is added to or removed from a group.
- Supports connected group log channels and global logger channels with username, ID, and link parsing (`t.me/...`).

### Antiflood and Protection
- Detect repeated spam bursts automatically.
- Choose flood actions: delete, warn, mute, kick, ban, tempmute, or tempban.
- Extend protection with broad content locks for links, media, stickers, gifs, polls, forwards, bots, voice messages, video notes, documents, photos, videos, audio, commands, hashtags, mentions, inline references, and contacts.

### Notes, Filters, and Button Notes
- Save reusable notes for rules, links, FAQs, staff info, and announcements.
- Automatic keyword filters for instant replies.
- Create button notes with clickable URLs.

### Smarter Welcomes
- Enable custom welcome and goodbye messages with placeholders (`{first}`, `{fullname}`, `{chatname}`).
- Welcome buttons and optional auto-mute for new members.

### Reports and Blacklists
- Allow members to report problematic messages directly to group admins (`/report`).
- Blacklist triggers for blocked words with automated actions.

### Federation System
- Connect moderation policy across multiple groups using federations (`/newfed`, `/fedban`, `/joinfed`).

### Owner and Admin Flexibility
- Dual full owners via `OWNER_IDS` and sudo users via `SUDO_USERS`.

## Setup

1. Clone the repository.
2. Create a virtual environment and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your values.
4. Run the bot:
   ```bash
   python bot.py
   ```

## Environment

```env
BOT_TOKEN=your_telegram_bot_token_here
OWNER_IDS=111111111,222222222
SUDO_USERS=
LOG_CHANNEL_ID=
QUIZ_INTERVAL_SECONDS=3600
SEEN_UPDATE_COOLDOWN_SECONDS=21600
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGO_DB_NAME=yuuki_bot
```

## Main commands

### User commands
- `/start`, `/help`, `/ping`, `/rules`, `/stats`, `/privacy`
- `/get`, `/notes`, `/filters`
- `/report`, `/id`, `/info`, `/admins`, `/locks`, `/warned`
- `/fedinfo`, `/fedstat`, `/myfeds`, `/blacklists`

### Moderation commands
- `/ban`, `/unban`, `/dban`, `/kick`, `/mute`, `/unmute`
- `/warn`, `/warns`, `/clearwarns`, `/warnlimit`, `/warnaction`
- `/del`, `/purge`, `/modlog`
- `/lock`, `/unlock`, `/flood`
- `/blacklist`, `/rmblacklist`
- `/reports on|off`

### Notes and rules
- `/save`, `/clear`, `/filter`, `/stop`
- `/setrules`, `/rulesbtn`
- `/welcome`, `/goodbye`

### Federation commands
- `/newfed`, `/joinfed`, `/leavefed`, `/fedadmin`, `/fedban`, `/unfedban`

### Utility and owner commands
- `/settitle`, `/setdesc`, `/cleanservice`, `/zombies`
- `/addquiz`, `/showquiz`, `/delquiz`, `/broadcast`, `/addlogger`

## Permissions

For full functionality, make Yuuki an admin with permission to:
- Delete messages
- Restrict members
- Ban users
- Pin messages
- Manage chat settings

## Notes

- `bot.db` is local SQLite storage and is used when `MONGO_URI` is not set. When `MONGO_URI` (or `MONGO_URL` / `MONGODB_URI`) is set, MongoDB is automatically used.
- Dual-owner support is controlled by `OWNER_IDS`.
