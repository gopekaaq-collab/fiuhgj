import os
import sys
import asyncio
import threading
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import core

# ═══════════════════════════════════════════════════
#  STATE MANAGEMENT
# ═══════════════════════════════════════════════════

bot_state = {
    "phrase_loaded": False,
    "proxy_loaded": False,
    "mnemonics": [],
    "proxies": [],
    "trader_threads": [],
    "trader_stop_event": None,
    "checker_running": False,
    "logs": [],
}

STATE_FILE = "awaiting_proxy"

# ═══════════════════════════════════════════════════
#  LUXURY UI FORMATTER
# ═══════════════════════════════════════════════════

def luxury_box(title, items):
    """Render luxury box-style message without emojis."""
    max_len = max(len(title), max((len(i) for i in items), default=0)) + 4
    bar = "━" * max_len
    lines = [
        f"┏{bar}┓",
        f"┃{title.center(max_len)}┃",
        f"┣{bar}┫",
    ]
    for item in items:
        lines.append(f"┃ {item.ljust(max_len - 2)}┃")
    lines.append(f"┗{bar}┛")
    return "\n".join(lines)


def luxury_inline(text):
    return f"▸ {text}"


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    bot_state["logs"].append(f"[{ts}] {msg}")
    if len(bot_state["logs"]) > 100:
        bot_state["logs"] = bot_state["logs"][-100:]


# ═══════════════════════════════════════════════════
#  AUTHORIZATION
# ═══════════════════════════════════════════════════

def is_authorized(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    return user_id == config.AUTHORIZED_CHAT_ID


async def unauthorized(update: Update):
    await update.message.reply_text(
        luxury_box("ACCESS DENIED", [
            "Your ID is not authorized.",
            "",
            f"Your ID: {update.effective_user.id}",
        ])
    )


# ═══════════════════════════════════════════════════
#  KEYBOARD MENUS
# ═══════════════════════════════════════════════════

def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("Upload Phrase", callback_data="menu_phrase"),
            InlineKeyboardButton("Input Proxy", callback_data="menu_proxy"),
        ],
        [
            InlineKeyboardButton("Run Checker", callback_data="run_checker"),
            InlineKeyboardButton("Start Trader", callback_data="start_trader"),
        ],
        [
            InlineKeyboardButton("Stop Trader", callback_data="stop_trader"),
            InlineKeyboardButton("System Status", callback_data="sys_status"),
        ],
        [
            InlineKeyboardButton("View Logs", callback_data="view_logs"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ═══════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await unauthorized(update)
        return

    welcome = luxury_box("CANTOR CONTROL SYSTEM", [
        luxury_inline("Welcome, Commander."),
        "",
        f"Session ID : #{update.effective_user.id}",
        f"Time       : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Phrase     : {'LOADED' if bot_state['phrase_loaded'] else 'EMPTY'}",
        f"Proxy Pool : {len(bot_state['proxies'])} nodes",
        f"Traders    : {len(bot_state['trader_threads'])} active",
        "",
        "Select operation below.",
    ])

    await update.message.reply_text(welcome, reply_markup=main_menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_phrase":
        text = luxury_box("UPLOAD PHRASE", [
            "Send your phrase.txt file",
            "as a document attachment.",
            "",
            "Format: one mnemonic per line",
        ])
        await query.edit_message_text(text, reply_markup=back_menu())

    elif data == "menu_proxy":
        context.user_data[STATE_FILE] = True
        text = luxury_box("INPUT PROXY", [
            "Send proxy list as text.",
            "",
            "Format:",
            "ip:port:user:pass",
            "or http://user:pass@ip:port",
            "",
            "One proxy per line.",
        ])
        await query.edit_message_text(text, reply_markup=back_menu())

    elif data == "run_checker":
        if not bot_state["phrase_loaded"]:
            await query.edit_message_text(
                luxury_box("ERROR", ["No phrase file loaded."]),
                reply_markup=back_menu()
            )
            return
        if bot_state["checker_running"]:
            await query.edit_message_text(
                luxury_box("BUSY", ["Checker is already running."]),
                reply_markup=back_menu()
            )
            return

        await query.edit_message_text(
            luxury_box("CHECKER", ["Initializing balance check..."]),
            reply_markup=back_menu()
        )
        asyncio.create_task(run_checker_task(query))

    elif data == "start_trader":
        if not bot_state["phrase_loaded"]:
            await query.edit_message_text(
                luxury_box("ERROR", ["No phrase file loaded."]),
                reply_markup=back_menu()
            )
            return
        if bot_state["trader_threads"]:
            await query.edit_message_text(
                luxury_box("BUSY", ["Trader is already running."]),
                reply_markup=back_menu()
            )
            return
        await launch_trader_batch(query)

    elif data == "stop_trader":
        await stop_all_traders(query)

    elif data == "sys_status":
        await show_status(query)

    elif data == "view_logs":
        await show_logs(query)

    elif data == "back_main":
        text = luxury_box("CANTOR CONTROL SYSTEM", [
            luxury_inline("Main Menu"),
            "",
            f"Phrase     : {'LOADED' if bot_state['phrase_loaded'] else 'EMPTY'}",
            f"Proxy Pool : {len(bot_state['proxies'])} nodes",
            f"Traders    : {len(bot_state['trader_threads'])} active",
        ])
        await query.edit_message_text(text, reply_markup=main_menu_keyboard())


def back_menu():
    keyboard = [[InlineKeyboardButton("Back to Main Menu", callback_data="back_main")]]
    return InlineKeyboardMarkup(keyboard)


async def launch_trader_batch(query):
    mnemonics = bot_state["mnemonics"]
    proxies = bot_state["proxies"]
    total = len(mnemonics)

    stop_event = threading.Event()
    bot_state["trader_stop_event"] = stop_event

    def status_cb(msg):
        add_log(msg)

    add_log(f"Trader batch starting: {total} wallets, batch size 5")

    # Jalankan batch di background thread
    def run_batch():
        threads, events = core.run_trader_batch(
            mnemonics, proxies,
            batch_size=5,
            batch_delay=(10, 20),
            status_callback=status_cb,
            stop_event=stop_event
        )
        bot_state["trader_threads"] = threads

    t = threading.Thread(target=run_batch, daemon=True)
    t.start()

    await query.edit_message_text(
        luxury_box("TRADER LAUNCHED", [
            f"Total      : {total} wallets",
            f"Batch Size : 5 parallel",
            f"Delay      : 10-20s per batch",
            f"Proxy Pool : {len(proxies)} nodes",
            "",
            "Use 'System Status' to monitor.",
        ]),
        reply_markup=back_menu()
    )


async def stop_all_traders(query):
    if bot_state["trader_stop_event"]:
        bot_state["trader_stop_event"].set()

    count = len(bot_state["trader_threads"])
    bot_state["trader_threads"] = []
    bot_state["trader_stop_event"] = None
    add_log(f"Stopped {count} trader(s)")

    await query.edit_message_text(
        luxury_box("TRADER STOPPED", [f"{count} instance(s) terminated."]),
        reply_markup=back_menu()
    )


async def show_status(query):
    active_count = sum(1 for t in bot_state["trader_threads"] if t[1].is_alive()) if bot_state["trader_threads"] else 0
    lines = [
        f"Phrase File : {'LOADED (' + str(len(bot_state['mnemonics'])) + ')' if bot_state['phrase_loaded'] else 'EMPTY'}",
        f"Proxy Pool  : {len(bot_state['proxies'])} nodes",
        f"Checker     : {'RUNNING' if bot_state['checker_running'] else 'IDLE'}",
        f"Traders     : {active_count} active / {len(bot_state['trader_threads'])} total",
    ]
    await query.edit_message_text(
        luxury_box("SYSTEM STATUS", lines),
        reply_markup=back_menu()
    )


async def show_logs(query):
    logs = bot_state["logs"][-30:]
    text = "\n".join(logs) if logs else "No logs available."
    await query.edit_message_text(
        f"```\n{text}\n```",
        parse_mode="Markdown",
        reply_markup=back_menu()
    )


# ═══════════════════════════════════════════════════
#  FILE & TEXT HANDLERS
# ═══════════════════════════════════════════════════

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await unauthorized(update)
        return

    doc = update.message.document
    if doc.file_name != "phrase.txt":
        await update.message.reply_text(
            luxury_box("ERROR", ["Please send file named exactly 'phrase.txt'"]),
            reply_markup=back_menu()
        )
        return

    file = await context.bot.get_file(doc.file_id)
    await file.download_to_drive(config.PHRASE_FILE)

    with open(config.PHRASE_FILE, "r") as f:
        bot_state["mnemonics"] = [x.strip() for x in f if x.strip()]

    bot_state["phrase_loaded"] = True
    add_log(f"Phrase uploaded: {len(bot_state['mnemonics'])} accounts")

    await update.message.reply_text(
        luxury_box("PHRASE LOADED", [
            f"Accounts : {len(bot_state['mnemonics'])}",
            "File saved successfully.",
        ]),
        reply_markup=main_menu_keyboard()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await unauthorized(update)
        return

    if context.user_data.get(STATE_FILE):
        context.user_data[STATE_FILE] = False
        text = update.message.text
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        bot_state["proxies"] = lines
        bot_state["proxy_loaded"] = True
        add_log(f"Proxy updated: {len(lines)} nodes")

        await update.message.reply_text(
            luxury_box("PROXY UPDATED", [
                f"Nodes : {len(lines)}",
                "Proxy pool active.",
            ]),
            reply_markup=main_menu_keyboard()
        )
        return

    await update.message.reply_text(
        luxury_box("INPUT IGNORED", ["Use the menu buttons to interact."]),
        reply_markup=main_menu_keyboard()
    )


# ═══════════════════════════════════════════════════
#  BACKGROUND TASKS
# ═══════════════════════════════════════════════════

async def run_checker_task(query):
    bot_state["checker_running"] = True
    add_log("Checker started")

    def progress(done, total):
        add_log(f"Checker progress: {done}/{total}")

    try:
        result = await asyncio.to_thread(core.run_checker, bot_state["mnemonics"], bot_state["proxies"], progress)

        lines = [
            f"Accounts   : {len(result['accounts'])}",
            f"Low Bal    : {len(result['low_accounts'])}",
            f"Total CC   : {result['total_cc']:.2f}",
            f"Total USDCx: {result['total_usdc']:.3f} ({result['usdcx_cc']:.2f} CC)",
            f"Total cETH : {result['total_ceth']:.6f} ({result['ceth_cc']:.2f} CC)",
            f"Reward     : {result['total_reward']:.2f} CC",
            f"Grand Total: {result['grand_total']:.2f} CC",
        ]

        if result["low_accounts"]:
            lines.append("")
            lines.append("LOW BALANCE:")
            for acc in result["low_accounts"]:
                lines.append(f"  Acc {acc['idx']}: CC={acc['canton']:.2f} USDCx={acc['usdc']:.2f}")

        text = luxury_box("CHECKER COMPLETE", lines)
        await query.edit_message_text(text, reply_markup=back_menu())

    except Exception as e:
        add_log(f"Checker error: {str(e)}")
        await query.edit_message_text(
            luxury_box("CHECKER FAILED", [str(e)]),
            reply_markup=back_menu()
        )
    finally:
        bot_state["checker_running"] = False
        add_log("Checker finished")


# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════

def main():
    if not config.BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set.")
        sys.exit(1)

    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("[SYSTEM] Bot polling started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
