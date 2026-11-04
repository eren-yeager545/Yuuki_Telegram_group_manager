"""
Mafia Game Engine for Telegram Bot.
Manages multi-group game states, role distribution, night actions,
day discussions, voting, and victory conditions.
Strictly decoupled from any AI APIs.
"""

import asyncio
import html
import logging
import random
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import ContextTypes

import store
from mafia_dialogue import (
    format_player_mention,
    format_player_tag,
    get_lobby_created_text,
    get_lobby_join_text,
    get_lobby_leave_text,
    get_role_dm_text,
    get_night_start_text,
    get_morning_announcement,
    get_discussion_start_text,
    get_npc_discussion_text,
    get_voting_start_text,
    get_lynch_announcement,
    get_accusation_text,
    get_defense_text,
    get_suspicion_alert_text,
    get_farewell_dm_text,
    get_victory_announcement,
    get_role_display_name
)

logger = logging.getLogger(__name__)

# Game configurations
MIN_PLAYERS = 5
MAX_PLAYERS = 16
LOBBY_DURATION = 120       # seconds (2 minutes)
NIGHT_DURATION = 60        # seconds (1 minute)
DISCUSSION_DURATION = 45   # seconds
VOTING_DURATION = 60       # seconds (1 minute)
DOCTOR_SELF_HEAL = True

# In-memory active games keyed by chat_id
ACTIVE_GAMES: Dict[int, "MafiaGame"] = {}
# Track last pinned mafia game announcement message ID per chat: chat_id -> message_id
LAST_PINNED_MAFIA_MESSAGES: Dict[int, int] = {}

NPC_PROFILES = [
    {"name": "Yuki (NPC)", "personality": "yuki"},
    {"name": "Phantom (NPC)", "personality": "phantom"},
    {"name": "Rukia (NPC)", "personality": "rukia"},
    {"name": "Kenshin (NPC)", "personality": "rukia"},
    {"name": "Shadow (NPC)", "personality": "phantom"},
    {"name": "Sakura (NPC)", "personality": "yuki"},
    {"name": "Byakuya (NPC)", "personality": "rukia"},
    {"name": "Kagura (NPC)", "personality": "yuki"},
    {"name": "Ren (NPC)", "personality": "phantom"},
    {"name": "Kira (NPC)", "personality": "phantom"},
    {"name": "Gin (NPC)", "personality": "yuki"},
    {"name": "Shunsui (NPC)", "personality": "rukia"},
    {"name": "Yoruichi (NPC)", "personality": "yuki"},
    {"name": "Grimmjow (NPC)", "personality": "phantom"},
    {"name": "Ulquiorra (NPC)", "personality": "rukia"},
    {"name": "Aizen (NPC)", "personality": "phantom"},
]


@dataclass
class MafiaPlayer:
    user_id: int
    name: str
    username: Optional[str] = None
    role: str = "villager"    # 'mafia', 'detective', 'doctor', 'villager'
    is_alive: bool = True
    is_host: bool = False
    is_bot: bool = False      # True for NPC
    npc_personality: Optional[str] = None  # 'yuki', 'phantom', 'rukia'
    dm_verified: bool = False
    voted_for: Optional[int] = None   # -1 for skip, user_id for player
    night_action_target: Optional[int] = None

    def display_tag(self) -> str:
        return format_player_tag(self.user_id, self.name, self.username)

    def mention(self) -> str:
        return format_player_mention(self.user_id, self.name)


class MafiaGame:
    def __init__(self, chat_id: int, chat_title: str, host_id: int, host_name: str, host_username: Optional[str] = None):
        self.game_id = str(uuid.uuid4())[:8]
        self.chat_id = chat_id
        self.chat_title = chat_title or f"Chat {chat_id}"
        self.host_id = host_id
        self.host_name = host_name
        self.host_username = host_username

        self.status = "lobby"   # 'lobby', 'night', 'discussion', 'voting', 'ended'
        self.players: Dict[int, MafiaPlayer] = {}
        self.round_num = 0
        self.phase_deadline = 0
        self.phase_task: Optional[asyncio.Task] = None
        self.active_timer_phase: Optional[str] = None
        self.timer_generation: int = 0
        self.pinned_message_id: Optional[int] = None

        self.phase_token: int = 0
        self.roles_assigned: bool = False
        self.start_lock: asyncio.Lock = asyncio.Lock()
        self.resolution_lock: asyncio.Lock = asyncio.Lock()

        # Per-player action tracking
        self.night_actions: Dict[int, int] = {}             # user_id -> target_user_id
        self.night_mafia_targets: Dict[int, int] = {}       # mafia_user_id -> target_user_id
        self._night_detective_target: Optional[int] = None
        self._night_doctor_target: Optional[int] = None

        # Day voting state
        self.day_votes: Dict[int, int] = {}                 # voter_id -> target_id (-1 for skip)
        self.accusations_made: Set[Tuple[int, int]] = set()

        # Elimination stats
        self.elimination_credits: Dict[int, int] = {}       # user_id -> count

        # NPC tracking
        self.npc_detective_memory: Dict[int, str] = {}      # target_id -> 'mafia' or 'town'
        self.npc_configured: bool = False

        # Add host as first player
        self.add_player(host_id, host_name, host_username, is_host=True)

    @property
    def night_detective_target(self) -> Optional[int]:
        for p in self.players.values():
            if p.role == "detective" and p.is_alive and p.user_id in self.night_actions:
                return self.night_actions[p.user_id]
        return self._night_detective_target

    @night_detective_target.setter
    def night_detective_target(self, val: Optional[int]):
        self._night_detective_target = val
        if val is not None:
            for p in self.players.values():
                if p.role == "detective" and p.is_alive:
                    self.night_actions[p.user_id] = val
                    break
        else:
            for p in self.players.values():
                if p.role == "detective":
                    self.night_actions.pop(p.user_id, None)

    @property
    def night_doctor_target(self) -> Optional[int]:
        for p in self.players.values():
            if p.role == "doctor" and p.is_alive and p.user_id in self.night_actions:
                return self.night_actions[p.user_id]
        return self._night_doctor_target

    @night_doctor_target.setter
    def night_doctor_target(self, val: Optional[int]):
        self._night_doctor_target = val
        if val is not None:
            for p in self.players.values():
                if p.role == "doctor" and p.is_alive:
                    self.night_actions[p.user_id] = val
                    break
        else:
            for p in self.players.values():
                if p.role == "doctor":
                    self.night_actions.pop(p.user_id, None)

    def add_player(self, user_id: int, name: str, username: Optional[str] = None, is_host: bool = False, is_bot: bool = False, npc_personality: Optional[str] = None) -> bool:
        if self.status != "lobby":
            return False
        if user_id in self.players:
            return False
        if len(self.players) >= MAX_PLAYERS:
            return False

        self.players[user_id] = MafiaPlayer(
            user_id=user_id,
            name=name,
            username=username,
            is_host=is_host,
            is_bot=is_bot,
            npc_personality=npc_personality,
            dm_verified=is_bot
        )
        return True

    def add_npcs(self, count: int) -> int:
        """
        Adds up to `count` NPC players without exceeding MAX_PLAYERS (16).
        Uses unique negative IDs (-1001, -1002, ...) and predefined profiles.
        Adjusts curr_npc_id on every iteration.
        """
        if self.status != "lobby":
            return 0
        added = 0
        used_names = {p.name for p in self.players.values()}
        used_ids = set(self.players.keys())

        profile_idx = 0
        curr_npc_id = -1001

        while len(self.players) < MAX_PLAYERS and added < count:
            while curr_npc_id in used_ids:
                curr_npc_id -= 1

            if profile_idx < len(NPC_PROFILES):
                prof = NPC_PROFILES[profile_idx]
                profile_idx += 1
                npc_name = prof["name"]
                npc_pers = prof["personality"]
            else:
                npc_name = f"Citizen #{abs(curr_npc_id)} (NPC)"
                npc_pers = random.choice(["yuki", "phantom", "rukia"])

            if npc_name in used_names:
                curr_npc_id -= 1
                continue

            success = self.add_player(
                user_id=curr_npc_id,
                name=npc_name,
                username=None,
                is_host=False,
                is_bot=True,
                npc_personality=npc_pers
            )
            if success:
                used_names.add(npc_name)
                used_ids.add(curr_npc_id)
                added += 1
            curr_npc_id -= 1

        return added

    def remove_player(self, user_id: int) -> bool:
        if self.status != "lobby":
            return False
        if user_id not in self.players:
            return False
        del self.players[user_id]
        return True

    def get_alive_players(self) -> List[MafiaPlayer]:
        return [p for p in self.players.values() if p.is_alive]

    def get_alive_count(self) -> int:
        return len(self.get_alive_players())

    def get_mafia_players(self, alive_only: bool = True) -> List[MafiaPlayer]:
        return [p for p in self.players.values() if p.role == "mafia" and (not alive_only or p.is_alive)]

    def get_town_players(self, alive_only: bool = True) -> List[MafiaPlayer]:
        return [p for p in self.players.values() if p.role != "mafia" and (not alive_only or p.is_alive)]

    def cancel_phase_timer(self):
        self.timer_generation += 1
        current_task = None
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            pass
        if self.phase_task and not self.phase_task.done() and self.phase_task != current_task:
            logger.debug(f"[Mafia {self.game_id}] Cancelling timer for phase '{self.active_timer_phase}'")
            self.phase_task.cancel()
        self.phase_task = None
        self.active_timer_phase = None

    def assign_roles(self):
        """
        Assigns roles based on player count:
        - 5-6 players: 1 Mafia, 1 Detective, 1 Doctor, 2-3 Villagers
        - 7-8 players: 2 Mafia, 1 Detective, 1 Doctor, 3-4 Villagers
        - 9-11 players: 2 Mafia (or 3 for 10+), 1 Detective, 1 Doctor, remainder Villagers
        - 12+ players: ~25% Mafia, 1 Detective, 1 Doctor, remainder Villagers
        """
        count = len(self.players)
        if count < MIN_PLAYERS:
            raise ValueError(f"Need at least {MIN_PLAYERS} players.")

        if count in (5, 6):
            mafia_count = 1
        elif count in (7, 8, 9):
            mafia_count = 2
        elif count in (10, 11, 12):
            mafia_count = 3
        else:
            mafia_count = max(3, count // 4)

        roles_pool = ["mafia"] * mafia_count
        roles_pool.append("detective")
        roles_pool.append("doctor")

        villager_count = count - len(roles_pool)
        roles_pool.extend(["villager"] * villager_count)

        if self.roles_assigned:
            logger.info(f"[Mafia {self.game_id}] Roles already assigned; skipping reassignment.")
            self.validate_roles()
            return

        random.shuffle(roles_pool)
        for player, role in zip(self.players.values(), roles_pool):
            player.role = role

        self.roles_assigned = True
        self.validate_roles()

    def validate_roles(self):
        assert len(self.players) >= MIN_PLAYERS, f"Need at least {MIN_PLAYERS} players."
        valid_roles = {"mafia", "detective", "doctor", "villager"}
        seen_players = set()
        assigned_roles = []

        for p_id, p in self.players.items():
            assert p.role in valid_roles, f"Player {p_id} has invalid role {p.role}"
            assert p_id not in seen_players, f"Duplicate player {p_id} in role distribution"
            seen_players.add(p_id)
            assigned_roles.append(p.role)

        assert len(seen_players) == len(self.players), "Every registered player must have exactly one role."
        assert len(assigned_roles) == len(self.players), "Total assigned roles must equal registered players."
        assert "mafia" in assigned_roles, "Role distribution must contain at least one Mafia."
        assert "detective" in assigned_roles, "Role distribution must contain at least one Detective."
        assert "doctor" in assigned_roles, "Role distribution must contain at least one Doctor."

    def check_win_condition(self) -> Optional[str]:
        """
        Checks game over conditions:
        - 'town' if all Mafia members eliminated
        - 'mafia' if Mafia members >= alive non-Mafia
        - None if game continues
        """
        alive_mafia = len(self.get_mafia_players(alive_only=True))
        alive_town = len(self.get_town_players(alive_only=True))

        if alive_mafia == 0:
            return "town"
        if alive_mafia >= alive_town:
            return "mafia"
        return None


# ----------------------------------------------------------------------------
# KEYBOARDS BUILDERS
# ----------------------------------------------------------------------------

def build_lobby_keyboard(game_id: str, bot_username: str) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("➕ Join Game", callback_data=f"mafia_join:{game_id}"),
            InlineKeyboardButton("➖ Leave Game", callback_data=f"mafia_leave:{game_id}")
        ],
        [
            InlineKeyboardButton("👥 View Players", callback_data=f"mafia_players:{game_id}"),
            InlineKeyboardButton("▶️ Start Game", callback_data=f"mafia_start:{game_id}")
        ],
        [
            InlineKeyboardButton("🎭 Add NPCs", callback_data=f"mafia_npc_menu:{game_id}"),
            InlineKeyboardButton("💬 Private Bot", url=f"https://t.me/{bot_username}?start=mafia_reg")
        ]
    ]
    return InlineKeyboardMarkup(kb)


def build_npc_prompt_keyboard(game_id: str) -> InlineKeyboardMarkup:
    """Inline buttons: YES / NO when lobby is ready to start. Consistent game_id in parts[1]."""
    kb = [
        [
            InlineKeyboardButton("YES", callback_data=f"mafia_npc_opt:{game_id}:yes"),
            InlineKeyboardButton("NO", callback_data=f"mafia_npc_opt:{game_id}:no")
        ]
    ]
    return InlineKeyboardMarkup(kb)


def build_npc_config_keyboard(game_id: str, current_count: int) -> InlineKeyboardMarkup:
    """Options: Custom NPC Count and Fill Players to 16."""
    remaining = MAX_PLAYERS - current_count
    kb = []
    custom_row = []
    for count in (1, 2, 3, 5):
        if count <= remaining:
            custom_row.append(
                InlineKeyboardButton(f"+{count} NPC", callback_data=f"mafia_npc_add:{game_id}:{count}")
            )
    if custom_row:
        kb.append(custom_row)

    second_row = [
        InlineKeyboardButton("1. Custom NPC Count", callback_data=f"mafia_npc_custom_input:{game_id}"),
        InlineKeyboardButton(f"2. Fill Players to 16 ({remaining} added)", callback_data=f"mafia_npc_add:{game_id}:{remaining}")
    ]
    kb.append(second_row)

    if current_count >= MIN_PLAYERS:
        kb.append([InlineKeyboardButton("▶️ Start Game Now", callback_data=f"mafia_npc_start_now:{game_id}")])

    return InlineKeyboardMarkup(kb)


def build_insufficient_npc_keyboard(game_id: str, current_count: int) -> InlineKeyboardMarkup:
    """
    Offers when lobby does not have minimum players:
    - Add NPCs to reach the minimum.
    - Custom NPC Count.
    - Fill Players to 16.
    """
    needed_for_min = max(0, MIN_PLAYERS - current_count)
    needed_for_16 = max(0, MAX_PLAYERS - current_count)
    kb = [
        [
            InlineKeyboardButton(f"➕ Add NPCs to reach minimum ({needed_for_min})", callback_data=f"mafia_npc_add:{game_id}:{needed_for_min}")
        ],
        [
            InlineKeyboardButton("🔢 Custom NPC Count", callback_data=f"mafia_npc_custom_input:{game_id}"),
            InlineKeyboardButton(f"👥 Fill Players to 16 ({needed_for_16})", callback_data=f"mafia_npc_add:{game_id}:{needed_for_16}")
        ]
    ]
    return InlineKeyboardMarkup(kb)


def build_night_target_keyboard(game_id: str, action: str, targets: List[MafiaPlayer]) -> InlineKeyboardMarkup:
    """
    Builds keyboard for private night actions (mafia kill, detective investigate, doctor protect).
    Action is 'mkill', 'dcheck', or 'dheal'.
    """
    rows = []
    for p in targets:
        btn_text = f"🎯 {p.name}"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"mafia_night_{action}:{game_id}:{p.user_id}")])
    return InlineKeyboardMarkup(rows)


def build_voting_keyboard(game_id: str, alive_players: List[MafiaPlayer]) -> InlineKeyboardMarkup:
    rows = []
    # Two buttons per row
    curr_row = []
    for p in alive_players:
        btn_text = f"⚖️ {p.name}"
        curr_row.append(InlineKeyboardButton(btn_text, callback_data=f"mafia_vote:{game_id}:{p.user_id}"))
        if len(curr_row) == 2:
            rows.append(curr_row)
            curr_row = []
    if curr_row:
        rows.append(curr_row)
    rows.append([InlineKeyboardButton("🕊️ Skip Lynch (No Vote)", callback_data=f"mafia_vote:{game_id}:skip")])
    return InlineKeyboardMarkup(rows)


def build_discussion_action_keyboard(game_id: str) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("👉 Point Accusation", callback_data=f"mafia_act_accuse:{game_id}"),
            InlineKeyboardButton("🛡️ Defend Honor", callback_data=f"mafia_act_defend:{game_id}")
        ],
        [
            InlineKeyboardButton("📋 Council Status", callback_data=f"mafia_status:{game_id}")
        ]
    ]
    return InlineKeyboardMarkup(kb)


# ----------------------------------------------------------------------------
# CORE GAME FLOW & ASYNC LOOPS
# ----------------------------------------------------------------------------

def start_phase_timer(
    game: MafiaGame,
    phase: str,
    duration: int,
    callback,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Schedules an isolated, robust timer for a given phase, chat_id, and game_id.
    - Cancels any existing phase timer for this game.
    - Prevents duplicate timer executions using timer_generation and phase checks.
    - Guarantees termination condition: when deadline expires,
      executes callback if game is still active in this phase and generation matches.
    - Properly catches Telegram edit/send failures, cancellations, and unexpected exceptions.
    - Logs detailed debug events with game_id, phase, duration, and status transitions.
    """
    game.cancel_phase_timer()
    gen = game.timer_generation
    token = game.phase_token
    game.active_timer_phase = phase
    game.phase_deadline = time.time() + duration

    logger.debug(
        f"[Mafia {game.game_id}] Timer started for phase '{phase}' (gen {gen}) | "
        f"Duration: {duration}s | Deadline: {game.phase_deadline:.2f}"
    )

    async def _timer_runner():
        try:
            while True:
                remaining = game.phase_deadline - time.time()
                if remaining <= 0:
                    break
                sleep_chunk = min(remaining, 5.0)
                await asyncio.sleep(sleep_chunk)
                if game.timer_generation != gen or game.phase_token != token or game.status != phase:
                    logger.debug(
                        f"[Mafia {game.game_id}] Phase '{phase}' (gen {gen}, token {token}) preempted/cancelled. Exiting timer runner."
                    )
                    return
                current_rem = max(0, int(game.phase_deadline - time.time()))
                logger.debug(
                    f"[Mafia {game.game_id}] Phase '{phase}' timer running: {current_rem}s remaining"
                )

            logger.info(
                f"[Mafia {game.game_id}] Timer expired for phase '{phase}' (gen {gen}). Triggering transition."
            )

            if game.timer_generation == gen and game.phase_token == token and game.status == phase:
                game.active_timer_phase = None
                await callback(game, context)
            else:
                logger.debug(
                    f"[Mafia {game.game_id}] Timer expired for phase '{phase}' (gen {gen}), but status is '{game.status}' (cur gen {game.timer_generation}). Skipping callback."
                )
        except asyncio.CancelledError:
            logger.debug(f"[Mafia {game.game_id}] Timer task cancelled for phase '{phase}' (gen {gen}).")
            raise
        except Exception as e:
            logger.error(
                f"[Mafia {game.game_id}] Unexpected exception in timer for phase '{phase}' (gen {gen}): {e}",
                exc_info=True
            )
            # Try to safely execute callback if game is still in this phase so game doesn't get stuck
            if game.timer_generation == gen and game.phase_token == token and game.status == phase:
                try:
                    game.active_timer_phase = None
                    await callback(game, context)
                except Exception as inner_e:
                    logger.error(
                        f"[Mafia {game.game_id}] Failed executing fallback callback for phase '{phase}': {inner_e}",
                        exc_info=True
                    )
        finally:
            if game.timer_generation == gen and game.phase_token == token and game.active_timer_phase == phase:
                game.active_timer_phase = None

    task = asyncio.create_task(_timer_runner())
    game.phase_task = task
    return task


async def handle_lobby_timeout(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when lobby timer expires:
    - If len(players) >= MIN_PLAYERS: automatically start the game.
    - Otherwise: cancel game and notify players.
    """
    logger.info(f"[Mafia {game.game_id}] Lobby timer expired. Player count: {len(game.players)}/{MIN_PLAYERS}")
    if game.status != "lobby":
        return

    if len(game.players) >= MIN_PLAYERS:
        await start_mafia_game(game, context)
    else:
        game.cancel_phase_timer()
        game.status = "ended"
        ACTIVE_GAMES.pop(game.chat_id, None)
        cancel_msg = (
            "⏳ <b>Lobby Expired!</b> 🌸\n\n"
            f"The Mafia game recruitment timed out without reaching the required <b>{MIN_PLAYERS}</b> players "
            f"(only <b>{len(game.players)}</b> joined).\n"
            "The lobby has been disbanded. Start a new one with <code>/mafia</code> when you are ready!"
        )
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=cancel_msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[Mafia {game.game_id}] Failed to send lobby timeout message: {e}")


async def start_mafia_game(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Initializes permanent role assignments, delivers private roles, and begins Night 1.
    If private DM fails for any player, halts and alerts in group with instructions.
    Protected against concurrent start calls and never reshuffles assigned roles.
    """
    async with game.start_lock:
        if game.status != "lobby":
            return False

        if len(game.players) < MIN_PLAYERS:
            return False

        game.assign_roles()
        bot_info = context.bot
        bot_username = getattr(bot_info, 'username', 'YuukiBot')

        # Verify and send role DMs to unverified human players
        failed_deliveries: List[MafiaPlayer] = []
        mafia_teammates = [
            {'user_id': p.user_id, 'name': p.name}
            for p in game.get_mafia_players(alive_only=False)
        ]

        for p in game.players.values():
            if p.is_bot or p.user_id < 0:
                p.dm_verified = True
                continue
            if p.dm_verified:
                continue
            role_dm = get_role_dm_text(p.role, teammates=mafia_teammates)
            try:
                await context.bot.send_message(
                    chat_id=p.user_id,
                    text=role_dm,
                    parse_mode="HTML"
                )
                p.dm_verified = True
            except (Forbidden, BadRequest, TelegramError) as e:
                logger.warning(f"[Mafia {game.game_id}] Failed to deliver secret role to user {p.user_id}: {e}")
                failed_deliveries.append(p)

        if failed_deliveries:
            game.status = "lobby"
            failed_tags = ", ".join([p.mention() for p in failed_deliveries])
            err_msg = (
                "⚠️ <b>Cannot Start Game: Private Delivery Failed!</b> 📬\n\n"
                f"The following players have not started a private chat with Yuki or have blocked the bot:\n{failed_tags}\n\n"
                f"👉 <b>How to fix:</b> Each unverified player must tap the button below to start Yuki in private, "
                f"then press <b>▶️ Start Game</b> again!"
            )
            retry_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Start Bot in Private", url=f"https://t.me/{bot_username}?start=mafia_reg")],
                [InlineKeyboardButton("▶️ Retry Start Game", callback_data=f"mafia_start:{game.game_id}")]
            ])
            try:
                await context.bot.send_message(chat_id=game.chat_id, text=err_msg, parse_mode="HTML", reply_markup=retry_kb)
            except Exception as e:
                logger.warning(f"[Mafia {game.game_id}] Failed to send delivery failure message: {e}")
            return False

        # Start Night 1
        game.status = "night"
        game.round_num = 1
        game.phase_token += 1

        # Pin game-start announcement in group
        await send_and_pin_start_announcement(game, context)

        await begin_night_phase(game, context)
        return True


async def send_and_pin_start_announcement(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """
    Sends the game-start announcement message containing player list and initial game information,
    and pins it using Telegram's pin-chat-message functionality.
    - Automatically unpins the previous Mafia game announcement in this chat if tracked.
    - Does not unpin unrelated messages or other administrators' pins.
    - Tracks the pinned message ID separately for each game_id and chat.
    - Handles cases where bot lacks permission to pin messages without interrupting the game.
    - Does not send an extra notification just for pinning (disable_notification=True).
    """
    player_lines = []
    for idx, p in enumerate(game.players.values(), 1):
        player_lines.append(f"{idx}. {p.mention()}{' (Host)' if p.is_host else ''}")
    players_text = "\n".join(player_lines)

    announcement_text = (
        "🎮 <b>The Mafia Game Has Begun!</b> 🌙✨\n\n"
        f"<b>Game ID:</b> <code>{game.game_id}</code>\n"
        f"<b>Host:</b> {format_player_mention(game.host_id, game.host_name)}\n"
        f"<b>Total Citizens:</b> {len(game.players)}\n\n"
        "📜 <b>Confirmed Town Council:</b>\n"
        f"{players_text}\n\n"
        "🎭 Secret roles have been delivered via private DM!\n"
        "Keep your wits sharp, interrogate every clue, and root out the underworld conspirators. "
        "Night 1 is underway!"
    )

    try:
        start_msg = await context.bot.send_message(
            chat_id=game.chat_id,
            text=announcement_text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[Mafia {game.game_id}] Failed to send game-start announcement in {game.chat_id}: {e}")
        return

    # Unpin previous Mafia game announcement in this chat if exists
    prev_pinned_id = LAST_PINNED_MAFIA_MESSAGES.get(game.chat_id)
    if prev_pinned_id:
        try:
            logger.debug(f"[Mafia {game.game_id}] Unpinning previous Mafia announcement msg {prev_pinned_id} in {game.chat_id}")
            await context.bot.unpin_chat_message(
                chat_id=game.chat_id,
                message_id=prev_pinned_id
            )
        except (BadRequest, Forbidden, TelegramError) as e:
            logger.warning(f"[Mafia {game.game_id}] Could not unpin previous Mafia announcement {prev_pinned_id}: {e}")
        except Exception as e:
            logger.warning(f"[Mafia {game.game_id}] Unexpected error unpinning previous announcement: {e}")

    # Pin the new start announcement message
    try:
        logger.debug(f"[Mafia {game.game_id}] Pinning start announcement message {start_msg.message_id} in {game.chat_id}")
        await context.bot.pin_chat_message(
            chat_id=game.chat_id,
            message_id=start_msg.message_id,
            disable_notification=True
        )
        game.pinned_message_id = start_msg.message_id
        LAST_PINNED_MAFIA_MESSAGES[game.chat_id] = start_msg.message_id
    except (BadRequest, Forbidden, TelegramError) as e:
        logger.warning(f"[Mafia {game.game_id}] Lacking permission to pin message or Telegram error: {e}")
    except Exception as e:
        logger.error(f"[Mafia {game.game_id}] Unexpected error while pinning game announcement: {e}")


async def begin_night_phase(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """
    Transitions to night phase and prompts night action holders via private DM.
    """
    game.status = "night"
    game.phase_token += 1
    game.night_actions.clear()
    game.night_mafia_targets.clear()
    game._night_detective_target = None
    game._night_doctor_target = None

    alive_players = game.get_alive_players()
    alive_count = len(alive_players)

    # Announce in group
    night_announcement = get_night_start_text(
        night_num=game.round_num,
        alive_count=alive_count,
        duration=NIGHT_DURATION
    )
    await context.bot.send_message(chat_id=game.chat_id, text=night_announcement, parse_mode="HTML")

    # Send private night prompts
    # 1. Mafia: Can target any living non-mafia player (or any living player)
    mafia_alive = game.get_mafia_players(alive_only=True)
    potential_victims = [p for p in alive_players if p.role != "mafia"] or alive_players

    for m in mafia_alive:
        if m.is_bot or m.user_id < 0:
            continue
        prompt_text = (
            f"🔴 <b>Mafia Meeting: Night {game.round_num}</b> 🗡️\n\n"
            "Choose a target to eliminate tonight. If there are multiple Mafia members, "
            "the player receiving the most syndicate votes will be selected."
        )
        try:
            await context.bot.send_message(
                chat_id=m.user_id,
                text=prompt_text,
                parse_mode="HTML",
                reply_markup=build_night_target_keyboard(game.game_id, "mkill", potential_victims)
            )
        except Exception as e:
            logger.warning(f"Failed to send mafia prompt to {m.user_id}: {e}")

    # 2. Detective: Can investigate any living player other than themselves
    for d in alive_players:
        if d.role == "detective":
            if d.is_bot or d.user_id < 0:
                continue
            det_targets = [p for p in alive_players if p.user_id != d.user_id]
            prompt_text = (
                f"🕵️ <b>Detective Investigation: Night {game.round_num}</b> 🔍\n\n"
                "Select one citizen to investigate. You will secretly learn their true allegiance."
            )
            try:
                await context.bot.send_message(
                    chat_id=d.user_id,
                    text=prompt_text,
                    parse_mode="HTML",
                    reply_markup=build_night_target_keyboard(game.game_id, "dcheck", det_targets)
                )
            except Exception as e:
                logger.warning(f"Failed to send detective prompt to {d.user_id}: {e}")

    # 3. Doctor: Can protect any living player (including self if DOCTOR_SELF_HEAL is True)
    for doc in alive_players:
        if doc.role == "doctor":
            if doc.is_bot or doc.user_id < 0:
                continue
            doc_targets = alive_players if DOCTOR_SELF_HEAL else [p for p in alive_players if p.user_id != doc.user_id]
            prompt_text = (
                f"🩺 <b>Doctor's Clinic: Night {game.round_num}</b> 💉\n\n"
                "Choose one citizen to protect from assassination tonight."
            )
            try:
                await context.bot.send_message(
                    chat_id=doc.user_id,
                    text=prompt_text,
                    parse_mode="HTML",
                    reply_markup=build_night_target_keyboard(game.game_id, "dheal", doc_targets)
                )
            except Exception as e:
                logger.warning(f"Failed to send doctor prompt to {doc.user_id}: {e}")

    # Execute deterministic rule-based NPC night decisions (No AI APIs used)
    process_npc_night_actions(game)

    # Schedule night timeout (passing is_timeout=True)
    async def _on_night_timeout(g, ctx):
        await resolve_night_phase(g, ctx, is_timeout=True)

    start_phase_timer(game, "night", NIGHT_DURATION, _on_night_timeout, context)


def process_npc_night_actions(game: MafiaGame):
    """
    Submits deterministic rule-based night actions for all alive NPC players.
    - Zero external AI APIs used. Pure Python state-based logic.
    - NPC Mafia: Targets living non-mafia player.
    - NPC Detective: Investigates a living citizen other than self.
    - NPC Doctor: Protects an eligible living citizen (self or fellow citizen).
    Actions are stored in per-player dictionaries without overwriting human actions.
    """
    alive_players = game.get_alive_players()
    if not alive_players:
        return

    # 1. NPC Mafia Action
    alive_mafia_npcs = [p for p in game.get_mafia_players(alive_only=True) if p.is_bot]
    potential_targets = [p for p in alive_players if p.role != "mafia"] or [p for p in alive_players if not p.is_bot]
    if potential_targets:
        human_targets = [t for uid, t in game.night_mafia_targets.items() if uid in game.players and not game.players[uid].is_bot]
        for m in alive_mafia_npcs:
            if m.user_id not in game.night_mafia_targets:
                if human_targets and any(p.user_id == human_targets[0] and p.is_alive for p in potential_targets):
                    chosen_id = human_targets[0]
                else:
                    chosen = random.choice(potential_targets)
                    chosen_id = chosen.user_id
                game.night_mafia_targets[m.user_id] = chosen_id
                game.night_actions[m.user_id] = chosen_id
                logger.info(
                    f"[Mafia {game.game_id}] NPC Mafia {m.user_id} ({m.name}) targeted {chosen_id}"
                )

    # 2. NPC Detective Action
    alive_det_npcs = [p for p in alive_players if p.role == "detective" and p.is_bot]
    for d in alive_det_npcs:
        if d.user_id not in game.night_actions:
            det_options = [p for p in alive_players if p.user_id != d.user_id and p.user_id not in game.npc_detective_memory]
            if not det_options:
                det_options = [p for p in alive_players if p.user_id != d.user_id]
            if det_options:
                chosen = random.choice(det_options)
                game.night_actions[d.user_id] = chosen.user_id
                game.npc_detective_memory[chosen.user_id] = 'mafia' if chosen.role == 'mafia' else 'town'
                logger.info(
                    f"[Mafia {game.game_id}] NPC Detective {d.user_id} ({d.name}) investigated {chosen.user_id} (result: {chosen.role})"
                )

    # 3. NPC Doctor Action
    alive_doc_npcs = [p for p in alive_players if p.role == "doctor" and p.is_bot]
    for doc in alive_doc_npcs:
        if doc.user_id not in game.night_actions:
            doc_options = alive_players if DOCTOR_SELF_HEAL else [p for p in alive_players if p.user_id != doc.user_id]
            if doc_options:
                chosen = random.choice(doc_options)
                game.night_actions[doc.user_id] = chosen.user_id
                logger.info(
                    f"[Mafia {game.game_id}] NPC Doctor {doc.user_id} ({doc.name}) protected {chosen.user_id} ({chosen.name})"
                )


async def announce_inactivity_elimination(player: MafiaPlayer, game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """
    Announces player elimination due to inactivity in both group and private DM.
    Follows exact Part 3 requirements:
    - Group announcement:
      🔴 Player eliminated due to inactivity!
      Player: @username
      Reason: Failed to submit the required action before the timer expired.
      The player has been removed from the active game.
    - Private DM:
      🔴 You have been eliminated!
      You were eliminated from the Mafia game because you did not submit your required action before the timer expired.
      You can spectate the remaining game, but you cannot participate in further rounds.
    Handles Telegram DM restrictions gracefully and does not interrupt game.
    """
    username_display = f"@{player.username.lstrip('@')}" if player.username else player.mention()
    group_text = (
        "🔴 <b>Player eliminated due to inactivity!</b>\n\n"
        f"<b>Player:</b> {username_display}\n\n"
        "<b>Reason:</b> Failed to submit the required action before the timer expired.\n\n"
        "The player has been removed from the active game."
    )
    try:
        await context.bot.send_message(
            chat_id=game.chat_id,
            text=group_text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"[Mafia {game.game_id}] Failed to send inactivity announcement to group {game.chat_id}: {e}")

    # Private DM to eliminated player (only for real users, user_id > 0)
    if player.user_id > 0 and not getattr(player, 'is_bot', False):
        dm_text = (
            "🔴 <b>You have been eliminated!</b>\n\n"
            "You were eliminated from the Mafia game because you did not submit your required action before the timer expired.\n\n"
            "You can spectate the remaining game, but you cannot participate in further rounds."
        )
        try:
            await context.bot.send_message(
                chat_id=player.user_id,
                text=dm_text,
                parse_mode="HTML"
            )
        except (Forbidden, BadRequest, TelegramError) as e:
            logger.debug(f"[Mafia {game.game_id}] Inactivity DM failed for user {player.user_id} (restricted/blocked): {e}")
        except Exception as e:
            logger.warning(f"[Mafia {game.game_id}] Inactivity DM unexpected error for user {player.user_id}: {e}")


def check_all_night_actions_submitted(game: MafiaGame) -> bool:
    """Checks if all alive special roles (Mafia, Detective, Doctor) have submitted their night actions."""
    mafia_alive = game.get_mafia_players(alive_only=True)
    if any(m.user_id not in game.night_mafia_targets and m.user_id not in game.night_actions for m in mafia_alive):
        return False

    detectives_alive = [p for p in game.players.values() if p.role == "detective" and p.is_alive]
    if any(d.user_id not in game.night_actions for d in detectives_alive):
        return False

    doctors_alive = [p for p in game.players.values() if p.role == "doctor" and p.is_alive]
    if any(doc.user_id not in game.night_actions for doc in doctors_alive):
        return False

    return True


async def resolve_night_phase(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE, is_timeout: bool = False):
    """
    Resolves night actions deterministically and starts Day discussion.
    Guarantees:
    - Resolves exactly once per night phase.
    - Timer resolution never cancels its own running task.
    - Synchronizes NPC actions on timeout before resolving.
    - Inactivity elimination for living Mafia, Detective, Doctor who failed to act.
    - Villagers are not eliminated for night inactivity.
    - Doctor protection NEVER prevents inactivity elimination. Protection only prevents eligible attacks.
    - Victory conditions checked immediately after inactivity eliminations.
    - Plurality Mafia kill resolved with Doctor protection shield.
    - Victory conditions checked again after victim death.
    """
    async with game.resolution_lock:
        if game.status != "night":
            return
        game.cancel_phase_timer()
        game.status = "resolving_night"

    if is_timeout:
        process_npc_night_actions(game)

    # 1. Record all protected player IDs from living Doctors who submitted a target
    protected_player_ids: Set[int] = set()
    for doc in game.players.values():
        if doc.role == "doctor" and doc.is_alive:
            target = game.night_actions.get(doc.user_id)
            if target is not None:
                protected_player_ids.add(target)

    # 2. Inactivity penalties for required night action roles (only if deadline timed out)
    # Living human Mafia, Doctor, Detective must submit actions; Villagers have no night action.
    # IMPORTANT: Doctor protection NEVER prevents inactivity elimination.
    inactive_eliminated: List[MafiaPlayer] = []

    if is_timeout:
        for p in game.get_alive_players():
            if p.is_bot:
                continue
            if p.role == "mafia":
                if p.user_id not in game.night_mafia_targets and p.user_id not in game.night_actions:
                    inactive_eliminated.append(p)
            elif p.role == "detective":
                if p.user_id not in game.night_actions:
                    inactive_eliminated.append(p)
            elif p.role == "doctor":
                if p.user_id not in game.night_actions:
                    inactive_eliminated.append(p)

    for inact_p in inactive_eliminated:
        if inact_p.is_alive:
            inact_p.is_alive = False
            await announce_inactivity_elimination(inact_p, game, context)

    # Check victory conditions immediately after inactivity eliminations
    win_after_inact = game.check_win_condition()
    if win_after_inact:
        await end_mafia_game(game, win_after_inact, context)
        return

    # 3. Determine Mafia attack target by plurality vote among living Mafia members who submitted targets
    mafia_votes: List[int] = []
    alive_mafia = game.get_mafia_players(alive_only=True)
    for m in alive_mafia:
        tgt = game.night_mafia_targets.get(m.user_id) or game.night_actions.get(m.user_id)
        if tgt is not None and tgt in game.players and game.players[tgt].is_alive:
            mafia_votes.append(tgt)

    final_victim_id: Optional[int] = None
    if mafia_votes:
        counts = Counter(mafia_votes)
        top_candidates = counts.most_common()
        top_vote_cnt = top_candidates[0][1]
        tied_targets = [t for t, cnt in top_candidates if cnt == top_vote_cnt]
        final_victim_id = min(tied_targets)

    # 4. Check Doctor protection against Mafia attack
    saved_by_doctor = False
    if final_victim_id is not None:
        if final_victim_id in protected_player_ids:
            saved_by_doctor = True
            logger.info(
                f"[Mafia {game.game_id}] Victim {final_victim_id} was successfully saved by Doctor protection!"
            )
            final_victim_id = None

    victim_player: Optional[MafiaPlayer] = None
    if final_victim_id and final_victim_id in game.players:
        victim_player = game.players[final_victim_id]
        if victim_player.is_alive:
            victim_player.is_alive = False
            try:
                await context.bot.send_message(
                    chat_id=victim_player.user_id,
                    text=get_farewell_dm_text(),
                    parse_mode="HTML"
                )
            except Exception:
                pass

    # Morning announcement in group
    survivors = game.get_alive_players()
    morning_text = get_morning_announcement(
        day_num=game.round_num,
        victim={
            'user_id': victim_player.user_id,
            'name': victim_player.name,
            'role': victim_player.role
        } if victim_player else None,
        survivor_count=len(survivors),
        doctor_saved=saved_by_doctor
    )

    if inactive_eliminated:
        inact_lines = [
            f"• {format_player_mention(p.user_id, p.name)} ({get_role_display_name(p.role)})"
            for p in inactive_eliminated
        ]
        morning_text += (
            f"\n\n⚡ <b>Inactivity Penalties:</b>\n"
            f"The following citizens failed to perform their nocturnal duty before the deadline and perished in the fog:\n"
            + "\n".join(inact_lines)
        )

    try:
        await context.bot.send_message(chat_id=game.chat_id, text=morning_text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"[Mafia {game.game_id}] Failed to send morning announcement: {e}")

    # Check victory conditions after victim elimination
    win = game.check_win_condition()
    if win:
        await end_mafia_game(game, win, context)
        return

    # Transition to daytime discussion
    await begin_discussion_phase(game, context)


async def begin_discussion_phase(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """Starts daytime discussion with prompts, optional NPC commentary, and timer before voting."""
    game.status = "discussion"
    game.phase_token += 1
    disc_text = get_discussion_start_text(day_num=game.round_num, duration=DISCUSSION_DURATION)

    alive_npcs = [p for p in game.get_alive_players() if p.is_bot]
    if alive_npcs:
        speaker = random.choice(alive_npcs)
        pers = speaker.npc_personality or "yuki"
        other_alive = [p for p in game.get_alive_players() if p.user_id != speaker.user_id]
        target = random.choice(other_alive) if other_alive else None
        target_tag = target.mention() if target else "a suspect"
        npc_comment = get_npc_discussion_text(speaker.mention(), pers, target_tag)
        disc_text += f"\n\n💬 <b>Council Chatter:</b>\n{npc_comment}"

    await context.bot.send_message(
        chat_id=game.chat_id,
        text=disc_text,
        parse_mode="HTML",
        reply_markup=build_discussion_action_keyboard(game.game_id)
    )

    start_phase_timer(game, "discussion", DISCUSSION_DURATION, begin_voting_phase, context)


def process_npc_voting(game: MafiaGame):
    """
    Submits deterministic rule-based day votes for all alive NPC players.
    - Zero external AI APIs used. Pure Python state-based logic.
    - NPC Detective: Votes for confirmed Mafia if known and alive.
    - NPC Mafia: Votes against non-mafia suspects or follows majority.
    - NPC Villager/Doctor: Votes against random living suspects or skips.
    """
    alive_players = game.get_alive_players()
    alive_npcs = [p for p in alive_players if p.is_bot]
    if not alive_npcs:
        return

    known_mafia_ids = [
        target_id for target_id, allegiance in game.npc_detective_memory.items()
        if allegiance == 'mafia' and any(p.user_id == target_id and p.is_alive for p in alive_players)
    ]

    for npc in alive_npcs:
        if npc.user_id in game.day_votes:
            continue

        if npc.role == "detective" and known_mafia_ids:
            game.day_votes[npc.user_id] = known_mafia_ids[0]
            logger.info(f"[Mafia {game.game_id}] NPC Detective {npc.user_id} voted for confirmed Mafia {known_mafia_ids[0]}")
        elif npc.role == "mafia":
            non_mafia_alive = [p for p in alive_players if p.role != "mafia"]
            if non_mafia_alive:
                target = random.choice(non_mafia_alive)
                game.day_votes[npc.user_id] = target.user_id
                logger.info(f"[Mafia {game.game_id}] NPC Mafia {npc.user_id} voted for {target.user_id}")
            else:
                game.day_votes[npc.user_id] = -1
        else:
            candidates = [p for p in alive_players if p.user_id != npc.user_id]
            if candidates and random.random() < 0.8:
                target = random.choice(candidates)
                game.day_votes[npc.user_id] = target.user_id
                logger.info(f"[Mafia {game.game_id}] NPC {npc.user_id} ({npc.role}) voted for {target.user_id}")
            else:
                game.day_votes[npc.user_id] = -1
                logger.info(f"[Mafia {game.game_id}] NPC {npc.user_id} ({npc.role}) voted to skip")


async def begin_voting_phase(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE):
    """Opens voting phase with inline buttons for alive citizens and casts NPC votes."""
    game.status = "voting"
    game.phase_token += 1
    game.day_votes.clear()

    process_npc_voting(game)

    alive = game.get_alive_players()
    vote_announcement = get_voting_start_text(
        day_num=game.round_num,
        alive_count=len(alive),
        duration=VOTING_DURATION
    )
    await context.bot.send_message(
        chat_id=game.chat_id,
        text=vote_announcement,
        parse_mode="HTML",
        reply_markup=build_voting_keyboard(game.game_id, alive)
    )

    async def _on_voting_timeout(g, ctx):
        await resolve_voting_phase(g, ctx, is_timeout=True)

    start_phase_timer(game, "voting", VOTING_DURATION, _on_voting_timeout, context)


async def resolve_voting_phase(game: MafiaGame, context: ContextTypes.DEFAULT_TYPE, is_timeout: bool = False):
    """
    Tallies votes, eliminates inactive non-voters, and eliminates player if plurality is reached.
    Guarantees:
    - Resolves exactly once.
    - Timer runner does not cancel itself.
    - All living human players must vote or explicitly skip.
    - Players failing to vote before timeout are eliminated for inactivity.
    - Victory conditions checked immediately after inactivity eliminations.
    - Dead players' votes are completely ignored.
    - Votes against dead players are rejected.
    """
    async with game.resolution_lock:
        if game.status != "voting":
            return
        game.cancel_phase_timer()
        game.status = "resolving_voting"

    if is_timeout:
        process_npc_voting(game)

    alive_before = game.get_alive_players()

    # 1. Inactivity elimination on timeout: human living players who did not vote or skip
    inactive_eliminated: List[MafiaPlayer] = []
    if is_timeout:
        for p in alive_before:
            if not p.is_bot and p.user_id not in game.day_votes:
                inactive_eliminated.append(p)

    for inact_p in inactive_eliminated:
        if inact_p.is_alive:
            inact_p.is_alive = False
            await announce_inactivity_elimination(inact_p, game, context)

    # Check victory conditions after inactivity eliminations
    win_after_inact = game.check_win_condition()
    if win_after_inact:
        await end_mafia_game(game, win_after_inact, context)
        return

    # 2. Count votes from players who submitted valid votes and ARE CURRENTLY ALIVE
    alive_targets_ids = {p.user_id for p in game.get_alive_players()}
    valid_votes = {
        voter: target
        for voter, target in game.day_votes.items()
        if voter in alive_targets_ids and game.players[voter].is_alive
    }

    lynched_player: Optional[MafiaPlayer] = None
    max_votes = 0

    if valid_votes:
        # Ignore votes targeting players who are no longer alive
        player_votes = [t for t in valid_votes.values() if t in alive_targets_ids]
        skip_count = sum(1 for t in valid_votes.values() if t == -1)

        if player_votes:
            counter = Counter(player_votes)
            top_candidates = counter.most_common()
            top_target, top_cnt = top_candidates[0]

            tied = len([t for t, cnt in top_candidates if cnt == top_cnt]) > 1
            if not tied and top_cnt > skip_count:
                lynched_player = game.players.get(top_target)
                max_votes = top_cnt

    if lynched_player and lynched_player.is_alive:
        lynched_player.is_alive = False
        # Credit elimination
        for voter, target in valid_votes.items():
            if target == lynched_player.user_id:
                game.elimination_credits[voter] = game.elimination_credits.get(voter, 0) + 1

        # Farewell DM
        try:
            await context.bot.send_message(
                chat_id=lynched_player.user_id,
                text=get_farewell_dm_text(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Group announcement
    survivors = game.get_alive_players()
    lynch_msg = get_lynch_announcement(
        day_num=game.round_num,
        victim={
            'user_id': lynched_player.user_id,
            'name': lynched_player.name,
            'role': lynched_player.role
        } if lynched_player else None,
        vote_count=max_votes,
        survivor_count=len(survivors)
    )

    if inactive_eliminated:
        inact_lines = [
            f"• {format_player_mention(p.user_id, p.name)} ({get_role_display_name(p.role)})"
            for p in inactive_eliminated
        ]
        lynch_msg += (
            f"\n\n⚡ <b>Inactivity Penalties:</b>\n"
            f"The following citizens failed to cast a vote before the deadline and were banished for non-participation:\n"
            + "\n".join(inact_lines)
        )

    try:
        await context.bot.send_message(chat_id=game.chat_id, text=lynch_msg, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"[Mafia {game.game_id}] Failed to send lynch announcement: {e}")

    # Check victory conditions after lynch
    win = game.check_win_condition()
    if win:
        await end_mafia_game(game, win, context)
        return

    # Continue to next night
    game.round_num += 1
    await begin_night_phase(game, context)


async def end_mafia_game(game: MafiaGame, winner_team: str, context: ContextTypes.DEFAULT_TYPE):
    """Ends the game, persists stats, announces victory and final roles, and cleans up."""
    game.cancel_phase_timer()
    game.status = "ended"

    survivors = [
        {'user_id': p.user_id, 'name': p.name, 'role': p.role}
        for p in game.get_alive_players()
    ]
    all_players = [
        {'user_id': p.user_id, 'name': p.name, 'role': p.role, 'is_alive': p.is_alive}
        for p in game.players.values()
    ]

    vic_announcement = get_victory_announcement(winner_team, survivors, all_players)
    try:
        await context.bot.send_message(chat_id=game.chat_id, text=vic_announcement, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send victory announcement in {game.chat_id}: {e}")

    # Persist game stats into store
    roles_dict = {p.user_id: p.role for p in game.players.values()}
    try:
        store.record_mafia_game_result(winner_team, roles_dict, game.elimination_credits)
    except Exception as e:
        logger.error(f"Failed to record mafia stats in DB: {e}")

    # Clean up from ACTIVE_GAMES
    ACTIVE_GAMES.pop(game.chat_id, None)


# ----------------------------------------------------------------------------
# USER COMMAND & BUTTON HANDLERS
# ----------------------------------------------------------------------------

async def mafia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main router for /mafia command:
    - /mafia or /mafia new: creates lobby in group
    - /mafia join: joins lobby
    - /mafia leave: leaves lobby
    - /mafia players: views player list
    - /mafia start: starts game
    - /mafia stop: aborts game (host or admin)
    - /mafia stats: view personal/target stats
    - /mafia leaderboard: view top players
    - /mafia rules: view game rules
    - /mafia accuse: accuse player
    - /mafia defend: defend yourself
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return

    args = context.args or []
    subcmd = args[0].lower() if args else "new"

    # Rules, stats, and leaderboard can be executed anywhere (including private DM)
    if subcmd in ("rules", "rule"):
        await send_mafia_rules(update, context)
        return

    if subcmd in ("stats", "stat"):
        await send_mafia_stats(update, context)
        return

    if subcmd in ("leaderboard", "lb", "top"):
        await send_mafia_leaderboard(update, context)
        return

    # All gameplay commands require group or supergroup
    if chat.type not in ("group", "supergroup"):
        await msg.reply_html(
            "🌸 <b>Mafia is a multiplayer social deduction game!</b>\n\n"
            "Please run <code>/mafia</code> inside a Telegram group or supergroup with your friends desu~ 💕"
        )
        return

    if subcmd in ("new", "create"):
        await create_mafia_lobby(update, context)
    elif subcmd == "join":
        await join_mafia_lobby(update, context)
    elif subcmd == "leave":
        await leave_mafia_lobby(update, context)
    elif subcmd == "players":
        await view_mafia_players(update, context)
    elif subcmd == "start":
        await start_mafia_from_cmd(update, context)
    elif subcmd == "stop":
        await stop_mafia_game(update, context)
    elif subcmd == "accuse":
        await handle_accuse_cmd(update, context)
    elif subcmd == "defend":
        await handle_defend_cmd(update, context)
    else:
        await msg.reply_html(
            "🌸 <b>Mafia Game Commands:</b>\n\n"
            "• <code>/mafia</code> — Create a new game lobby\n"
            "• <code>/mafia join</code> — Join the lobby\n"
            "• <code>/mafia leave</code> — Leave the lobby\n"
            "• <code>/mafia players</code> — Show current players\n"
            "• <code>/mafia start</code> — Start the game (Min 5 players)\n"
            "• <code>/mafia stop</code> — Stop active game\n"
            "• <code>/mafia stats</code> — View player statistics\n"
            "• <code>/mafia leaderboard</code> — View top players\n"
            "• <code>/mafia rules</code> — Read game rules & roles\n"
            "• <code>/mafia accuse &lt;player&gt;</code> — Publicly accuse a suspect\n"
            "• <code>/mafia defend</code> — Voice your public self-defense"
        )


async def create_mafia_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if chat.id in ACTIVE_GAMES:
        game = ACTIVE_GAMES[chat.id]
        if game.status != "ended":
            await msg.reply_html(
                f"⚠️ <b>A Mafia game is already in progress in this group!</b>\n"
                f"Phase: <b>{game.status.upper()}</b> | Players: <b>{len(game.players)}</b>\n"
                "Use <code>/mafia stop</code> if you need to abort the current game."
            )
            return

    bot_info = context.bot
    bot_username = getattr(bot_info, 'username', 'YuukiBot')

    game = MafiaGame(
        chat_id=chat.id,
        chat_title=chat.title,
        host_id=user.id,
        host_name=user.first_name,
        host_username=user.username
    )
    ACTIVE_GAMES[chat.id] = game

    lobby_text = get_lobby_created_text(
        host_id=user.id,
        host_name=user.first_name,
        player_count=len(game.players),
        max_players=MAX_PLAYERS,
        min_players=MIN_PLAYERS
    )
    await msg.reply_html(lobby_text, reply_markup=build_lobby_keyboard(game.game_id, bot_username))
    start_phase_timer(game, "lobby", LOBBY_DURATION, handle_lobby_timeout, context)


async def join_mafia_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status != "lobby":
        await msg.reply_html("There is no open Mafia lobby right now! Start one with <code>/mafia</code> 🌸")
        return

    if user.id in game.players:
        await msg.reply_html("You are already in this lobby desu~ 💕")
        return

    if len(game.players) >= MAX_PLAYERS:
        await msg.reply_html("The lobby is full! Maximum player limit reached.")
        return

    game.add_player(user.id, user.first_name, user.username)
    text = get_lobby_join_text(user.id, user.first_name, user.username, len(game.players), MAX_PLAYERS)
    await msg.reply_html(text)


async def leave_mafia_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status != "lobby":
        await msg.reply_html("There is no active lobby you can leave right now.")
        return

    if user.id not in game.players:
        await msg.reply_html("You haven't joined the lobby yet.")
        return

    game.remove_player(user.id)
    text = get_lobby_leave_text(user.id, user.first_name, user.username, len(game.players), MAX_PLAYERS)
    await msg.reply_html(text)


async def view_mafia_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game:
        await msg.reply_html("No Mafia game found in this group.")
        return

    lines = [f"👥 <b>Mafia Game Council ({len(game.players)}/{MAX_PLAYERS}):</b>\n"]
    for idx, p in enumerate(game.players.values(), 1):
        status_disp = " (Host)" if p.is_host else ""
        alive_disp = " [Alive]" if p.is_alive else " [Eliminated]"
        lines.append(f"{idx}. {p.mention()}{status_disp}{alive_disp if game.status != 'lobby' else ''}")

    lines.append(f"\nGame Phase: <b>{game.status.upper()}</b>")
    await msg.reply_html("\n".join(lines))


async def start_mafia_from_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status != "lobby":
        await msg.reply_html("There is no pending lobby to start.")
        return

    # Check host permission or group admin
    from admin import is_target_admin
    is_admin_user = await is_target_admin(chat.id, user.id, context)
    if user.id != game.host_id and not is_admin_user:
        await msg.reply_html("Only the game host or a group administrator can start the game! 🌸")
        return

    if len(game.players) < MIN_PLAYERS:
        await msg.reply_html(
            f"⚠️ <b>Cannot Start:</b> Mafia requires at least <b>{MIN_PLAYERS}</b> players! "
            f"Currently joined: <b>{len(game.players)}</b>."
        )
        return

    await start_mafia_game(game, context)


async def stop_mafia_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status == "ended":
        await msg.reply_html("No active Mafia game to stop.")
        return

    from admin import is_target_admin
    is_admin_user = await is_target_admin(chat.id, user.id, context)
    if user.id != game.host_id and not is_admin_user:
        await msg.reply_html("Only the game host or a group admin can stop the game.")
        return

    game.cancel_phase_timer()
    game.status = "ended"
    ACTIVE_GAMES.pop(chat.id, None)

    await msg.reply_html(
        "🛑 <b>Mafia Game Dismissed!</b> 🌸\n\n"
        f"The game was stopped by {format_player_mention(user.id, user.first_name)}. "
        "The town council has been adjourned."
    )


async def send_mafia_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    rules_text = (
        "📜 <b>Mafia Game Rules & Roles</b> 🎭\n\n"
        "Mafia is an interactive social deduction game where citizens try to root out conspirators "
        "before the town is destroyed!\n\n"
        "👥 <b>Roles:</b>\n"
        "• 🔴 <b>Mafia:</b> Secret conspirators who secretly vote each night to assassinate one innocent citizen.\n"
        "• 🕵️ <b>Detective:</b> Town investigator who secretly checks one player's allegiance each night.\n"
        "• 🩺 <b>Doctor:</b> Town healer who secretly shields one citizen each night from assassination.\n"
        "• 🧑 <b>Villager:</b> Honest citizens who discuss theories and cast lynching votes during the day.\n\n"
        "🌙 <b>Night Phase:</b>\n"
        "Mafia, Detective, and Doctor submit their choices privately via DM with Yuki.\n\n"
        "☀️ <b>Day Phase:</b>\n"
        "1. Morning report announces nocturnal events.\n"
        "2. Town meeting discussion opens with accusations and defense prompts.\n"
        "3. Voting booths open: vote for a suspect or skip lynch.\n"
        "4. Plurality votes eliminate a suspect.\n\n"
        "🏆 <b>Win Conditions:</b>\n"
        "• <b>Town Wins:</b> When all Mafia members are eliminated!\n"
        "• <b>Mafia Wins:</b> When Mafia members equal or outnumber living townsfolk!"
    )
    await msg.reply_html(rules_text)


async def send_mafia_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    target_user_id = user.id
    target_name = user.first_name

    reply_to = msg.reply_to_message
    if reply_to and reply_to.from_user:
        target_user_id = reply_to.from_user.id
        target_name = reply_to.from_user.first_name
    elif context.args and len(context.args) > 1:
        arg_val = context.args[1].strip()
        if arg_val.isdigit():
            target_user_id = int(arg_val)
            target_name = f"User {target_user_id}"

    stats = store.get_mafia_stats(target_user_id)
    text = (
        f"📊 <b>Mafia Career Dossier: {html.escape(target_name)}</b> 🕵️\n\n"
        f"• <b>Games Played:</b> {stats['games_played']}\n"
        f"• <b>Games Won:</b> {stats['games_won']} ({stats['win_rate']}% Win Rate)\n"
        f"• 🔴 <b>Mafia Victories:</b> {stats['mafia_wins']}\n"
        f"• 🕊️ <b>Town Victories:</b> {stats['town_wins']}\n"
        f"• 🎯 <b>Successful Eliminations:</b> {stats['eliminations']}"
    )
    await msg.reply_html(text)


async def send_mafia_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    lb = store.get_mafia_leaderboard(limit=10)

    if not lb:
        await msg.reply_html("🏆 <b>Mafia Leaderboard</b>\n\nNo games recorded yet desu! Be the first to win a game! 🌸")
        return

    lines = ["🏆 <b>Top Town Citizens & Underworld Bosses</b> 🌸✨\n"]
    medals = ["🥇", "🥈", "🥉"]
    for idx, row in enumerate(lb, 1):
        medal = medals[idx - 1] if idx <= 3 else f"<b>{idx}.</b>"
        tag = format_player_tag(row['user_id'], row['full_name'], row['username'])
        lines.append(
            f"{medal} {tag} — <b>{row['games_won']}</b> wins / {row['games_played']} games ({row['win_rate']}%)"
        )

    await msg.reply_html("\n".join(lines))


async def handle_accuse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status != "discussion":
        await msg.reply_html("Accusations can only be voiced during the daytime discussion phase!")
        return

    if user.id not in game.players or not game.players[user.id].is_alive:
        await msg.reply_html("Only living citizens participating in the game may level accusations.")
        return

    reply_to = msg.reply_to_message
    target_user = reply_to.from_user if reply_to and reply_to.from_user else None

    if not target_user and len(context.args) > 1:
        target_name_arg = context.args[1].lstrip('@').lower()
        for p in game.get_alive_players():
            if (p.username and p.username.lower() == target_name_arg) or (p.name.lower() == target_name_arg) or str(p.user_id) == target_name_arg:
                target_user = type('Target', (), {'id': p.user_id, 'first_name': p.name})()
                break

    if not target_user:
        await msg.reply_html("Reply to a player's message with <code>/mafia accuse</code> or use the button!")
        return

    if target_user.id == user.id:
        await msg.reply_html("You cannot accuse yourself desu~ 🌸")
        return

    if target_user.id not in game.players or not game.players[target_user.id].is_alive:
        await msg.reply_html("Target player is not an active living citizen in this game.")
        return

    accuse_text = get_accusation_text(user.id, user.first_name, target_user.id, target_user.first_name)
    await msg.reply_html(accuse_text)


async def handle_defend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    game = ACTIVE_GAMES.get(chat.id)
    if not game or game.status != "discussion":
        await msg.reply_html("You can defend your honor during the daytime discussion phase!")
        return

    if user.id not in game.players or not game.players[user.id].is_alive:
        await msg.reply_html("Only living citizens in this game may speak in self-defense.")
        return

    defense_text = get_defense_text(user.id, user.first_name)
    await msg.reply_html(defense_text)


# ----------------------------------------------------------------------------
# CALLBACK QUERY ROUTER
# ----------------------------------------------------------------------------

async def mafia_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles all callback interactions for Mafia game:
    - mafia_join:<game_id>
    - mafia_leave:<game_id>
    - mafia_players:<game_id>
    - mafia_start:<game_id>
    - mafia_vote:<game_id>:<target_id>
    - mafia_night_mkill:<game_id>:<target_id>
    - mafia_night_dcheck:<game_id>:<target_id>
    - mafia_night_dheal:<game_id>:<target_id>
    - mafia_act_accuse:<game_id>
    - mafia_act_defend:<game_id>
    - mafia_status:<game_id>
    """
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("mafia_"):
        return

    data = query.data
    user = query.from_user
    chat = update.effective_chat

    # Parse callback action
    parts = data.split(":")
    action = parts[0]
    game_id = parts[1] if len(parts) > 1 else ""

    # Find matching game by game_id across all groups
    game = None
    for g in ACTIVE_GAMES.values():
        if g.game_id == game_id:
            game = g
            break

    if not game or game.status == "ended":
        await query.answer("This Mafia match has expired or ended! 🌸", show_alert=True)
        return

    # 1. Join Lobby
    if action == "mafia_join":
        if game.status != "lobby":
            await query.answer("The game has already begun! You cannot join now.", show_alert=True)
            return
        if user.id in game.players:
            await query.answer("You have already joined this game!", show_alert=True)
            return
        if len(game.players) >= MAX_PLAYERS:
            await query.answer("Lobby is full!", show_alert=True)
            return

        game.add_player(user.id, user.first_name, user.username)
        await query.answer("You joined the town council! 🎭")
        bot_info = context.bot
        bot_username = getattr(bot_info, 'username', 'YuukiBot')
        join_txt = get_lobby_join_text(user.id, user.first_name, user.username, len(game.players), MAX_PLAYERS)
        try:
            await query.message.reply_html(join_txt)
        except Exception:
            pass
        return

    # 2. Leave Lobby
    if action == "mafia_leave":
        if game.status != "lobby":
            await query.answer("The game has already started; you cannot leave now.", show_alert=True)
            return
        if user.id not in game.players:
            await query.answer("You are not in this lobby.", show_alert=True)
            return

        game.remove_player(user.id)
        await query.answer("You left the lobby.")
        leave_txt = get_lobby_leave_text(user.id, user.first_name, user.username, len(game.players), MAX_PLAYERS)
        try:
            await query.message.reply_html(leave_txt)
        except Exception:
            pass
        return

    # 3. View Players
    if action == "mafia_players":
        lines = [f"👥 Players ({len(game.players)}/{MAX_PLAYERS}):"]
        for p in game.players.values():
            status_disp = " (Host)" if p.is_host else ""
            lines.append(f"• {p.name}{status_disp}")
        await query.answer("\n".join(lines[:10]), show_alert=True)
        return

    # 4. Start Game
    if action == "mafia_start":
        if game.status != "lobby":
            await query.answer("Game is already in progress!", show_alert=True)
            return

        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can start the game! 🌸", show_alert=True)
            return

        if len(game.players) < MIN_PLAYERS:
            await query.answer("Insufficient players! Choose NPC options below.", show_alert=False)
            prompt_txt = (
                f"🎭 <b>Insufficient Players!</b>\n\n"
                f"Currently joined: <b>{len(game.players)}/{MIN_PLAYERS}</b> required citizens.\n"
                "Would you like to add NPC players to start this game?"
            )
            try:
                await query.message.reply_html(
                    prompt_txt,
                    reply_markup=build_insufficient_npc_keyboard(game.game_id, len(game.players))
                )
            except Exception:
                pass
            return

        if not game.npc_configured:
            await query.answer()
            prompt_txt = "🎭 <b>Would you like to add NPC players to this game?</b>"
            try:
                await query.message.reply_html(
                    prompt_txt,
                    reply_markup=build_npc_prompt_keyboard(game.game_id)
                )
            except Exception:
                pass
            return

        await query.answer("Starting Mafia game... 🌙")
        await start_mafia_game(game, context)
        return

    # NPC Menu / Callbacks
    if action == "mafia_npc_menu":
        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can configure NPCs! 🌸", show_alert=True)
            return
        await query.answer()
        cur_cnt = len(game.players)
        if cur_cnt < MIN_PLAYERS:
            kb = build_insufficient_npc_keyboard(game.game_id, cur_cnt)
        else:
            kb = build_npc_config_keyboard(game.game_id, cur_cnt)
        try:
            await query.message.reply_html("🎭 <b>NPC Player Configuration</b>\nChoose an option:", reply_markup=kb)
        except Exception:
            pass
        return

    if action == "mafia_npc_opt":
        # parts: mafia_npc_opt:<game_id>:yes or mafia_npc_opt:<game_id>:no
        real_game_id = parts[1] if len(parts) > 1 else game_id
        opt = parts[2] if len(parts) > 2 else "no"
        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can configure NPCs! 🌸", show_alert=True)
            return

        if opt == "no":
            game.npc_configured = True
            await query.answer("NPCs disabled. Starting game! 🌸")
            if len(game.players) >= MIN_PLAYERS:
                await start_mafia_game(game, context)
            else:
                await query.message.reply_html(f"Need at least {MIN_PLAYERS} players to start without NPCs!")
            return
        else:
            game.npc_configured = True
            await query.answer("Opening NPC options...")
            cur_cnt = len(game.players)
            cfg_kb = build_npc_config_keyboard(game.game_id, cur_cnt)
            try:
                await query.edit_message_text(
                    f"🎭 <b>NPC Player Configuration</b>\n\nCurrent citizens: <b>{cur_cnt}/{MAX_PLAYERS}</b>\n"
                    "Select how many NPCs to add to the council:",
                    parse_mode="HTML",
                    reply_markup=cfg_kb
                )
            except Exception:
                pass
            return

    if action == "mafia_npc_add":
        # parts: mafia_npc_add:<game_id>:<count>
        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can configure NPCs! 🌸", show_alert=True)
            return

        try:
            count_to_add = int(parts[2])
        except (ValueError, IndexError):
            count_to_add = 0

        added = game.add_npcs(count_to_add)
        game.npc_configured = True
        total = len(game.players)
        await query.answer(f"Added {added} NPC(s)! Total: {total}/{MAX_PLAYERS}")
        try:
            await query.message.reply_html(
                f"🤖 <b>Added {added} NPC citizen(s) to the council!</b>\n"
                f"Total Citizens: <b>{total}/{MAX_PLAYERS}</b>\n\n"
                f"Press <b>▶️ Start Game</b> when ready!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ Start Game Now", callback_data=f"mafia_npc_start_now:{game.game_id}")]
                ])
            )
        except Exception:
            pass
        return

    if action == "mafia_npc_custom_input":
        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can configure NPCs! 🌸", show_alert=True)
            return

        cur_cnt = len(game.players)
        max_can_add = MAX_PLAYERS - cur_cnt
        await query.answer()
        count_btns = []
        row = []
        for n in range(1, min(max_can_add + 1, 12)):
            row.append(InlineKeyboardButton(f"+{n}", callback_data=f"mafia_npc_add:{game.game_id}:{n}"))
            if len(row) == 4:
                count_btns.append(row)
                row = []
        if row:
            count_btns.append(row)

        try:
            await query.message.reply_html(
                f"🔢 <b>Select Custom NPC Count:</b>\n"
                f"Current: {cur_cnt} players. You can add up to {max_can_add} NPCs.",
                reply_markup=InlineKeyboardMarkup(count_btns)
            )
        except Exception:
            pass
        return

    if action == "mafia_npc_start_now":
        from admin import is_target_admin
        is_admin_user = await is_target_admin(game.chat_id, user.id, context)
        if user.id != game.host_id and not is_admin_user:
            await query.answer("Only the game host or group admin can start the game! 🌸", show_alert=True)
            return

        if len(game.players) < MIN_PLAYERS:
            await query.answer(f"Need at least {MIN_PLAYERS} players! Currently: {len(game.players)}", show_alert=True)
            return

        game.npc_configured = True
        await query.answer("Starting Mafia game with NPCs! 🌙")
        await start_mafia_game(game, context)
        return

    # 5. Day Vote
    if action == "mafia_vote":
        if game.status != "voting":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected vote callback from user {user.id}: "
                f"game phase is '{game.status}', expected 'voting'"
            )
            await query.answer("Voting is not open right now!", show_alert=True)
            return

        if user.id not in game.players:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected vote callback from user {user.id}: "
                f"user not found in game"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return

        if not game.players[user.id].is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected vote callback from user {user.id}: "
                f"player is dead"
            )
            await query.answer("Only living players can vote!", show_alert=True)
            return

        target_str = parts[2] if len(parts) > 2 else ""
        if target_str == "skip":
            game.day_votes[user.id] = -1
            logger.info(f"[Mafia {game.game_id}] Player {user.id} ({game.players[user.id].name}) voted to SKIP")
            await query.answer("You voted to skip lynch!")
            total_voted = len(game.day_votes)
            alive_cnt = game.get_alive_count()
            if total_voted >= alive_cnt:
                await resolve_voting_phase(game, context)
            return

        try:
            target_id = int(target_str)
        except ValueError:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected vote callback from user {user.id}: "
                f"invalid target value '{target_str}'"
            )
            await query.answer("Invalid vote target.", show_alert=True)
            return

        if target_id not in game.players or not game.players[target_id].is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected vote callback from user {user.id}: "
                f"target {target_id} not found or not alive"
            )
            await query.answer("Target player is no longer alive!", show_alert=True)
            return

        game.day_votes[user.id] = target_id
        target_player = game.players[target_id]
        logger.info(
            f"[Mafia {game.game_id}] Player {user.id} ({game.players[user.id].name}) voted for {target_id} ({target_player.name})"
        )
        await query.answer(f"Voted for {target_player.name}!")

        # Count votes for target
        votes_for_target = sum(1 for t in game.day_votes.values() if t == target_id)
        if votes_for_target >= 3:
            alert = get_suspicion_alert_text(target_player.user_id, target_player.name)
            try:
                await context.bot.send_message(chat_id=game.chat_id, text=alert, parse_mode="HTML")
            except Exception:
                pass

        # If all alive players have voted, resolve immediately
        if len(game.day_votes) >= game.get_alive_count():
            await resolve_voting_phase(game, context)
        return

    # 6. Night Action: Mafia Kill
    if action == "mafia_night_mkill":
        if game.status != "night":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill callback from user {user.id}: "
                f"game phase is '{game.status}', expected 'night'"
            )
            await query.answer("Night phase has ended!", show_alert=True)
            return

        player = game.players.get(user.id)
        if not player:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill callback from user {user.id}: "
                f"user not in game"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if player.role != "mafia":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill callback from user {user.id}: "
                f"role is '{player.role}', expected 'mafia'"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if not player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill callback from user {user.id}: "
                f"mafia player is dead"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return

        try:
            target_id = int(parts[2])
        except (ValueError, IndexError):
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill from user {user.id}: invalid payload {parts}"
            )
            await query.answer("Invalid target.", show_alert=True)
            return

        target_player = game.players.get(target_id)
        if not target_player or not target_player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected mkill from user {user.id}: target {target_id} not alive"
            )
            await query.answer("Target player is no longer alive!", show_alert=True)
            return

        game.night_mafia_targets[user.id] = target_id
        game.night_actions[user.id] = target_id
        target_name = target_player.name
        logger.info(
            f"[Mafia {game.game_id}] Mafia player {user.id} selected target {target_id} ({target_name})"
        )
        await query.answer(f"Target selected: {target_name} 🎯")
        try:
            await query.edit_message_text(f"🔴 Target confirmed: <b>{html.escape(target_name)}</b>. Awaiting dawn...", parse_mode="HTML")
        except Exception:
            pass

        if check_all_night_actions_submitted(game):
            await resolve_night_phase(game, context)
        return

    # 7. Night Action: Detective Check
    if action == "mafia_night_dcheck":
        if game.status != "night":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck callback from user {user.id}: "
                f"game phase is '{game.status}', expected 'night'"
            )
            await query.answer("Night phase has ended!", show_alert=True)
            return

        player = game.players.get(user.id)
        if not player:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck callback from user {user.id}: "
                f"user not in game"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if player.role != "detective":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck callback from user {user.id}: "
                f"role is '{player.role}', expected 'detective'"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if not player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck callback from user {user.id}: "
                f"detective is dead"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return

        try:
            target_id = int(parts[2])
        except (ValueError, IndexError):
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck from user {user.id}: invalid payload {parts}"
            )
            await query.answer("Invalid target.", show_alert=True)
            return

        target_player = game.players.get(target_id)
        if not target_player or not target_player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck from user {user.id}: target {target_id} not alive"
            )
            await query.answer("Target player is no longer alive!", show_alert=True)
            return

        if target_id == user.id:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dcheck from user {user.id}: self-investigation attempted"
            )
            await query.answer("You cannot investigate yourself!", show_alert=True)
            return

        game.night_detective_target = target_id
        game.night_actions[user.id] = target_id
        logger.info(
            f"[Mafia {game.game_id}] Detective {user.id} checked target {target_id} ({target_player.name})"
        )

        is_threat = (target_player.role == "mafia")
        res_text = (
            f"🕵️ <b>Investigation Report for {format_player_mention(target_player.user_id, target_player.name)}:</b>\n\n"
            f"Result: <b>{'🔴 MAFIA' if is_threat else '🕊️ INNOCENT (Townsperson)'}</b>\n\n"
            "<i>Keep this secret information safe until daylight!</i>"
        )
        await query.answer("Investigation complete!")
        try:
            await query.edit_message_text(res_text, parse_mode="HTML")
        except Exception:
            pass

        if check_all_night_actions_submitted(game):
            await resolve_night_phase(game, context)
        return

    # 8. Night Action: Doctor Heal
    if action == "mafia_night_dheal":
        if game.status != "night":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal callback from user {user.id}: "
                f"game phase is '{game.status}', expected 'night'"
            )
            await query.answer("Night phase has ended!", show_alert=True)
            return

        player = game.players.get(user.id)
        if not player:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal callback from user {user.id}: "
                f"user not in game"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if player.role != "doctor":
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal callback from user {user.id}: "
                f"role is '{player.role}', expected 'doctor'"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return
        if not player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal callback from user {user.id}: "
                f"doctor is dead"
            )
            await query.answer("Unauthorized action.", show_alert=True)
            return

        try:
            target_id = int(parts[2])
        except (ValueError, IndexError):
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal from user {user.id}: invalid payload {parts}"
            )
            await query.answer("Invalid target.", show_alert=True)
            return

        target_player = game.players.get(target_id)
        if not target_player or not target_player.is_alive:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal from user {user.id}: target {target_id} not alive"
            )
            await query.answer("Target player is no longer alive!", show_alert=True)
            return

        if not DOCTOR_SELF_HEAL and target_id == user.id:
            logger.warning(
                f"[Mafia {game.game_id}] Rejected dheal from user {user.id}: self-heal not allowed"
            )
            await query.answer("You cannot protect yourself!", show_alert=True)
            return

        game.night_doctor_target = target_id
        game.night_actions[user.id] = target_id
        target_name = target_player.name
        logger.info(
            f"[Mafia {game.game_id}] Doctor {user.id} protected target {target_id} ({target_name})"
        )

        await query.answer(f"Protection granted to {target_name}!")
        try:
            await query.edit_message_text(f"🩺 You are guarding <b>{html.escape(target_name)}</b> tonight. May they stay safe!", parse_mode="HTML")
        except Exception:
            pass

        if check_all_night_actions_submitted(game):
            await resolve_night_phase(game, context)
        return

    # 9. Discussion Actions
    if action == "mafia_act_accuse":
        if game.status != "discussion":
            await query.answer("Discussion is closed.", show_alert=True)
            return
        if user.id not in game.players or not game.players[user.id].is_alive:
            await query.answer("Only living players can accuse.", show_alert=True)
            return

        alive_targets = [p for p in game.get_alive_players() if p.user_id != user.id]
        if not alive_targets:
            await query.answer("No suspects available.")
            return

        random_suspect = random.choice(alive_targets)
        text = get_accusation_text(user.id, user.first_name, random_suspect.user_id, random_suspect.name)
        await query.answer("Accusation posted!")
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML")
        except Exception:
            pass
        return

    if action == "mafia_act_defend":
        if game.status != "discussion":
            await query.answer("Discussion is closed.", show_alert=True)
            return
        if user.id not in game.players or not game.players[user.id].is_alive:
            await query.answer("Only living players can defend.", show_alert=True)
            return

        text = get_defense_text(user.id, user.first_name)
        await query.answer("Defense stated!")
        try:
            await context.bot.send_message(chat_id=game.chat_id, text=text, parse_mode="HTML")
        except Exception:
            pass
        return

    if action == "mafia_status":
        alive_cnt = game.get_alive_count()
        tot_cnt = len(game.players)
        status_text = (
            f"📋 <b>Council Status:</b>\n"
            f"Phase: <b>{game.status.upper()}</b> (Day {game.round_num})\n"
            f"Living Citizens: <b>{alive_cnt}/{tot_cnt}</b>"
        )
        await query.answer(status_text, show_alert=True)
        return
