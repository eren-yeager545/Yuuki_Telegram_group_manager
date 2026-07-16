# Yuuki : Telegram group manager

**Yuuki** is a stylish Telegram group manager built for communities that want strong moderation, flexible automation, and a more polished member experience without unnecessary bloat.[code_file:1]

Instead of being only a ban-and-mute bot, Yuuki is designed as a complete group control system: it helps admins manage spam, organize notes and rules, automate responses, welcome members in a smarter way, coordinate moderation across chats, and keep engagement alive with built-in quiz features.[code_file:1]

## Why Yuuki stands out

Yuuki combines practical admin power with community-focused tools, making it useful for support groups, fandoms, public communities, gaming chats, study groups, and large discussion spaces.[code_file:1] It brings together practical moderation tools, lightweight deployment, dual-owner support, and flexible configuration in a single Python bot project.[code_file:1]

## Core features

### Moderation toolkit
- Ban, unban, kick, mute, unmute, warn, clear warns, and view warned users.[code_file:1]
- Purge messages, inspect moderation logs, and manage chat title or description.[code_file:1]
- Set custom warn limits and define what happens after the limit is reached, such as mute, ban, kick, tempmute, or tempban.[code_file:1]

### Antiflood and protection
- Detect repeated spam bursts automatically.[code_file:1]
- Choose the response action for flooding: delete, warn, mute, kick, ban, tempmute, or tempban.[code_file:1]
- Extend protection with broad content locks for links, media, stickers, gifs, polls, forwards, bots, voice messages, video notes, documents, photos, videos, audio, commands, hashtags, mentions, inline references, and contacts.[code_file:1]

### Notes, filters, and button notes
- Save reusable notes for rules, links, FAQs, staff info, and announcements.[code_file:1]
- Add automatic keyword filters for instant replies.[code_file:1]
- Create button notes so a note can include clickable buttons that lead users to rules, channels, forms, or external resources.[code_file:1]

### Smarter welcomes
- Enable custom welcome and goodbye messages.[code_file:1]
- Personalize messages using placeholders like member name and group name.[code_file:1]
- Add welcome buttons and optionally auto-mute new members for a short period after joining.[code_file:1]

### Reports and blacklists
- Let members report problematic messages directly to admins.[code_file:1]
- Create blacklist triggers for blocked words or phrases.[code_file:1]
- Attach actions to blacklists so the bot can delete, warn, mute, kick, or ban automatically when needed.[code_file:1]

### Federation system
- Create federations to connect moderation policy across multiple groups.[code_file:1]
- Assign fed admins, join or leave federations, and apply fedbans to shared problem users.[code_file:1]
- Use this for networks of related groups that need consistent moderation control.[code_file:1]

### Engagement tools
- Post quizzes automatically on a schedule.[code_file:1]
- Add, list, and delete saved quiz questions.[code_file:1]
- Keep your group active with lightweight interactive content instead of pure moderation only.[code_file:1]

### Owner and admin flexibility
- Support dual full owners with `OWNER_IDS`, so two trusted people can have equal owner-level control.[code_file:1]
- Add extra trusted people through `SUDO_USERS`.[code_file:1]
- Useful for teams where one main owner should not be the only person with full recovery or management access.[code_file:1]

## Setup

1. Clone the repository.[code_file:1]
2. Create a virtual environment.[code_file:1]
3. Install dependencies:[code_file:1]
   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and fill in your values.[code_file:1]
5. Run the bot:[code_file:1]
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
```

## Main commands

### User commands
- `/start`, `/help`, `/ping`, `/rules`, `/stats`, `/privacy`
- `/get`, `/notes`, `/filters`
- `/report`, `/id`, `/info`, `/admins`, `/locks`, `/warned`
- `/fedinfo`, `/fedstat`, `/myfeds`, `/blacklists`

### Moderation commands
- `/ban`, `/unban`, `/kick`, `/mute`, `/unmute`
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
- `/addquiz`, `/showquiz`, `/delquiz`, `/broadcast`

## Best for

- Public Telegram communities.[code_file:1]
- Fan, gaming, support, and study groups.[code_file:1]
- Multi-group networks that need federation-style moderation.[code_file:1]
- Admin teams that want both automation and direct manual control.[code_file:1]

## Permissions

For full functionality, make the bot an admin with permission to:[code_file:1]
- Delete messages.[code_file:1]
- Restrict members.[code_file:1]
- Ban users.[code_file:1]
- Pin messages.[code_file:1]
- Manage chat settings.[code_file:1]

## Notes

- Test in a private group before using it in a large public chat.[code_file:1]
- `bot.db` is local SQLite storage and is ignored by git.[code_file:1]
- Dual-owner support is controlled by `OWNER_IDS`.[code_file:1]

## Secret safety

- All runtime secrets should be provided through environment variables in `.env`.
- Do not commit your real `.env` file to GitHub.
- If any secret was ever hardcoded in an earlier local version of this project, rotate it immediately because old values may remain in shell history, backups, or git history after pushing.
