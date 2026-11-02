"""
Narrative Dialogue Engine for Mafia Game.
Generates rich, story-driven, immersive social deduction narration
using predefined templates, conditional logic, random variations, and real game state.
STRICT REQUIREMENT: NO AI APIS OR EXTERNAL LLM CALLS ARE USED.
"""

import html
import random
from typing import List, Optional


def format_player_mention(user_id: int, name: str) -> str:
    escaped_name = html.escape(name or 'Player')
    return f"<b>{escaped_name}</b> (<a href=\"tg://user?id={user_id}\">profile</a>)"


def format_player_tag(user_id: int, name: str, username: Optional[str] = None) -> str:
    if username:
        clean = username.lstrip('@')
        return f"@{clean}"
    return format_player_mention(user_id, name)


# ---------------------------------------------------------
# LOBBY & RECRUITMENT TEMPLATES
# ---------------------------------------------------------
LOBBY_CREATED_TEMPLATES = [
    (
        "🎭 <b>A Shadow Falls Upon the Town...</b> 🌆\n\n"
        "Whispers echo down cobblestone streets as the sun begins to set. A secret syndicate known as the <b>Mafia</b> "
        "has infiltrated the town, and no one knows who can be trusted.\n\n"
        "👑 <b>Game Host:</b> {host_tag}\n"
        "👥 <b>Town Council:</b> {player_count}/{max_players} citizens gathered (Min: {min_players})\n\n"
        "Will you stand with the innocent townsfolk, or conspire in the shadows? Gather your allies and prepare your wits!"
    ),
    (
        "🕯️ <b>The Town Meeting Has Been Convened!</b> 🏛️\n\n"
        "An emergency summons rings out from the church bell tower! Deception is taking root among the citizens, "
        "and danger looms as night approaches.\n\n"
        "👑 <b>Host:</b> {host_tag}\n"
        "👥 <b>Citizens Assembled:</b> {player_count}/{max_players} (Min: {min_players})\n\n"
        "Step forward, take your seat in the council, and uncover the betrayal before the town falls silent."
    ),
    (
        "🌑 <b>Dark Clouds Gather Over the Village</b> ⚔️\n\n"
        "Rumors of nocturnal assassinations have gripped the settlement. Trust is a luxury few can afford.\n\n"
        "👑 <b>Organized by:</b> {host_tag}\n"
        "👥 <b>Citizens in Lobby:</b> {player_count}/{max_players} (Min: {min_players})\n\n"
        "Press <b>Join Game</b> below to take up a role. Beware: deception walks among us!"
    )
]

LOBBY_JOIN_TEMPLATES = [
    "🚪 {player_tag} stepped into the town square and took a seat at the council table! ({player_count}/{max_players})",
    "📜 {player_tag} answered the summons and joined the town ranks. ({player_count}/{max_players})",
    "🕯️ {player_tag} entered from the chilly fog and joined the vigil. ({player_count}/{max_players})",
    "🛡️ {player_tag} pulled up a chair. Another soul enters the game of shadows! ({player_count}/{max_players})"
]

LOBBY_LEAVE_TEMPLATES = [
    "🚪 {player_tag} slipped out of the town council and hurried into the mist. ({player_count}/{max_players})",
    "🚶 {player_tag} decided to step back from the looming danger and left the lobby. ({player_count}/{max_players})",
    "💨 {player_tag} withdrew from the assembly. ({player_count}/{max_players})"
]

# ---------------------------------------------------------
# ROLE INTRODUCTIONS (PRIVATE DM)
# ---------------------------------------------------------
MAFIA_ROLE_TEMPLATES = [
    (
        "🔴 <b>Your Secret Identity: MAFIA</b> 🗡️\n\n"
        "You operate in the shadows, hidden among the gullible townspeople. Every night, you and your syndicate "
        "will choose an innocent target to eliminate.\n\n"
        "🤝 <b>Your Syndicate:</b>\n{teammates_text}\n\n"
        "🎭 <b>Your Objective:</b> Blend in during daytime discussions, deflect suspicion, eliminate the town leaders, "
        "and equal or outnumber the surviving townsfolk!"
    ),
    (
        "🔴 <b>The Syndicate Welcomes You: MAFIA</b> 🕶️\n\n"
        "Under the cover of moonlight, you strike with deadly precision. The town thinks you are one of them, but your "
        "true loyalty belongs to the underworld.\n\n"
        "🤝 <b>Fellow Conspirators:</b>\n{teammates_text}\n\n"
        "💡 <b>Tip:</b> Watch who speaks up in town meetings. Coordinate silently and mislead the Detective!"
    )
]

DETECTIVE_ROLE_TEMPLATES = [
    (
        "🕵️ <b>Your Secret Identity: DETECTIVE</b> 🔍\n\n"
        "You are the town's keenest mind and sharpest eye. Each night, you may secretly investigate one living citizen "
        "to discover whether they are a member of the Mafia or an innocent Townsperson.\n\n"
        "⚖️ <b>Your Objective:</b> Gather undeniable evidence, guide the town toward justice during daytime voting, "
        "and protect yourself from being targeted by the syndicate!"
    ),
    (
        "🕵️ <b>Badge & Magnifying Glass: DETECTIVE</b> 📜\n\n"
        "When the clock strikes midnight, your investigation begins. Choose one suspect each night to reveal their true allegiance.\n\n"
        "💡 <b>Tip:</b> Be careful when revealing your findings in public! If the Mafia catches on that you are the Detective, "
        "you will become their primary target."
    )
]

DOCTOR_ROLE_TEMPLATES = [
    (
        "🩺 <b>Your Secret Identity: DOCTOR</b> 💉\n\n"
        "Armed with medicine and bravery, you venture into the dark every night to administer protective aid. "
        "Choose one living citizen each night to watch over.\n\n"
        "🛡️ <b>Your Objective:</b> If your chosen patient is attacked by the Mafia tonight, your timely intervention will "
        "save their life!\n\n"
        "💡 <b>Tip:</b> Anticipate who the Mafia fears most. A well-placed protection can completely turn the tide of the game."
    ),
    (
        "🩺 <b>The Town Healer: DOCTOR</b> 🏥\n\n"
        "While others sleep, you keep vigil over the sick and endangered. Pick one soul to safeguard tonight.\n\n"
        "🛡️ If the Mafia strikes your patient, they will survive the night unharmed!"
    )
]

VILLAGER_ROLE_TEMPLATES = [
    (
        "🧑 <b>Your Secret Identity: VILLAGER</b> 🌾\n\n"
        "You have no nocturnal tricks or special weapons—only your voice, your intuition, and your vote. "
        "The Mafia walks among you pretending to be ordinary neighbors.\n\n"
        "⚖️ <b>Your Objective:</b> Scrutinize every word during town meetings, interrogate suspicious behavior, "
        "form alliances with honest players, and vote to lynch the Mafia before they outnumber you!"
    ),
    (
        "🧑 <b>The Pillar of the Town: VILLAGER</b> 🏘️\n\n"
        "You sleep through the night while danger creeps outside your window. By day, your vote is the most powerful "
        "weapon the town possesses.\n\n"
        "💡 <b>Tip:</b> Watch how players vote. Conspirators often defend each other or quickly jump onto innocent bandwagons!"
    )
]

# ---------------------------------------------------------
# NIGHT PHASE TEMPLATES
# ---------------------------------------------------------
NIGHT_START_TEMPLATES = [
    (
        "🌑 <b>Night {night_num} Falls Over the Town</b> 🌙\n\n"
        "The sun sinks beneath the horizon and a heavy shroud of darkness envelops every house. "
        "Doors are bolted shut, candles flicker out, and an eerie silence settles upon the streets.\n\n"
        "Living Citizens: <b>{alive_count}</b>\n\n"
        "<i>Living players with nocturnal abilities: check your private message with Yuki right now to make your choice!</i>\n"
        "⏳ Night deadline: <b>{duration} seconds</b>."
    ),
    (
        "🌃 <b>The Midnight Hour: Night {night_num}</b> ⏳\n\n"
        "A chill wind howls through the alleyways. The innocent rest uneasily in their beds while shadows stir in the corners of town.\n\n"
        "Living Citizens: <b>{alive_count}</b>\n\n"
        "🔴 <b>Mafia</b>, 🕵️ <b>Detective</b>, and 🩺 <b>Doctor</b>: your private action menus are waiting in your DM!\n"
        "⏳ You have <b>{duration} seconds</b> to execute your decisions."
    ),
    (
        "🌑 <b>Darkness Descends: Night {night_num}</b> 🕯️\n\n"
        "Not a single light burns in the village square. Someone among you is hunting, someone is searching for truth, "
        "and someone is desperately trying to save a life.\n\n"
        "Surviving Citizens: <b>{alive_count}</b>\n\n"
        "<i>Check your private DM with the bot to submit your night action!</i> (⏳ {duration}s)"
    )
]

# ---------------------------------------------------------
# MORNING & NIGHT RESOLUTION TEMPLATES
# ---------------------------------------------------------
MORNING_KILL_TEMPLATES = [
    (
        "☀️ <b>Dawn Breaks: A Grim Discovery on Day {day_num}</b> 🥀\n\n"
        "The morning rooster crows, but the mood in the town square is grim. As neighbors gather at the fountain, "
        "a horrifying scene greets them.\n\n"
        "🩸 <b>{victim_tag}</b> was murdered in cold blood during the dark hours of the night!\n"
        "Their life has been extinguished, and their role is revealed to be: <b>{role_display}</b>.\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "Anger and suspicion swell through the crowd. Who could have done this?!"
    ),
    (
        "🌅 <b>Tragedy at Sunrise: Day {day_num}</b> 💔\n\n"
        "Sunlight filters through the misty streets, but peace has been shattered once again.\n\n"
        "🥀 The lifeless body of <b>{victim_tag}</b> was found outside their doorstep! The Mafia left no mercy behind.\n"
        "📜 They served the town as: <b>{role_display}</b>.\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "The time for tears is past—the town must find the killers before another night falls."
    )
]

MORNING_SAVED_TEMPLATES = [
    (
        "☀️ <b>Miracle at Dawn: Day {day_num}</b> 🕊️✨\n\n"
        "The morning sun rises upon an anxious town, but against all odds, <b>everyone survived the night!</b>\n\n"
        "Signs of a violent struggle and medical bandages were discovered near the town square—the <b>Doctor</b>'s "
        "timely intervention thwarted the Mafia's deadly assault!\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "A cheer rises from the crowd, but the danger remains. The syndicate is still among you!"
    ),
    (
        "🌅 <b>A Life Spared: Day {day_num}</b> 🛡️\n\n"
        "Daylight washes over the streets to reveal wonderful news: <b>no one died tonight!</b>\n\n"
        "The Healer's watchful eyes protected the intended victim just in the nick of time.\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "The Mafia's blade struck empty air. Stand united and root them out!"
    )
]

MORNING_NO_KILL_TEMPLATES = [
    (
        "☀️ <b>A Quiet Night: Day {day_num}</b> 🕊️\n\n"
        "Daylight returns to the town. Miraculously, the streets were quiet last night and <b>no casualties occurred</b>.\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "Perhaps the assassins hesitated, or perhaps luck smiled upon the innocent. Stay vigilant!"
    )
]

# ---------------------------------------------------------
# DISCUSSION PROMPTS
# ---------------------------------------------------------
DISCUSSION_PROMPTS = [
    "🗣️ <i>Notice who was quiet during yesterday's meeting? The quietest citizen often hides the darkest dagger.</i>",
    "🤔 <i>Who deflected the conversation when questions were asked? Pay close attention to sudden bandwagon shifts!</i>",
    "💬 <i>If someone accused another player with unearned confidence, ask them for their reasons.</i>",
    "🕵️ <i>Look at previous vote records! Mafia members rarely vote against their own unless forced to sacrifice a pawn.</i>",
    "🧐 <i>Trust nobody blindly. Share your theories, challenge suspicious defenses, and question every motive!</i>",
    "🕯️ <i>Someone's reaction to this morning's news looked entirely rehearsed. Who looks guilty to you?</i>"
]

DISCUSSION_START_TEMPLATES = [
    (
        "🗣️ <b>Town Council In Session: Day {day_num} Discussion</b> 🏛️\n\n"
        "The floor is now open for public debate! Question your neighbors, present your theories, and defend your name.\n\n"
        "{prompt}\n\n"
        "💡 <i>Players may also use the interactive buttons below to post public accusations or self-defenses!</i>\n"
        "⏳ Discussion time remaining: <b>{duration} seconds</b> before voting commences."
    ),
    (
        "📢 <b>The Accusations Begin: Day {day_num} Town Meeting</b> ⚖️\n\n"
        "Citizens gather around the podium in heated debate. Every word spoken today could be the difference between survival and disaster.\n\n"
        "{prompt}\n\n"
        "⏳ You have <b>{duration} seconds</b> to discuss before the voting booths open!"
    )
]

# ---------------------------------------------------------
# VOTING PHASE TEMPLATES
# ---------------------------------------------------------
VOTING_START_TEMPLATES = [
    (
        "⚖️ <b>The Town Court Opens: Casting Votes for Day {day_num}</b> 🗳️\n\n"
        "Discussion has concluded. The hour has come to hold someone accountable for the town's plight.\n\n"
        "Living Citizens: <b>{alive_count}</b>\n\n"
        "Press a button below to cast your vote against a suspect, or vote to <b>Skip</b> if you believe evidence is insufficient.\n"
        "⏳ Voting deadline: <b>{duration} seconds</b>."
    ),
    (
        "🗳️ <b>Judgment Time: Day {day_num} Lynch Vote</b> ⚖️\n\n"
        "The gavel strikes the wooden pedestal! Every living citizen must now cast their verdict.\n\n"
        "Living Citizens: <b>{alive_count}</b>\n\n"
        "Will you bring justice upon the syndicate, or send an innocent neighbor to the gallows?\n"
        "⏳ Voting concludes in <b>{duration} seconds</b>."
    )
]

# ---------------------------------------------------------
# ELIMINATION (LYNCH) TEMPLATES
# ---------------------------------------------------------
LYNCH_EXECUTION_TEMPLATES = [
    (
        "⚖️ <b>The Town Has Spoken: Execution on Day {day_num}</b> 🏛️\n\n"
        "After tallying the votes, the town council has delivered its irreversible verdict!\n\n"
        "The accused, <b>{victim_tag}</b>, was led to the gallows with {vote_count} votes.\n"
        "As their final words faded, their true identity was revealed:\n"
        "🏷️ Role: <b>{role_display}</b>\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "The crowd disperses in somber reflection as evening approaches."
    ),
    (
        "⚔️ <b>A Citizen Banished: Day {day_num} Lynch Result</b> 🥀\n\n"
        "The majority reached consensus. Despite desperate pleas, <b>{victim_tag}</b> has been eliminated by the town!\n\n"
        "📜 The ledger of truth reveals they were: <b>{role_display}</b>.\n\n"
        "Surviving Citizens: <b>{survivor_count}</b>\n"
        "Did the town root out evil, or fall victim to deception? Night will soon reveal the answer."
    )
]

TIE_VOTE_TEMPLATES = [
    (
        "⚖️ <b>A Divided Council: No Lynch on Day {day_num}</b> 🕊️\n\n"
        "The town council could not reach a clear majority! Conflicting accusations and tied votes have left the citizens deadlocked.\n\n"
        "No player was eliminated today. The sun begins to set as uneasy citizens retreat to their homes..."
    ),
    (
        "🗳️ <b>Deadlock in the Town Square!</b> 🤷\n\n"
        "The votes are split evenly or the town chose to skip the lynch! Without a decisive majority, the gallows remain empty.\n\n"
        "Night falls once again upon the uneasy settlement..."
    )
]

# ---------------------------------------------------------
# ACCUSATION & DEFENSE TEMPLATES
# ---------------------------------------------------------
ACCUSATION_TEMPLATES = [
    "👉 <b>{accuser_tag}</b> points an accusing finger at <b>{target_tag}</b>!\n<i>\"Your story doesn't add up! Look at how you deflected earlier—I believe you're working for the Mafia!\"</i>",
    "🧐 <b>{accuser_tag}</b> publicly questions <b>{target_tag}</b>!\n<i>\"You've been awfully quiet while the rest of us investigate. What are you hiding in the dark?\"</i>",
    "⚡ <b>{accuser_tag}</b> challenges <b>{target_tag}</b>!\n<i>\"Everything about your behavior screams deception. The town needs to look closely at you!\"</i>"
]

DEFENSE_TEMPLATES = [
    "🛡️ <b>{defender_tag}</b> steps forward to defend their honor!\n<i>\"I swear on my life that my loyalty lies with the Town! Accusing me is playing right into the Mafia's hands!\"</i>",
    "🗣️ <b>{defender_tag}</b> speaks out against the suspicions!\n<i>\"Look at my record and my actions! The real killers are trying to frame an innocent citizen!\"</i>",
    "✋ <b>{defender_tag}</b> raises their voice in self-defense!\n<i>\"Do not be deceived! If you eliminate me, you will only weaken the town's chances of survival!\"</i>"
]

SUSPICION_ALERT_TEMPLATES = [
    "⚠️ <b>Suspicion Mounts Against {target_tag}!</b> Several citizens have turned their eyes and votes toward them. The pressure is on!",
    "🔥 <b>The Heat Rises!</b> Multiple players are questioning <b>{target_tag}</b>. Will they be able to clear their name before the gavel falls?"
]

# ---------------------------------------------------------
# FAREWELL (PRIVATE DM TO ELIMINATED PLAYER)
# ---------------------------------------------------------
FAREWELL_TEMPLATES = [
    (
        "👻 <b>You Have Fallen...</b> 🥀\n\n"
        "Your journey in this game of Mafia has come to an end. Your spirit now wanders the shadowy streets of the town.\n\n"
        "🤫 <b>Ghost Rule:</b> Please refrain from giving tips, spoiling secrets, or revealing private roles in the group! "
        "Watch the remainder of the drama unfold in silence."
    ),
    (
        "🕊️ <b>Rest In Peace, Citizen</b> 🕯️\n\n"
        "You have been eliminated from the town council. You fought valiantly for your team!\n\n"
        "🤫 Remember: dead players must keep silent regarding roles and private knowledge until the game concludes."
    )
]

# ---------------------------------------------------------
# VICTORY TEMPLATES
# ---------------------------------------------------------
TOWN_VICTORY_TEMPLATES = [
    (
        "🏆🎉 <b>TOWN VICTORY! Justice Triumphs!</b> 🕊️✨\n\n"
        "The final shadow has been banished from the streets! Through courage, deduction, and unwavering unity, "
        "the honest citizens have successfully eliminated every member of the <b>Mafia</b>!\n\n"
        "☀️ The sun rises upon a peaceful dawn, and the bells of freedom toll across the valley.\n\n"
        "<b>Surviving Heroes:</b>\n{survivors_text}\n\n"
        "📜 <b>Final Player Roster:</b>\n{roster_text}"
    ),
    (
        "🏆🌟 <b>THE TOWN HAS WON! Order is Restored!</b> 🏛️🎊\n\n"
        "The syndicate's reign of terror is over! The honest people of the town exposed every conspirator and saved their home.\n\n"
        "<b>Honorable Survivors:</b>\n{survivors_text}\n\n"
        "📜 <b>Player Roles:</b>\n{roster_text}"
    )
]

MAFIA_VICTORY_TEMPLATES = [
    (
        "🏆🔴 <b>MAFIA VICTORY! The Shadows Consume the Town!</b> 🗡️🖤\n\n"
        "The deceit was too grand and the blades were too sharp. The <b>Mafia</b> has successfully equaled or outnumbered "
        "the remaining citizens!\n\n"
        "🌑 The town square falls dead silent under the rule of the underworld. Deception has won the game.\n\n"
        "<b>Victorious Syndicate:</b>\n{survivors_text}\n\n"
        "📜 <b>Final Player Roster:</b>\n{roster_text}"
    ),
    (
        "🏆🕶️ <b>THE UNDERWORLD REIGNS! Mafia Triumph!</b> 🔴🍷\n\n"
        "The syndicate moved unseen and manipulated every vote. With the town's defenses completely shattered, "
        "the Mafia emerges victorious!\n\n"
        "<b>Victorious Conspirators:</b>\n{survivors_text}\n\n"
        "📜 <b>Full Player Roster:</b>\n{roster_text}"
    )
]


# ---------------------------------------------------------
# BUILDER FUNCTIONS
# ---------------------------------------------------------

def get_lobby_created_text(host_id: int, host_name: str, player_count: int, max_players: int, min_players: int = 5) -> str:
    template = random.choice(LOBBY_CREATED_TEMPLATES)
    host_tag = format_player_mention(host_id, host_name)
    return template.format(
        host_tag=host_tag,
        player_count=player_count,
        max_players=max_players,
        min_players=min_players
    )


def get_lobby_join_text(user_id: int, user_name: str, username: Optional[str], player_count: int, max_players: int) -> str:
    template = random.choice(LOBBY_JOIN_TEMPLATES)
    tag = format_player_tag(user_id, user_name, username)
    return template.format(player_tag=tag, player_count=player_count, max_players=max_players)


def get_lobby_leave_text(user_id: int, user_name: str, username: Optional[str], player_count: int, max_players: int) -> str:
    template = random.choice(LOBBY_LEAVE_TEMPLATES)
    tag = format_player_tag(user_id, user_name, username)
    return template.format(player_tag=tag, player_count=player_count, max_players=max_players)


def get_role_dm_text(role: str, teammates: List[dict] = None) -> str:
    role_lower = str(role).lower()
    if role_lower == 'mafia':
        template = random.choice(MAFIA_ROLE_TEMPLATES)
        if teammates and len(teammates) > 1:
            team_lines = [f"• {format_player_mention(m['user_id'], m['name'])}" for m in teammates]
            teammates_text = "\n".join(team_lines)
        else:
            teammates_text = "• <i>You are currently the lone wolf of the Mafia syndicate!</i>"
        return template.format(teammates_text=teammates_text)
    elif role_lower == 'detective':
        return random.choice(DETECTIVE_ROLE_TEMPLATES)
    elif role_lower == 'doctor':
        return random.choice(DOCTOR_ROLE_TEMPLATES)
    else:
        return random.choice(VILLAGER_ROLE_TEMPLATES)


def get_night_start_text(night_num: int, alive_count: int, duration: int) -> str:
    template = random.choice(NIGHT_START_TEMPLATES)
    return template.format(night_num=night_num, alive_count=alive_count, duration=duration)


def get_morning_announcement(day_num: int, victim: Optional[dict], survivor_count: int, doctor_saved: bool = False) -> str:
    if doctor_saved:
        template = random.choice(MORNING_SAVED_TEMPLATES)
        return template.format(day_num=day_num, survivor_count=survivor_count)
    elif victim:
        template = random.choice(MORNING_KILL_TEMPLATES)
        victim_tag = format_player_mention(victim['user_id'], victim['name'])
        role_disp = get_role_display_name(victim['role'])
        return template.format(
            day_num=day_num,
            victim_tag=victim_tag,
            role_display=role_disp,
            survivor_count=survivor_count
        )
    else:
        template = random.choice(MORNING_NO_KILL_TEMPLATES)
        return template.format(day_num=day_num, survivor_count=survivor_count)


def get_discussion_start_text(day_num: int, duration: int) -> str:
    template = random.choice(DISCUSSION_START_TEMPLATES)
    prompt = random.choice(DISCUSSION_PROMPTS)
    return template.format(day_num=day_num, duration=duration, prompt=prompt)


def get_voting_start_text(day_num: int, alive_count: int, duration: int) -> str:
    template = random.choice(VOTING_START_TEMPLATES)
    return template.format(day_num=day_num, alive_count=alive_count, duration=duration)


def get_lynch_announcement(day_num: int, victim: Optional[dict], vote_count: int, survivor_count: int) -> str:
    if victim:
        template = random.choice(LYNCH_EXECUTION_TEMPLATES)
        victim_tag = format_player_mention(victim['user_id'], victim['name'])
        role_disp = get_role_display_name(victim['role'])
        return template.format(
            day_num=day_num,
            victim_tag=victim_tag,
            vote_count=vote_count,
            role_display=role_disp,
            survivor_count=survivor_count
        )
    else:
        template = random.choice(TIE_VOTE_TEMPLATES)
        return template.format(day_num=day_num)


def get_accusation_text(accuser_id: int, accuser_name: str, target_id: int, target_name: str) -> str:
    template = random.choice(ACCUSATION_TEMPLATES)
    a_tag = format_player_mention(accuser_id, accuser_name)
    t_tag = format_player_mention(target_id, target_name)
    return template.format(accuser_tag=a_tag, target_tag=t_tag)


def get_defense_text(defender_id: int, defender_name: str) -> str:
    template = random.choice(DEFENSE_TEMPLATES)
    d_tag = format_player_mention(defender_id, defender_name)
    return template.format(defender_tag=d_tag)


def get_suspicion_alert_text(target_id: int, target_name: str) -> str:
    template = random.choice(SUSPICION_ALERT_TEMPLATES)
    t_tag = format_player_mention(target_id, target_name)
    return template.format(target_tag=t_tag)


def get_farewell_dm_text() -> str:
    return random.choice(FAREWELL_TEMPLATES)


def get_victory_announcement(winner_team: str, survivors: List[dict], all_players: List[dict]) -> str:
    survivors_lines = [
        f"• {format_player_mention(s['user_id'], s['name'])} ({get_role_display_name(s['role'])})"
        for s in survivors
    ]
    survivors_text = "\n".join(survivors_lines) if survivors_lines else "<i>No survivors remained</i>"

    roster_lines = [
        f"• {format_player_mention(p['user_id'], p['name'])} — {get_role_display_name(p['role'])} {'(Alive)' if p.get('is_alive') else '(Eliminated)'}"
        for p in all_players
    ]
    roster_text = "\n".join(roster_lines)

    if winner_team.lower() == 'town':
        template = random.choice(TOWN_VICTORY_TEMPLATES)
    else:
        template = random.choice(MAFIA_VICTORY_TEMPLATES)

    return template.format(survivors_text=survivors_text, roster_text=roster_text)


def get_role_display_name(role: str) -> str:
    role_lower = str(role).lower()
    mapping = {
        'mafia': '🔴 Mafia',
        'detective': '🕵️ Detective',
        'doctor': '🩺 Doctor',
        'villager': '🧑 Villager'
    }
    return mapping.get(role_lower, f"🧑 {role.title()}")
