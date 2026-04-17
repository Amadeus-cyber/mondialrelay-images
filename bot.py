#!/usr/bin/env python3
"""
ZeuScraper — Bot Telegram
Commandes : /start  /index  /url  /bgp  /ripe  /help
Le bot peut aussi recevoir un fichier .txt directement.
"""

import os
import logging
import tempfile
from io import BytesIO

try:
    from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                          ReplyKeyboardMarkup, ReplyKeyboardRemove)
    from telegram.ext import (ApplicationBuilder, CommandHandler,
                               MessageHandler, CallbackQueryHandler,
                               ConversationHandler, ContextTypes, filters)
    from core import (scrape_index, scrape_url, scrape_bgp, scrape_ripe,
                      extract, build_lists)
except ImportError:
    print("Deps manquants. Lance : pip install python-telegram-bot requests beautifulsoup4")
    raise

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Token — mets le tien ici ou via variable d'environnement BOT_TOKEN ────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "METS_TON_TOKEN_ICI")

# ── États de conversation ──────────────────────────────────────────────────────
(
    STATE_MENU,
    STATE_INDEX_URL, STATE_INDEX_EXT, STATE_INDEX_WORKERS, STATE_INDEX_LIMIT,
    STATE_URL_URL,   STATE_URL_FOLLOW, STATE_URL_WORKERS,
    STATE_BGP_ASN,
    STATE_RIPE_QUERY,
) = range(10)

# ── Keyboard principal ────────────────────────────────────────────────────────

MAIN_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗂  Index Apache",  callback_data="index"),
     InlineKeyboardButton("🔗  URL directe",   callback_data="url")],
    [InlineKeyboardButton("🌐  BGP (ASN)",     callback_data="bgp"),
     InlineKeyboardButton("📡  RIPE",          callback_data="ripe")],
    [InlineKeyboardButton("ℹ️  Aide",          callback_data="help")],
])

BANNER = (
    "⚡ *ZeuScraper v3.0*\n"
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "Domain \\& IP Range Scraper\n\n"
    "Choisis un mode ou envoie directement un fichier \\`.txt\\` "
    "pour extraire les domaines et IPs\\."
)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def send_lists(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     results: dict, source_label: str):
    domains, all_ips = build_lists(results)

    msg = (
        f"✅ *Scraping terminé* — _{source_label}_\n\n"
        f"📄 Domaines  : `{len(domains)}`\n"
        f"🌐 IPs/CIDRs : `{len(all_ips)}`"
    )
    await update.effective_message.reply_text(msg, parse_mode="MarkdownV2")

    if domains:
        buf = BytesIO("\n".join(domains).encode())
        buf.name = "domains.txt"
        await update.effective_message.reply_document(buf, filename="domains.txt",
                                                       caption="📄 Liste des domaines")

    if all_ips:
        buf = BytesIO("\n".join(all_ips).encode())
        buf.name = "ips.txt"
        await update.effective_message.reply_document(buf, filename="ips.txt",
                                                       caption="🌐 Liste des IPs / CIDRs")

    return await show_main_menu(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🏠 *Menu principal* — Choisis un mode :"
    msg  = update.effective_message
    if msg:
        await msg.reply_text(text, reply_markup=MAIN_KB, parse_mode="Markdown")
    return STATE_MENU


async def typing_action(context, chat_id):
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(BANNER, parse_mode="MarkdownV2")
    return await show_main_menu(update, context)


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *ZeuScraper — Aide*\n\n"
        "*Commandes :*\n"
        "  `/start`  — Menu principal\n"
        "  `/index`  — Index Apache \\(répertoire\\)\n"
        "  `/url`    — URL directe\n"
        "  `/bgp`    — Plages IP depuis ASN\n"
        "  `/ripe`   — Plages IP RIPE NCC\n"
        "  `/help`   — Cette aide\n\n"
        "*Envoie un fichier :*\n"
        "  Envoie n'importe quel fichier \\`.txt\\` et le bot "
        "extraira automatiquement les domaines et IPs qu'il contient\\."
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")
    return STATE_MENU


# ── Callback inline keyboard ──────────────────────────────────────────────────

async def callback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "index":
        await query.message.reply_text(
            "🗂 *Index Apache*\n\nEnvoie l\\`URL de l'index \\(ex: https://example.com/lists/\\)\\.\n\n"
            "Tape /annuler pour revenir\\.",
            parse_mode="MarkdownV2"
        )
        return STATE_INDEX_URL

    elif data == "url":
        await query.message.reply_text(
            "🔗 *URL directe*\n\nEnvoie l'URL à scraper\\.\n\nTape /annuler pour revenir\\.",
            parse_mode="MarkdownV2"
        )
        return STATE_URL_URL

    elif data == "bgp":
        await query.message.reply_text(
            "🌐 *BGP — Numéro ASN*\n\nEx: `15169` ou `AS15169`\\.\n\nTape /annuler pour revenir\\.",
            parse_mode="MarkdownV2"
        )
        return STATE_BGP_ASN

    elif data == "ripe":
        await query.message.reply_text(
            "📡 *RIPE NCC*\n\nEntre ton terme de recherche \\(org, IP, réseau\\)\\.\n\nTape /annuler pour revenir\\.",
            parse_mode="MarkdownV2"
        )
        return STATE_RIPE_QUERY

    elif data == "help":
        return await cmd_help(update, context)

    return STATE_MENU


# ── /annuler ──────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("↩️ Annulé.")
    return await show_main_menu(update, context)


# ── Flux Index Apache ─────────────────────────────────────────────────────────

async def index_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["index_url"] = update.message.text.strip()
    await update.message.reply_text(
        "📂 Extensions à télécharger ?\n\nDéfaut : `txt,gz`",
        parse_mode="Markdown"
    )
    return STATE_INDEX_EXT


async def index_get_ext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["index_ext"] = update.message.text.strip() or "txt,gz"
    await update.message.reply_text(
        "⚡ Combien de *workers* \\(threads\\) ? \\(1\\-20\\)\n\nDéfaut : `5`",
        parse_mode="MarkdownV2"
    )
    return STATE_INDEX_WORKERS


async def index_get_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["index_workers"] = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        context.user_data["index_workers"] = 5
    await update.message.reply_text(
        "🔢 Limite de fichiers ? \\(`0` = tous\\)\n\nDéfaut : `0`",
        parse_mode="MarkdownV2"
    )
    return STATE_INDEX_LIMIT


async def index_get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["index_limit"] = max(0, int(update.message.text.strip()))
    except ValueError:
        context.user_data["index_limit"] = 0

    url     = context.user_data["index_url"]
    exts    = [e.strip().lstrip(".").lower()
               for e in context.user_data["index_ext"].split(",")]
    workers = context.user_data["index_workers"]
    limit   = context.user_data["index_limit"]

    await update.message.reply_text(
        f"⏳ Scraping en cours…\n`{url}`",
        parse_mode="Markdown"
    )
    await typing_action(context, update.effective_chat.id)

    results, total = await context.application.loop.run_in_executor(
        None, lambda: scrape_index(url, exts, limit, workers)
    )

    context.user_data.clear()
    return await send_lists(update, context, results,
                            f"Index Apache · {total} fichiers")


# ── Flux URL directe ──────────────────────────────────────────────────────────

async def url_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_url"] = update.message.text.strip()
    await update.message.reply_text(
        "⚡ Combien de *workers* \\(threads\\) ?\n\nDéfaut : `3`",
        parse_mode="MarkdownV2"
    )
    return STATE_URL_WORKERS


async def url_get_workers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["url_workers"] = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        context.user_data["url_workers"] = 3

    url     = context.user_data["target_url"]
    workers = context.user_data["url_workers"]

    await update.message.reply_text(f"⏳ Scraping…\n`{url}`", parse_mode="Markdown")
    await typing_action(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_url(url, follow=False, depth=1, workers=workers)
    )

    context.user_data.clear()
    return await send_lists(update, context, results, f"URL · {url[:50]}")


# ── Flux BGP ──────────────────────────────────────────────────────────────────

async def bgp_get_asn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asn = update.message.text.strip()
    await update.message.reply_text(f"⏳ Requête BGP AS{asn.upper().replace('AS','')}…")
    await typing_action(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_bgp(asn)
    )
    return await send_lists(update, context, results, f"BGP AS{asn}")


# ── Flux RIPE ─────────────────────────────────────────────────────────────────

async def ripe_get_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    await update.message.reply_text(f"⏳ Requête RIPE : {query}…")
    await typing_action(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_ripe(query)
    )
    return await send_lists(update, context, results, f"RIPE · {query}")


# ── Fichier reçu ──────────────────────────────────────────────────────────────

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        return STATE_MENU

    await update.message.reply_text("📥 Fichier reçu, extraction en cours…")
    await typing_action(context, update.effective_chat.id)

    file = await context.bot.get_file(doc.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        await file.download_to_drive(tmp.name)
        content = open(tmp.name, "r", errors="replace").read()
        os.unlink(tmp.name)

    results = extract(content)
    return await send_lists(update, context, results, f"Fichier : {doc.file_name}")


# ── Lancement du bot ──────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "METS_TON_TOKEN_ICI":
        print("❌ Configure BOT_TOKEN dans bot.py ou via la variable d'env BOT_TOKEN")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("index", lambda u, c: (
                u.message.reply_text("🗂 URL de l'index Apache ?") or STATE_INDEX_URL
            )),
            CommandHandler("url",   lambda u, c: (
                u.message.reply_text("🔗 URL cible ?") or STATE_URL_URL
            )),
            CommandHandler("bgp",   lambda u, c: (
                u.message.reply_text("🌐 Numéro ASN ?") or STATE_BGP_ASN
            )),
            CommandHandler("ripe",  lambda u, c: (
                u.message.reply_text("📡 Terme de recherche RIPE ?") or STATE_RIPE_QUERY
            )),
            MessageHandler(filters.Document.ALL, handle_file),
        ],
        states={
            STATE_MENU:          [CallbackQueryHandler(callback_menu),
                                  MessageHandler(filters.Document.ALL, handle_file)],
            STATE_INDEX_URL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, index_get_url)],
            STATE_INDEX_EXT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, index_get_ext)],
            STATE_INDEX_WORKERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, index_get_workers)],
            STATE_INDEX_LIMIT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, index_get_limit)],
            STATE_URL_URL:       [MessageHandler(filters.TEXT & ~filters.COMMAND, url_get_url)],
            STATE_URL_WORKERS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, url_get_workers)],
            STATE_BGP_ASN:       [MessageHandler(filters.TEXT & ~filters.COMMAND, bgp_get_asn)],
            STATE_RIPE_QUERY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ripe_get_query)],
        },
        fallbacks=[CommandHandler("annuler", cmd_cancel),
                   CommandHandler("help", cmd_help)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))

    print("🚀 ZeuScraper Bot démarré. Ctrl+C pour arrêter.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
