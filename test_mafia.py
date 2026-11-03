import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.error import Forbidden

import store
import mafia
from mafia import MafiaGame, MIN_PLAYERS, ACTIVE_GAMES


@pytest.fixture(autouse=True)
def clean_mafia_state(tmp_path, monkeypatch):
    ACTIVE_GAMES.clear()
    db_file = str(tmp_path / "test_mafia.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()
    yield
    ACTIVE_GAMES.clear()


def make_mock_user(user_id: int, name: str, username: str = None):
    return User(id=user_id, first_name=name, is_bot=False, username=username)


def make_mock_update(user: User, chat_id: int = -10012345, text: str = "/mafia", chat_type: str = "supergroup"):
    u = MagicMock(spec=Update)
    c = Chat(id=chat_id, type=chat_type, title=f"Test Group {chat_id}")
    m = MagicMock(spec=Message)
    m.message_id = 111
    m.text = text
    m.reply_html = AsyncMock()
    m.reply_text = AsyncMock()
    m.reply_to_message = None

    u.effective_chat = c
    u.effective_user = user
    u.effective_message = m
    u.message = m
    u.callback_query = None
    return u, m, c


@pytest.mark.asyncio
async def test_lobby_creation_and_rejection_in_dm():
    u_dm, m_dm, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=101, chat_type="private")
    ctx = MagicMock()
    ctx.args = []

    await mafia.mafia_cmd(u_dm, ctx)
    m_dm.reply_html.assert_called_once()
    assert "inside a Telegram group" in m_dm.reply_html.call_args[0][0]

    # Group creation succeeds
    u_grp, m_grp, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001, chat_type="supergroup")
    await mafia.mafia_cmd(u_grp, ctx)
    assert -1001 in ACTIVE_GAMES
    game = ACTIVE_GAMES[-1001]
    assert game.status == "lobby"
    assert len(game.players) == 1
    assert 101 in game.players
    m_grp.reply_html.assert_called_once()
    assert "Alice" in m_grp.reply_html.call_args[0][0]


@pytest.mark.asyncio
async def test_duplicate_game_prevention():
    u1, m1, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.args = []

    await mafia.mafia_cmd(u1, ctx)
    assert -1001 in ACTIVE_GAMES

    # Try creating second game in same group
    u2, m2, _ = make_mock_update(make_mock_user(102, "Bob"), chat_id=-1001)
    await mafia.mafia_cmd(u2, ctx)
    assert "already in progress" in m2.reply_html.call_args[0][0]


@pytest.mark.asyncio
async def test_lobby_join_leave_and_duplicate_prevention():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)

    # Bob joins
    u2, m2, _ = make_mock_update(make_mock_user(102, "Bob"), chat_id=-1001)
    ctx.args = ["join"]
    await mafia.mafia_cmd(u2, ctx)
    game = ACTIVE_GAMES[-1001]
    assert len(game.players) == 2
    assert 102 in game.players
    assert "Bob" in m2.reply_html.call_args[0][0]

    # Bob attempts duplicate join
    m2.reply_html.reset_mock()
    await mafia.mafia_cmd(u2, ctx)
    assert len(game.players) == 2
    assert "already in this lobby" in m2.reply_html.call_args[0][0]

    # Bob leaves
    ctx.args = ["leave"]
    await mafia.mafia_cmd(u2, ctx)
    assert len(game.players) == 1
    assert 102 not in game.players


@pytest.mark.asyncio
async def test_start_with_fewer_than_five_players_rejected():
    u1, m1, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    # Add 3 more players (total 4)
    for uid in (102, 103, 104):
        game.add_player(uid, f"User{uid}")
    assert len(game.players) == 4

    ctx.args = ["start"]
    await mafia.mafia_cmd(u1, ctx)
    m1.reply_html.assert_called()
    assert "at least" in m1.reply_html.call_args[0][0] and "5" in m1.reply_html.call_args[0][0]
    assert game.status == "lobby"


@pytest.mark.asyncio
async def test_unauthorized_start_rejected():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    # Add 4 players (total 5)
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    # Non-host user 102 attempts to start
    u2, m2, _ = make_mock_update(make_mock_user(102, "Bob"), chat_id=-1001)
    ctx.args = ["start"]

    with patch("admin.is_target_admin", AsyncMock(return_value=False)):
        await mafia.mafia_cmd(u2, ctx)

    assert "Only the game host" in m2.reply_html.call_args[0][0]
    assert game.status == "lobby"


@pytest.mark.asyncio
async def test_private_role_delivery_failure_stops_start_safely():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    # Mock send_message: user 103 has not started the bot
    async def mock_send(chat_id, text, **kwargs):
        if chat_id == 103:
            raise Forbidden("Bot was blocked by the user or never started")
        return MagicMock()

    ctx.bot.send_message.side_effect = mock_send

    ctx.args = ["start"]
    with patch("admin.is_target_admin", AsyncMock(return_value=True)):
        await mafia.mafia_cmd(u1, ctx)

    assert game.status == "lobby"
    # An error notification with instructions was sent
    sent_texts = [call[1]["text"] for call in ctx.bot.send_message.call_args_list if "text" in call[1]]
    assert any("Cannot Start Game: Private Delivery Failed" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_game_start_and_role_distribution():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    ctx.args = ["start"]
    with patch("admin.is_target_admin", AsyncMock(return_value=True)):
        await mafia.mafia_cmd(u1, ctx)

    assert game.status == "night"
    assert game.round_num == 1

    roles = [p.role for p in game.players.values()]
    assert roles.count("mafia") == 1
    assert roles.count("detective") == 1
    assert roles.count("doctor") == 1
    assert roles.count("villager") == 2

    # Clean timers
    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_night_actions_doctor_save_and_detective_result():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")

    # Manually configure roles
    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"
    game.status = "night"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    # 1. Detective investigates Mafia member 101 via callback
    det_cb = MagicMock(spec=Update)
    q_det = MagicMock(spec=CallbackQuery)
    q_det.data = f"mafia_night_dcheck:{game.game_id}:101"
    q_det.from_user = make_mock_user(102, "P102")
    q_det.answer = AsyncMock()
    q_det.edit_message_text = AsyncMock()
    det_cb.callback_query = q_det

    await mafia.mafia_callback_handler(det_cb, ctx)
    assert game.night_detective_target == 101
    q_det.edit_message_text.assert_called_once()
    assert "🔴 MAFIA" in q_det.edit_message_text.call_args[0][0]

    # 2. Mafia targets Villager 104
    maf_cb = MagicMock(spec=Update)
    q_maf = MagicMock(spec=CallbackQuery)
    q_maf.data = f"mafia_night_mkill:{game.game_id}:104"
    q_maf.from_user = make_mock_user(101, "Alice")
    q_maf.answer = AsyncMock()
    q_maf.edit_message_text = AsyncMock()
    maf_cb.callback_query = q_maf

    await mafia.mafia_callback_handler(maf_cb, ctx)
    assert game.night_mafia_targets[101] == 104

    # 3. Doctor protects Villager 104
    doc_cb = MagicMock(spec=Update)
    q_doc = MagicMock(spec=CallbackQuery)
    q_doc.data = f"mafia_night_dheal:{game.game_id}:104"
    q_doc.from_user = make_mock_user(103, "P103")
    q_doc.answer = AsyncMock()
    q_doc.edit_message_text = AsyncMock()
    doc_cb.callback_query = q_doc

    await mafia.mafia_callback_handler(doc_cb, ctx)
    assert game.night_doctor_target == 104

    # Resolving night actions
    await mafia.resolve_night_phase(game, ctx)

    # Because Doctor saved 104, 104 must still be alive!
    assert game.players[104].is_alive is True
    assert game.status == "discussion"

    # Morning announcement mentions save
    sent_msgs = [c[1]["text"] for c in ctx.bot.send_message.call_args_list if "text" in c[1]]
    assert any("everyone survived" in m or "no one died" in m for m in sent_msgs)

    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_night_actions_successful_kill():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")

    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"
    game.status = "night"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    # Mafia targets 104, Doctor heals 105
    game.night_mafia_targets[101] = 104
    game.night_doctor_target = 105

    await mafia.resolve_night_phase(game, ctx)
    # Player 104 should be eliminated
    assert game.players[104].is_alive is False
    assert game.status == "discussion"

    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_voting_and_tie_resolution():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")

    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"

    game.status = "voting"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    # Tied votes: 101 and 102 both get 2 votes
    game.day_votes = {
        101: 102,
        103: 102,
        102: 101,
        104: 101,
        105: -1
    }

    await mafia.resolve_voting_phase(game, ctx)
    # No one eliminated because of tie
    assert all(p.is_alive for p in game.players.values())
    # Should proceed to Night 2
    assert game.status == "night"
    assert game.round_num == 2

    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_voting_plurality_lynch_and_town_win():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")

    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"

    game.status = "voting"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    # Plurality vote against Mafia 101
    game.day_votes = {
        102: 101,
        103: 101,
        104: 101,
        101: 102,
        105: -1
    }

    await mafia.resolve_voting_phase(game, ctx)

    # Mafia 101 should be eliminated
    assert game.players[101].is_alive is False
    # All mafia eliminated -> Town Victory!
    assert -1001 not in ACTIVE_GAMES or game.status == "ended"

    # Verify victory stats recorded
    s102 = store.get_mafia_stats(102)
    assert s102["games_won"] == 1
    assert s102["town_wins"] == 1

    s101 = store.get_mafia_stats(101)
    assert s101["games_played"] == 1
    assert s101["games_won"] == 0


@pytest.mark.asyncio
async def test_mafia_win_condition():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")

    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"

    # Pre-eliminate 102, 103, 104
    game.players[102].is_alive = False
    game.players[103].is_alive = False
    game.players[104].is_alive = False

    # Alive: 1 Mafia (101) vs 1 Villager (105)
    # Mafia equals non-mafia -> Mafia wins!
    win = game.check_win_condition()
    assert win == "mafia"

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    await mafia.end_mafia_game(game, "mafia", ctx)
    s101 = store.get_mafia_stats(101)
    assert s101["games_won"] == 1
    assert s101["mafia_wins"] == 1


@pytest.mark.asyncio
async def test_multiple_groups_simultaneous_isolation():
    u_g1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    u_g2, _, _ = make_mock_update(make_mock_user(201, "Bob"), chat_id=-1002)

    ctx = MagicMock()
    ctx.args = []

    await mafia.mafia_cmd(u_g1, ctx)
    await mafia.mafia_cmd(u_g2, ctx)

    assert -1001 in ACTIVE_GAMES
    assert -1002 in ACTIVE_GAMES

    g1 = ACTIVE_GAMES[-1001]
    g2 = ACTIVE_GAMES[-1002]

    assert g1.game_id != g2.game_id
    assert 101 in g1.players and 101 not in g2.players
    assert 201 in g2.players and 201 not in g1.players


@pytest.mark.asyncio
async def test_stop_command_permission_and_cleanup():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    # Non-host, non-admin cannot stop
    u2, m2, _ = make_mock_update(make_mock_user(102, "Bob"), chat_id=-1001)
    ctx.args = ["stop"]
    with patch("admin.is_target_admin", AsyncMock(return_value=False)):
        await mafia.mafia_cmd(u2, ctx)
    assert -1001 in ACTIVE_GAMES
    assert "Only the game host" in m2.reply_html.call_args[0][0]

    # Host can stop
    ctx.args = ["stop"]
    with patch("admin.is_target_admin", AsyncMock(return_value=False)):
        await mafia.mafia_cmd(u1, ctx)
    assert -1001 not in ACTIVE_GAMES


@pytest.mark.asyncio
async def test_expired_callback_rejection():
    cb_up = MagicMock(spec=Update)
    q = MagicMock(spec=CallbackQuery)
    q.data = "mafia_vote:nonexistent_game_id:101"
    q.answer = AsyncMock()
    cb_up.callback_query = q

    ctx = MagicMock()
    await mafia.mafia_callback_handler(cb_up, ctx)
    q.answer.assert_called_once()
    assert "expired" in q.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_no_ai_api_invoked_during_game():
    with patch("handlers.ai_chat.provider_manager.chat", AsyncMock()) as mock_ai:
        u, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
        ctx = MagicMock()
        ctx.args = []
        await mafia.mafia_cmd(u, ctx)
        mock_ai.assert_not_called()

@pytest.mark.asyncio
async def test_lobby_timer_expiry_start_game():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]

    # Add players to meet minimum
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    assert game.status == "lobby"
    # Fast forward lobby timeout
    await mafia.handle_lobby_timeout(game, ctx)
    assert game.status == "night"
    assert game.round_num == 1
    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_lobby_timer_expiry_cancel_insufficient_players():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.args = []
    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]
    assert len(game.players) == 1

    await mafia.handle_lobby_timeout(game, ctx)
    assert -1001 not in ACTIVE_GAMES
    assert game.status == "ended"
    sent_msgs = [c[1]["text"] for c in ctx.bot.send_message.call_args_list if "text" in c[1]]
    assert any("Lobby Expired" in m for m in sent_msgs)


@pytest.mark.asyncio
async def test_night_timer_expiry_transitions_to_discussion():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")
    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"
    game.status = "night"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    # Trigger resolve_night_phase (as night timer expiry does)
    await mafia.resolve_night_phase(game, ctx)
    assert game.status == "discussion"
    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_discussion_timer_expiry_transitions_to_voting():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")
    game.status = "discussion"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    await mafia.begin_voting_phase(game, ctx)
    assert game.status == "voting"
    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_voting_timer_expiry_finalizes_votes():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"P{uid}")
    # Assign valid roles so win condition is not triggered on 0 mafia
    game.players[101].role = "mafia"
    game.players[102].role = "detective"
    game.players[103].role = "doctor"
    game.players[104].role = "villager"
    game.players[105].role = "villager"

    game.status = "voting"
    game.round_num = 1
    ACTIVE_GAMES[-1001] = game

    ctx = MagicMock()
    ctx.bot = AsyncMock()

    await mafia.resolve_voting_phase(game, ctx)
    # With no votes cast (deadlock), it transitions to night 2
    assert game.status == "night"
    assert game.round_num == 2
    game.cancel_phase_timer()


@pytest.mark.asyncio
async def test_phase_timer_cancellation_and_duplicate_prevention():
    game = MafiaGame(chat_id=-1001, chat_title="Group", host_id=101, host_name="Alice")
    ctx = MagicMock()
    ctx.bot = AsyncMock()

    cb_called = 0
    async def dummy_cb(g, c):
        nonlocal cb_called
        cb_called += 1

    # Start timer 1 for discussion
    task1 = mafia.start_phase_timer(game, "discussion", 1, dummy_cb, ctx)
    assert game.active_timer_phase == "discussion"
    assert not task1.done()

    # Immediately start timer 2 for voting -> cancels task1
    task2 = mafia.start_phase_timer(game, "voting", 1, dummy_cb, ctx)
    assert task1.cancelling() or task1.cancelled() or task1.done()
    assert game.active_timer_phase == "voting"

    # Cancel phase timer
    game.cancel_phase_timer()
    assert task2.cancelling() or task2.cancelled() or task2.done()
    assert game.active_timer_phase is None
    assert game.phase_task is None


@pytest.mark.asyncio
async def test_automatic_message_pinning_and_unpinning():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    start_msg_mock = MagicMock()
    start_msg_mock.message_id = 999
    ctx.bot.send_message = AsyncMock(return_value=start_msg_mock)
    ctx.bot.pin_chat_message = AsyncMock()
    ctx.bot.unpin_chat_message = AsyncMock()
    ctx.args = []

    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    # Set a previous pinned mafia message in this chat
    mafia.LAST_PINNED_MAFIA_MESSAGES[-1001] = 888

    await mafia.start_mafia_game(game, ctx)

    # Verifications:
    # 1. Previous pinned message was unpinned
    ctx.bot.unpin_chat_message.assert_called_once_with(chat_id=-1001, message_id=888)
    # 2. New message was pinned with disable_notification=True
    ctx.bot.pin_chat_message.assert_called_once_with(chat_id=-1001, message_id=999, disable_notification=True)
    assert game.pinned_message_id == 999
    assert mafia.LAST_PINNED_MAFIA_MESSAGES[-1001] == 999

    # 3. When game ends, pinned message is preserved
    await mafia.end_mafia_game(game, "town", ctx)
    # unpin_chat_message shouldn't be called on end
    assert ctx.bot.unpin_chat_message.call_count == 1
    assert mafia.LAST_PINNED_MAFIA_MESSAGES[-1001] == 999


@pytest.mark.asyncio
async def test_message_pinning_permission_failure_graceful():
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    start_msg_mock = MagicMock()
    start_msg_mock.message_id = 999
    ctx.bot.send_message = AsyncMock(return_value=start_msg_mock)
    from telegram.error import BadRequest
    ctx.bot.pin_chat_message = AsyncMock(side_effect=BadRequest("Not enough rights to pin messages"))
    ctx.bot.unpin_chat_message = AsyncMock()
    ctx.args = []

    await mafia.mafia_cmd(u1, ctx)
    game = ACTIVE_GAMES[-1001]
    for uid in (102, 103, 104, 105):
        game.add_player(uid, f"User{uid}")

    # Game should start smoothly without raising an unhandled exception
    started = await mafia.start_mafia_game(game, ctx)
    assert started is True
    assert game.status == "night"
    game.cancel_phase_timer()

@pytest.mark.asyncio
async def test_full_lifecycle_and_simultaneous_games():
    # Group 1 and Group 2 simultaneously
    u1, _, _ = make_mock_update(make_mock_user(101, "Alice"), chat_id=-1001)
    u2, _, _ = make_mock_update(make_mock_user(201, "Bob"), chat_id=-1002)

    ctx = MagicMock()
    ctx.bot = AsyncMock()
    start_msg1 = MagicMock()
    start_msg1.message_id = 1111
    start_msg2 = MagicMock()
    start_msg2.message_id = 2222

    async def mock_send(chat_id, text, **kwargs):
        m = MagicMock()
        if chat_id == -1001:
            m.message_id = 1111
        elif chat_id == -1002:
            m.message_id = 2222
        else:
            m.message_id = 3333
        return m

    ctx.bot.send_message.side_effect = mock_send
    ctx.args = []

    # Create lobbies
    await mafia.mafia_cmd(u1, ctx)
    await mafia.mafia_cmd(u2, ctx)

    g1 = ACTIVE_GAMES[-1001]
    g2 = ACTIVE_GAMES[-1002]

    # Populate players
    for uid in (102, 103, 104, 105):
        g1.add_player(uid, f"User{uid}")
    for uid in (202, 203, 204, 205):
        g2.add_player(uid, f"User{uid}")

    # Start games
    await mafia.start_mafia_game(g1, ctx)
    await mafia.start_mafia_game(g2, ctx)

    assert g1.status == "night"
    assert g2.status == "night"
    assert g1.pinned_message_id == 1111
    assert g2.pinned_message_id == 2222
    assert g1.game_id != g2.game_id

    # Resolve Night 1 on g1
    await mafia.resolve_night_phase(g1, ctx)
    assert g1.status == "discussion"
    assert g2.status == "night"  # g2 remains in night!

    # Discussion ends on g1 -> starts voting
    await mafia.begin_voting_phase(g1, ctx)
    assert g1.status == "voting"
    assert g2.status == "night"

    # Eliminate all mafia in g1
    mafia_p = [p for p in g1.players.values() if p.role == "mafia"][0]
    town_ps = [p for p in g1.players.values() if p.role != "mafia"]
    for tp in town_ps:
        g1.day_votes[tp.user_id] = mafia_p.user_id

    await mafia.resolve_voting_phase(g1, ctx)
    # g1 ends
    assert g1.status == "ended"
    assert -1001 not in ACTIVE_GAMES

    # g2 is still going
    assert -1002 in ACTIVE_GAMES
    assert g2.status == "night"

    g2.cancel_phase_timer()
