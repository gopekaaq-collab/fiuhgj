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
    "phrase_loaded":         False,
    "proxy_loaded":          False,
    "mnemonics":             [],
    "proxies":               [],
    "trader_threads":        [],
    "trader_stop_event":     None,
    "checker_running":       False,
    "logs":                  [],
    "awaiting_add_accounts": False,
}

# ═══════════════════════════════════════════════════
#  LUXURY UI FORMATTER  (HTML, no box chars)
# ═══════════════════════════════════════════════════

def lux(title: str, items: list) -> str:
    """
    Clean luxury format:
      ◆  TITLE  ◆
      ──────────────────────────
        item 1
        item 2
    """
    header = f"<b>◆  {title.upper()}  ◆</b>"
    sep    = "<code>──────────────────────────</code>"
    lines  = []
    for item in items:
        lines.append(f"  {item}" if item != "" else "")
    body = "\n".join(lines)
    return f"{header}\n{sep}\n<code>{body}</code>"


def add_log(msg):
    ts    = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    bot_state["logs"].append(entry)
    if len(bot_state["logs"]) > 100:
        bot_state["logs"] = bot_state["logs"][-100:]
    print(entry, flush=True)


# ═══════════════════════════════════════════════════
#  AUTHORIZATION
# ═══════════════════════════════════════════════════

def is_authorized(update: Update) -> bool:
    """Full access — owner only."""
    uid = update.effective_user.id if update.effective_user else None
    return uid == config.AUTHORIZED_CHAT_ID


def is_viewer(update: Update) -> bool:
    """Read-only access — viewer only."""
    uid = update.effective_user.id if update.effective_user else None
    return uid == config.VIEWER_CHAT_ID


def is_any_authorized(update: Update) -> bool:
    """True for both owner and viewer."""
    return is_authorized(update) or is_viewer(update)


async def unauthorized(update: Update):
    await update.message.reply_text(
        lux("ACCESS DENIED", [
            "Your ID is not authorized.",
            "",
            f"Your ID  ·  {update.effective_user.id}",
        ]),
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════
#  KEYBOARD MENUS
# ═══════════════════════════════════════════════════

def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("Upload Phrase",         callback_data="menu_phrase"),
            InlineKeyboardButton("Upload Proxy",          callback_data="menu_proxy"),
        ],
        [
            InlineKeyboardButton("Run Checker",           callback_data="run_checker"),
            InlineKeyboardButton("Start Trader",          callback_data="start_trader"),
        ],
        [
            InlineKeyboardButton("Stop Trader",           callback_data="stop_trader"),
            InlineKeyboardButton("System Status",         callback_data="sys_status"),
        ],
        [
            InlineKeyboardButton("➕ Add Accounts",        callback_data="add_accounts"),
            InlineKeyboardButton("🗑 Remove All Accounts", callback_data="remove_accounts"),
        ],
        [
            InlineKeyboardButton("View Logs",             callback_data="view_logs"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def viewer_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("System Status",  callback_data="sys_status"),
            InlineKeyboardButton("View Logs",      callback_data="view_logs"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_menu():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩  Back to Main Menu", callback_data="back_main")
    ]])


# ═══════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_any_authorized(update):
        await unauthorized(update)
        return

    # ── Viewer (read-only) ──────────────────────────
    if is_viewer(update):
        welcome = lux("CANTOR MONITOR", [
            "Read-only access granted.",
            "",
            f"Session ID  ·  #{update.effective_user.id}",
            f"Time        ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "Available  ·  Status & Logs",
        ])
        await update.message.reply_text(welcome, parse_mode="HTML",
                                        reply_markup=viewer_menu_keyboard())
        return

    # ── Owner (full access) ─────────────────────────
    phrase_status = (
        f"LOADED  ({len(bot_state['mnemonics'])} accounts)"
        if bot_state["phrase_loaded"] else "EMPTY"
    )

    welcome = lux("CANTOR CONTROL SYSTEM", [
        "Welcome, Commander.",
        "",
        f"Session ID  ·  #{update.effective_user.id}",
        f"Time        ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Phrase      ·  {phrase_status}",
        f"Proxy Pool  ·  {len(bot_state['proxies'])} nodes",
        f"Traders     ·  {len(bot_state['trader_threads'])} active",
        "",
        "Select operation below.",
    ])

    await update.message.reply_text(welcome, parse_mode="HTML",
                                    reply_markup=main_menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_any_authorized(update):
        return

    query = update.callback_query
    await query.answer()
    data  = query.data

    # ── Viewer: only allow status & logs ──────────
    VIEWER_ALLOWED = {"sys_status", "view_logs", "back_main"}
    if is_viewer(update) and data not in VIEWER_ALLOWED:
        await query.answer("⛔ Access restricted.", show_alert=True)
        return

    # ── Upload Phrase ──────────────────────────────
    if data == "menu_phrase":
        await query.edit_message_text(
            lux("UPLOAD PHRASE", [
                "Send your phrase.txt file",
                "as a document attachment.",
                "",
                "Format  ·  one mnemonic per line",
            ]),
            parse_mode="HTML", reply_markup=back_menu()
        )

    # ── Upload Proxy ───────────────────────────────
    elif data == "menu_proxy":
        await query.edit_message_text(
            lux("UPLOAD PROXY", [
                "Send your proxy.txt file",
                "as a document attachment.",
                "",
                "Format  ·  ip:port:user:pass",
                "        ·  http://user:pass@ip:port",
                "",
                "One proxy per line.",
            ]),
            parse_mode="HTML", reply_markup=back_menu()
        )

    # ── Run Checker ────────────────────────────────
    elif data == "run_checker":
        if not bot_state["phrase_loaded"]:
            await query.edit_message_text(
                lux("ERROR", ["No phrase file loaded."]),
                parse_mode="HTML", reply_markup=back_menu()
            )
            return
        if bot_state["checker_running"]:
            await query.edit_message_text(
                lux("BUSY", ["Checker is already running."]),
                parse_mode="HTML", reply_markup=back_menu()
            )
            return

        await query.edit_message_text(
            lux("CHECKER", [
                "Initializing balance check...",
                "",
                f"Accounts  ·  {len(bot_state['mnemonics'])}",
                f"Proxies   ·  {len(bot_state['proxies'])} nodes",
            ]),
            parse_mode="HTML", reply_markup=back_menu()
        )
        asyncio.create_task(run_checker_task(query))

    # ── Start Trader ───────────────────────────────
    elif data == "start_trader":
        if not bot_state["phrase_loaded"]:
            await query.edit_message_text(
                lux("ERROR", ["No phrase file loaded."]),
                parse_mode="HTML", reply_markup=back_menu()
            )
            return
        if bot_state["trader_threads"]:
            await query.edit_message_text(
                lux("BUSY", ["Trader is already running."]),
                parse_mode="HTML", reply_markup=back_menu()
            )
            return
        await launch_trader_batch(query)

    # ── Stop Trader ────────────────────────────────
    elif data == "stop_trader":
        await stop_all_traders(query)

    # ── System Status ──────────────────────────────
    elif data == "sys_status":
        await show_status(query)

    # ── View Logs ──────────────────────────────────
    elif data == "view_logs":
        await show_logs(query)

    # ── Add Accounts ───────────────────────────────
    elif data == "add_accounts":
        bot_state["awaiting_add_accounts"] = True
        current = len(bot_state["mnemonics"])
        await query.edit_message_text(
            lux("ADD ACCOUNTS", [
                f"Currently loaded  ·  {current} accounts",
                "",
                "Send your mnemonics as a",
                "plain text message.",
                "",
                "One mnemonic per line.",
                "They will be APPENDED to",
                "the existing list.",
            ]),
            parse_mode="HTML", reply_markup=back_menu()
        )

    # ── Remove All Accounts ────────────────────────
    elif data == "remove_accounts":
        count = len(bot_state["mnemonics"])
        bot_state["mnemonics"]     = []
        bot_state["phrase_loaded"] = False
        if os.path.exists(config.PHRASE_FILE):
            os.remove(config.PHRASE_FILE)
        add_log(f"Removed all {count} accounts")
        await query.edit_message_text(
            lux("ACCOUNTS CLEARED", [
                f"Removed   ·  {count} accounts",
                "List is now empty.",
            ]),
            parse_mode="HTML", reply_markup=back_menu()
        )

    # ── Back to Main Menu ──────────────────────────
    elif data == "back_main":
        if is_viewer(update):
            await query.edit_message_text(
                lux("CANTOR MONITOR", [
                    "Read-only access granted.",
                    "",
                    "Available  ·  Status & Logs",
                ]),
                parse_mode="HTML", reply_markup=viewer_menu_keyboard()
            )
        else:
            phrase_status = (
                f"LOADED  ({len(bot_state['mnemonics'])} accounts)"
                if bot_state["phrase_loaded"] else "EMPTY"
            )
            await query.edit_message_text(
                lux("CANTOR CONTROL SYSTEM", [
                    "Main Menu",
                    "",
                    f"Phrase      ·  {phrase_status}",
                    f"Proxy Pool  ·  {len(bot_state['proxies'])} nodes",
                    f"Traders     ·  {len(bot_state['trader_threads'])} active",
                ]),
                parse_mode="HTML", reply_markup=main_menu_keyboard()
            )


# ═══════════════════════════════════════════════════
#  TRADER
# ═══════════════════════════════════════════════════

async def launch_trader_batch(query):
    mnemonics  = bot_state["mnemonics"]
    proxies    = bot_state["proxies"]
    total      = len(mnemonics)
    stop_event = threading.Event()
    bot_state["trader_stop_event"] = stop_event

    add_log(f"Trader batch starting: {total} wallets")

    def run_batch():
        threads, _ = core.run_trader_batch(
            mnemonics, proxies,
            batch_size=5,
            batch_delay=(10, 20),
            status_callback=add_log,
            stop_event=stop_event
        )
        bot_state["trader_threads"] = threads

    threading.Thread(target=run_batch, daemon=True).start()

    await query.edit_message_text(
        lux("TRADER LAUNCHED", [
            f"Total       ·  {total} wallets",
            f"Batch Size  ·  5 parallel",
            f"Delay       ·  10–20s per batch",
            f"Proxy Pool  ·  {len(proxies)} nodes",
            "",
            "Use System Status to monitor.",
        ]),
        parse_mode="HTML", reply_markup=back_menu()
    )


async def stop_all_traders(query):
    if bot_state["trader_stop_event"]:
        bot_state["trader_stop_event"].set()

    count = len(bot_state["trader_threads"])
    bot_state["trader_threads"]    = []
    bot_state["trader_stop_event"] = None
    add_log(f"Stopped {count} trader(s)")

    await query.edit_message_text(
        lux("TRADER STOPPED", [
            f"Terminated  ·  {count} instance(s)",
        ]),
        parse_mode="HTML", reply_markup=back_menu()
    )


# ═══════════════════════════════════════════════════
#  STATUS & LOGS
# ═══════════════════════════════════════════════════

async def show_status(query):
    active = (
        sum(1 for t in bot_state["trader_threads"] if t[1].is_alive())
        if bot_state["trader_threads"] else 0
    )
    phrase_status = (
        f"LOADED  ({len(bot_state['mnemonics'])})"
        if bot_state["phrase_loaded"] else "EMPTY"
    )
    await query.edit_message_text(
        lux("SYSTEM STATUS", [
            f"Phrase File  ·  {phrase_status}",
            f"Proxy Pool   ·  {len(bot_state['proxies'])} nodes",
            f"Checker      ·  {'RUNNING' if bot_state['checker_running'] else 'IDLE'}",
            f"Traders      ·  {active} active / {len(bot_state['trader_threads'])} total",
        ]),
        parse_mode="HTML", reply_markup=back_menu()
    )


async def show_logs(query):
    logs = bot_state["logs"][-30:]
    text = "\n".join(logs) if logs else "No logs available."
    await query.edit_message_text(
        f"<b>◆  LOGS  ◆</b>\n"
        f"<code>──────────────────────────</code>\n"
        f"<code>{text}</code>",
        parse_mode="HTML", reply_markup=back_menu()
    )


# ═══════════════════════════════════════════════════
#  TEXT & FILE HANDLERS
# ═══════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text — used for Add Accounts flow."""
    if not is_any_authorized(update):
        await unauthorized(update)
        return

    if is_viewer(update):
        return  # viewer has no text-input flows

    if not bot_state.get("awaiting_add_accounts"):
        return

    bot_state["awaiting_add_accounts"] = False
    raw           = update.message.text or ""
    new_mnemonics = [l.strip() for l in raw.splitlines() if l.strip()]

    if not new_mnemonics:
        await update.message.reply_text(
            lux("ERROR", ["No valid mnemonics found."]),
            parse_mode="HTML", reply_markup=main_menu_keyboard()
        )
        return

    before = len(bot_state["mnemonics"])
    bot_state["mnemonics"].extend(new_mnemonics)
    bot_state["phrase_loaded"] = True

    with open(config.PHRASE_FILE, "w") as f:
        f.write("\n".join(bot_state["mnemonics"]))

    after = len(bot_state["mnemonics"])
    add_log(f"Add Accounts: +{len(new_mnemonics)} (total {after})")

    await update.message.reply_text(
        lux("ACCOUNTS ADDED", [
            f"Added     ·  {len(new_mnemonics)} accounts",
            f"Before    ·  {before} accounts",
            f"Total Now ·  {after} accounts",
        ]),
        parse_mode="HTML", reply_markup=main_menu_keyboard()
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_any_authorized(update):
        await unauthorized(update)
        return

    if is_viewer(update):
        return  # viewer cannot upload files

    doc       = update.message.document
    file_name = doc.file_name

    if file_name == "phrase.txt":
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(config.PHRASE_FILE)

        with open(config.PHRASE_FILE, "r") as f:
            bot_state["mnemonics"] = [x.strip() for x in f if x.strip()]

        bot_state["phrase_loaded"] = True
        add_log(f"Phrase uploaded: {len(bot_state['mnemonics'])} accounts")

        await update.message.reply_text(
            lux("PHRASE LOADED", [
                f"Accounts  ·  {len(bot_state['mnemonics'])}",
                "File saved successfully.",
            ]),
            parse_mode="HTML", reply_markup=main_menu_keyboard()
        )

    elif file_name == "proxy.txt":
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(config.PROXY_FILE)

        with open(config.PROXY_FILE, "r") as f:
            bot_state["proxies"] = [x.strip() for x in f if x.strip()]

        bot_state["proxy_loaded"] = True
        add_log(f"Proxy uploaded: {len(bot_state['proxies'])} nodes")

        await update.message.reply_text(
            lux("PROXY LOADED", [
                f"Nodes  ·  {len(bot_state['proxies'])}",
                "File saved successfully.",
            ]),
            parse_mode="HTML", reply_markup=main_menu_keyboard()
        )

    else:
        await update.message.reply_text(
            lux("UNKNOWN FILE", [
                "Accepted files:",
                "",
                "  ·  phrase.txt",
                "  ·  proxy.txt",
            ]),
            parse_mode="HTML", reply_markup=main_menu_keyboard()
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
        result = await asyncio.to_thread(
            core.run_checker,
            bot_state["mnemonics"],
            bot_state["proxies"],
            progress
        )

        lines = [
            f"Accounts    ·  {len(result['accounts'])}",
            f"Low Bal     ·  {len(result['low_accounts'])}",
            "",
            f"Total CC    ·  {result['total_cc']:.2f} CC",
            f"Total USDCx ·  {result['total_usdc']:.3f}  ({result['usdcx_cc']:.2f} CC)",
            f"Total cETH  ·  {result['total_ceth']:.6f}  ({result['ceth_cc']:.2f} CC)",
            f"Reward      ·  {result['total_reward']:.2f} CC",
            f"Grand Total ·  {result['grand_total']:.2f} CC",
            "",
            f"Daily TX    ·  {result['total_daily_tx']}",
            f"Daily Rew   ·  {result['total_daily_reward']:.2f} CC",
            f"Period TX   ·  {result['total_tx_range']}",
            f"Period Rew  ·  {result['total_reward_range']:.2f} CC",
        ]

        if result["low_accounts"]:
            lines.append("")
            lines.append("LOW BALANCE ACCOUNTS :")
            for acc in result["low_accounts"]:
                lines.append(
                    f"  [{acc['idx']:>3}]  CC {acc['canton']:.2f}"
                    f"  USDCx {acc['usdc']:.2f}"
                    f"  cETH {acc['ceth']:.6f}"
                )

        await query.edit_message_text(
            lux("CHECKER COMPLETE", lines),
            parse_mode="HTML", reply_markup=back_menu()
        )

    except Exception as e:
        add_log(f"Checker error: {e}")
        await query.edit_message_text(
            lux("CHECKER FAILED", [str(e)]),
            parse_mode="HTML", reply_markup=back_menu()
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("[SYSTEM] Bot polling started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
