"""Регистрация обработчиков Telegram."""

from telegram.ext import Application, ChatMemberHandler, CommandHandler, MessageHandler, filters

from bot.handlers.chat import handle_message
from bot.handlers.commands import cmd_delete, cmd_reset, cmd_start
from bot.handlers.common import on_error, on_my_chat_member
from bot.handlers.pray import cmd_pray, cmd_praystats


def register(app: Application) -> None:
    app.add_error_handler(on_error)
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("pray", cmd_pray))
    app.add_handler(CommandHandler("praystats", cmd_praystats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
