import html
import uuid
import asyncio
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

GAMES: Dict[str, Dict[str, Any]] = {}
INVITATIONS: Dict[str, Dict[str, Any]] = {}


def format_user_mention(user_id: int, name: str) -> str:
    escaped_name = html.escape(name or 'User')
    return f"<b>{escaped_name}</b> (<a href=\"tg://user?id={user_id}\">tap to open profile</a>)"


def check_winner(board: list):
    lines = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
        (0, 3, 6), (1, 4, 7), (2, 5, 8),  # cols
        (0, 4, 8), (2, 4, 6)              # diagonals
    ]
    for a, b, c in lines:
        if board[a] != ' ' and board[a] == board[b] == board[c]:
            return board[a]
    if ' ' not in board:
        return 'draw'
    return None


def minimax(board: list, depth: int, is_maximizing: bool) -> int:
    res = check_winner(board)
    if res == 'O':
        return 10 - depth
    if res == 'X':
        return depth - 10
    if res == 'draw':
        return 0

    if is_maximizing:
        best_score = -1000
        for i in range(9):
            if board[i] == ' ':
                board[i] = 'O'
                score = minimax(board, depth + 1, False)
                board[i] = ' '
                best_score = max(best_score, score)
        return best_score
    else:
        best_score = 1000
        for i in range(9):
            if board[i] == ' ':
                board[i] = 'X'
                score = minimax(board, depth + 1, True)
                board[i] = ' '
                best_score = min(best_score, score)
        return best_score


def get_bot_move(board: list) -> int:
    best_score = -1000
    best_move = -1
    for i in range(9):
        if board[i] == ' ':
            board[i] = 'O'
            score = minimax(board, 0, False)
            board[i] = ' '
            if score > best_score:
                best_score = score
                best_move = i
    return best_move


def build_board_keyboard(game_id: str, board: list, disabled: bool = False) -> InlineKeyboardMarkup:
    keyboard = []
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            cell = board[idx]
            if cell == 'X':
                btn_text = '❌'
            elif cell == 'O':
                btn_text = '⭕'
            else:
                btn_text = '⬜'

            cb_data = f"ttt_noop:{game_id}" if disabled or cell != ' ' else f"ttt_move:{game_id}:{idx}"
            row.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


async def expire_invitation(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if context.job else {}
    game_id = data.get('game_id')
    inv = INVITATIONS.pop(game_id, None)
    if not inv or inv.get('status') != 'pending':
        return

    chat_id = inv['chat_id']
    msg_id = inv['msg_id']
    p2 = inv['p2']
    p2_tag = format_user_mention(p2['id'], p2['name'])

    text = (
        "⌛ <b>Tournament Invitation Expired!</b> 🌸\n\n"
        f"{p2_tag} didn't respond in 30 seconds desu~ The tournament match has been dismissed! 💕"
    )
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode='HTML')
    except Exception:
        pass


async def ttt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return

    bot_info = context.bot
    bot_id = bot_info.id
    bot_name = getattr(bot_info, 'first_name', 'Yuki')

    reply_to = msg.reply_to_message
    target_user = reply_to.from_user if reply_to and reply_to.from_user else None

    # Check if command is used in reply to another user
    if target_user and target_user.id != user.id and not target_user.is_bot:
        # PvP Tournament mode
        game_id = str(uuid.uuid4())[:8]
        p1 = {'id': user.id, 'name': user.first_name}
        p2 = {'id': target_user.id, 'name': target_user.first_name}

        p1_tag = format_user_mention(p1['id'], p1['name'])
        p2_tag = format_user_mention(p2['id'], p2['name'])

        text = (
            "🏆 <b>Tic Tac Toe Tournament Challenge!</b> 🎮✨\n\n"
            f"{p1_tag} has challenged {p2_tag} to a Tic Tac Toe tournament match!\n\n"
            f"{p2_tag}, do you accept this challenge? You have 30 seconds to respond desu~ 💕"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Yes ⭕", callback_data=f"ttt_accept:{game_id}"),
                InlineKeyboardButton("No ❌", callback_data=f"ttt_decline:{game_id}")
            ]
        ])

        sent_msg = await msg.reply_html(text, reply_markup=keyboard)

        INVITATIONS[game_id] = {
            'p1': p1,
            'p2': p2,
            'chat_id': chat.id,
            'msg_id': sent_msg.message_id,
            'status': 'pending'
        }

        # Schedule 30 second timeout
        if context.job_queue:
            context.job_queue.run_once(
                expire_invitation,
                when=30,
                data={'game_id': game_id},
                name=f"ttt_inv_expire_{game_id}"
            )
        else:
            # Fallback asyncio timer if job_queue is not active in context
            async def fallback_timer():
                await asyncio.sleep(30)
                dummy_job = type('Job', (), {'data': {'game_id': game_id}})()
                dummy_ctx = type('Ctx', (), {'job': dummy_job, 'bot': context.bot})()
                await expire_invitation(dummy_ctx)
            asyncio.create_task(fallback_timer())
        return

    if target_user and target_user.id == user.id:
        await msg.reply_html(
            f"🌸 You can't challenge yourself to a tournament desu~! If you want to play against me, run /ttt without replying to anyone! 💕"
        )
        return

    if target_user and target_user.is_bot and target_user.id != bot_id:
        await msg.reply_html(
            f"🌸 You can't challenge another bot to a tournament desu~! If you want to play against me, run /ttt without replying! 💕"
        )
        return

    # Single Player vs Bot (Yuki)
    game_id = str(uuid.uuid4())[:8]
    p1 = {'id': user.id, 'name': user.first_name}
    p2 = {'id': bot_id, 'name': bot_name}

    GAMES[game_id] = {
        'p1': p1,
        'p2': p2,
        'board': [' '] * 9,
        'turn': p1['id'],
        'vs_bot': True
    }

    p1_tag = format_user_mention(p1['id'], p1['name'])
    p2_tag = format_user_mention(p2['id'], p2['name'])

    text = (
        "🎮 <b>Tic Tac Toe Match Started!</b> 🌸✨\n\n"
        f"❌ Player 1: {p1_tag}\n"
        f"⭕ Player 2: {p2_tag}\n\n"
        f"Current Turn: ❌ {p1_tag} desu~ 💕"
    )

    await msg.reply_html(text, reply_markup=build_board_keyboard(game_id, GAMES[game_id]['board']))


async def ttt_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(('ttt_accept:', 'ttt_decline:', 'ttt_move:', 'ttt_noop:')):
        return

    data = query.data
    user = query.from_user

    if data.startswith('ttt_noop:'):
        await query.answer()
        return

    # Handle Invitation Accept / Decline
    if data.startswith(('ttt_accept:', 'ttt_decline:')):
        action, game_id = data.split(':', 1)
        inv = INVITATIONS.get(game_id)

        if not inv or inv.get('status') != 'pending':
            await query.answer("Gomen ne~! This tournament invitation has expired or ended desu! 🌸", show_alert=True)
            return

        p1 = inv['p1']
        p2 = inv['p2']

        if user.id != p2['id']:
            await query.answer("Gomen ne~! Only the challenged player can respond to this tournament invitation desu! 🌸", show_alert=True)
            return

        p1_tag = format_user_mention(p1['id'], p1['name'])
        p2_tag = format_user_mention(p2['id'], p2['name'])

        if action == 'ttt_decline':
            inv['status'] = 'declined'
            INVITATIONS.pop(game_id, None)
            await query.answer("Tournament challenge declined! 🌸")

            text = (
                "❌ <b>Tournament Challenge Declined!</b> 🌸\n\n"
                f"{p2_tag} declined the Tic Tac Toe tournament match desu~ 💕"
            )
            try:
                await query.edit_message_text(text, parse_mode='HTML')
            except Exception:
                pass
            return

        if action == 'ttt_accept':
            inv['status'] = 'accepted'
            INVITATIONS.pop(game_id, None)
            await query.answer("Tournament challenge accepted! 🎮✨")

            GAMES[game_id] = {
                'p1': p1,
                'p2': p2,
                'board': [' '] * 9,
                'turn': p1['id'],
                'vs_bot': False
            }

            text = (
                "🎮 <b>Tic Tac Toe Tournament Match!</b> 🌸✨\n\n"
                f"❌ Player 1: {p1_tag}\n"
                f"⭕ Player 2: {p2_tag}\n\n"
                f"Current Turn: ❌ {p1_tag} desu~ 💕"
            )
            try:
                await query.edit_message_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=build_board_keyboard(game_id, GAMES[game_id]['board'])
                )
            except Exception:
                pass
            return

    # Handle Game Move
    if data.startswith('ttt_move:'):
        _, game_id, idx_str = data.split(':', 2)
        try:
            idx = int(idx_str)
        except ValueError:
            await query.answer()
            return

        game = GAMES.get(game_id)
        if not game:
            await query.answer("Gomen ne~! This match is no longer active desu! 🌸", show_alert=True)
            return

        p1 = game['p1']
        p2 = game['p2']

        if user.id not in (p1['id'], p2['id']):
            await query.answer("Gomen ne~! You are not a player in this match desu! 🌸", show_alert=True)
            return

        if user.id != game['turn']:
            await query.answer("Gomen ne~! It's not your turn right now desu! 🌸", show_alert=True)
            return

        if game['board'][idx] != ' ':
            await query.answer("That spot is already taken desu~! 🌸", show_alert=True)
            return

        # Make player move
        symbol = 'X' if user.id == p1['id'] else 'O'
        game['board'][idx] = symbol

        p1_tag = format_user_mention(p1['id'], p1['name'])
        p2_tag = format_user_mention(p2['id'], p2['name'])

        res = check_winner(game['board'])

        if res:
            GAMES.pop(game_id, None)
            await query.answer()

            if res == 'draw':
                text = (
                    "🤝 <b>Tic Tac Toe Match Ended!</b> 🌸✨\n\n"
                    "It's a draw! Both players fought super well desu~! 💕 (⁠人⁠*⁠´⁠∀⁠｀⁠)"
                )
            else:
                winner_user = p1 if res == 'X' else p2
                winner_tag = format_user_mention(winner_user['id'], winner_user['name'])
                win_symbol = '❌' if res == 'X' else '⭕'
                text = (
                    "🎉 <b>Tic Tac Toe Match Ended!</b> 🌸✨\n\n"
                    f"🏆 Winner: {win_symbol} {winner_tag}! Congratulations desu~! 💕 (⁠≧⁠▽⁠≦⁠)🌸"
                )

            try:
                await query.edit_message_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=build_board_keyboard(game_id, game['board'], disabled=True)
                )
            except Exception:
                pass
            return

        # Game continues: handle next turn
        if game['vs_bot']:
            # Bot AI turn
            bot_idx = get_bot_move(game['board'])
            if bot_idx != -1:
                game['board'][bot_idx] = 'O'

            bot_res = check_winner(game['board'])
            if bot_res:
                GAMES.pop(game_id, None)
                await query.answer()

                if bot_res == 'draw':
                    text = (
                        "🤝 <b>Tic Tac Toe Match Ended!</b> 🌸✨\n\n"
                        "It's a draw! You played super well against me desu~! 💕 (⁠人⁠*⁠´⁠∀⁠｀⁠)"
                    )
                else:
                    text = (
                        "🎉 <b>Tic Tac Toe Match Ended!</b> 🌸✨\n\n"
                        f"🏆 Winner: ⭕ {p2_tag}! Yuki won this round desu~! 💕 (⁠≧⁠▽⁠≦⁠)🌸"
                    )

                try:
                    await query.edit_message_text(
                        text,
                        parse_mode='HTML',
                        reply_markup=build_board_keyboard(game_id, game['board'], disabled=True)
                    )
                except Exception:
                    pass
                return

            # Turn back to P1
            game['turn'] = p1['id']
            text = (
                "🎮 <b>Tic Tac Toe Match!</b> 🌸✨\n\n"
                f"❌ Player 1: {p1_tag}\n"
                f"⭕ Player 2: {p2_tag}\n\n"
                f"Current Turn: ❌ {p1_tag} desu~ 💕"
            )
            await query.answer()
            try:
                await query.edit_message_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=build_board_keyboard(game_id, game['board'])
                )
            except Exception:
                pass
            return
        else:
            # PvP mode turn switch
            next_user = p2 if game['turn'] == p1['id'] else p1
            next_symbol = '⭕' if game['turn'] == p1['id'] else '❌'
            game['turn'] = next_user['id']
            next_tag = format_user_mention(next_user['id'], next_user['name'])

            text = (
                "🎮 <b>Tic Tac Toe Tournament Match!</b> 🌸✨\n\n"
                f"❌ Player 1: {p1_tag}\n"
                f"⭕ Player 2: {p2_tag}\n\n"
                f"Current Turn: {next_symbol} {next_tag} desu~ 💕"
            )
            await query.answer()
            try:
                await query.edit_message_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=build_board_keyboard(game_id, game['board'])
                )
            except Exception:
                pass
            return
