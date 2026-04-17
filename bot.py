#!/usr/bin/env python3
"""
ZeuScraper — Bot Telegram
Lit le token depuis .env (BOT_TOKEN=...) ou variable d'environnement.
"""

import os
import logging
import tempfile
from io import BytesIO
from pathlib import Path

# Charge .env si présent
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

try:
    from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, Message)
    from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                               CallbackQueryHandler, ConversationHandler,
                               ContextTypes, filters)
    from telegram.error import BadRequest
    from core import scrape_index, scrape_url, scrape_bgp, scrape_ripe, extract, build_lists
except ImportError:
    print("Deps manquants : pip install python-telegram-bot requests beautifulsoup4")
    raise

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── États ─────────────────────────────────────────────────────────────────────

(
    ST_MENU,
    ST_IDX_URL, ST_IDX_EXT, ST_IDX_WORKERS, ST_IDX_LIMIT,
    ST_URL_TARGET, ST_URL_WORKERS,
    ST_BGP_ASN,
    ST_RIPE_QUERY,
) = range(9)

# ── Clavier principal ─────────────────────────────────────────────────────────

KB_MAIN = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🗂  Index Apache",  callback_data="m:index"),
        InlineKeyboardButton("🔗  URL directe",   callback_data="m:url"),
    ],
    [
        InlineKeyboardButton("🌐  BGP / ASN",     callback_data="m:bgp"),
        InlineKeyboardButton("📡  RIPE NCC",      callback_data="m:ripe"),
    ],
    [InlineKeyboardButton("ℹ️  Aide",            callback_data="m:help")],
])

KB_CANCEL = InlineKeyboardMarkup([[
    InlineKeyboardButton("❌ Annuler", callback_data="m:cancel"),
]])

KB_EXT = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📄 txt",    callback_data="ext:txt"),
        InlineKeyboardButton("📦 gz",     callback_data="ext:gz"),
        InlineKeyboardButton("📋 csv",    callback_data="ext:csv"),
    ],
    [
        InlineKeyboardButton("🗂 txt + gz",        callback_data="ext:txt,gz"),
        InlineKeyboardButton("✅ TOUTES (*)",       callback_data="ext:*"),
    ],
    [InlineKeyboardButton("❌ Annuler",             callback_data="m:cancel")],
])

BANNER_TEXT = (
    "⚡ *ZeuScraper v3.0*\n"
    "━━━━━━━━━━━━━━━━━━━\n"
    "Domain & IP Range Scraper\n\n"
    "Choisis un mode ci\\-dessous, ou envoie directement "
    "un fichier `.txt` pour extraire domaines et IPs automatiquement\\."
)

HELP_TEXT = (
    "📖 *ZeuScraper — Aide*\n\n"
    "*Modes disponibles :*\n"
    "🗂 *Index Apache* — Scrape tous les fichiers d'un répertoire web\n"
    "🔗 *URL directe* — Scrape une page ou un fichier précis\n"
    "🌐 *BGP/ASN* — Plages IP d'un ASN via BGP\\.HE\\.NET\n"
    "📡 *RIPE NCC* — Plages IP via l'API RIPE\n\n"
    "*Fichier direct :*\n"
    "Envoie n'importe quel fichier `.txt` → extraction immédiate\n\n"
    "*Commandes :*\n"
    "`/start` `/help` `/annuler`"
)

# ── Helpers messages ──────────────────────────────────────────────────────────

async def delete_msg(bot, chat_id: int, msg_id: int):
    """Supprime silencieusement un message (ignore les erreurs si déjà supprimé)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except BadRequest:
        pass


async def replace(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  text: str, keyboard=None, parse_mode="MarkdownV2") -> Message:
    """
    Envoie un nouveau message ET supprime le précédent bot-message stocké.
    Stocke l'ID du nouveau message pour le prochain replace().
    """
    chat_id  = update.effective_chat.id
    prev_ids = context.user_data.get("bot_msgs", [])

    # Envoie d'abord le nouveau message
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode=parse_mode,
    )

    # Supprime les anciens messages bot
    for mid in prev_ids:
        await delete_msg(context.bot, chat_id, mid)

    # Essaie aussi de supprimer le message utilisateur déclencheur
    trigger = update.message or (update.callback_query and update.callback_query.message)
    if update.message:
        await delete_msg(context.bot, chat_id, update.message.message_id)

    context.user_data["bot_msgs"] = [msg.message_id]
    return msg


async def replace_doc(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      buf: BytesIO, filename: str, caption: str) -> Message:
    """Envoie un document en supprimant les anciens messages."""
    chat_id  = update.effective_chat.id
    prev_ids = context.user_data.get("bot_msgs", [])

    msg = await context.bot.send_document(
        chat_id=chat_id,
        document=buf,
        filename=filename,
        caption=caption,
    )
    for mid in prev_ids:
        await delete_msg(context.bot, chat_id, mid)

    if update.message:
        await delete_msg(context.bot, chat_id, update.message.message_id)

    context.user_data["bot_msgs"] = [msg.message_id]
    return msg


async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")


# ── Menus ─────────────────────────────────────────────────────────────────────

async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("step_data", None)
    await replace(update, context, BANNER_TEXT, KB_MAIN)
    return ST_MENU


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await show_main(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await replace(update, context, HELP_TEXT,
                  InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="m:home")]]))
    return ST_MENU


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    return await show_main(update, context)


# ── Callback inline ───────────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q    = update.callback_query
    await q.answer()
    data = q.data

    if data == "m:home" or data == "m:cancel":
        context.user_data.pop("step_data", None)
        return await show_main(update, context)

    if data == "m:help":
        return await cmd_help(update, context)

    # ── Choix de mode ──
    if data == "m:index":
        await replace(update, context,
                      "🗂 *Index Apache*\n\n"
                      "Envoie l'URL du répertoire à scraper\\.\n"
                      "_ex: https://example\\.com/lists/_",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "index"}
        return ST_IDX_URL

    if data == "m:url":
        await replace(update, context,
                      "🔗 *URL directe*\n\nEnvoie l'URL de la page ou du fichier\\.",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "url"}
        return ST_URL_TARGET

    if data == "m:bgp":
        await replace(update, context,
                      "🌐 *BGP / ASN*\n\nEnvoie le numéro ASN\\.\n_ex: `15169` ou `AS15169`_",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "bgp"}
        return ST_BGP_ASN

    if data == "m:ripe":
        await replace(update, context,
                      "📡 *RIPE NCC*\n\nEnvoie ton terme de recherche \\(org, IP, réseau\\)\\.",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "ripe"}
        return ST_RIPE_QUERY

    # ── Choix d'extension via boutons ──
    if data.startswith("ext:"):
        val = data[4:]
        sd  = context.user_data.setdefault("step_data", {})
        sd["ext"] = val
        label = "TOUTES les extensions" if val == "*" else f"`.{val}`"
        await replace(update, context,
                      f"✅ Extensions : *{label}*\n\n"
                      "⚡ Combien de *workers* \\(threads parallèles\\) ?\n\n"
                      "_Entre un nombre entre 1 et 20, ou tape `5` pour le défaut\\._",
                      KB_CANCEL)
        return ST_IDX_WORKERS

    return ST_MENU


# ── Flux Index Apache ─────────────────────────────────────────────────────────

async def idx_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    context.user_data.setdefault("step_data", {})["url"] = url
    await replace(update, context,
                  "📂 *Quelles extensions télécharger ?*\n\n"
                  "Choisis ci\\-dessous ou tape manuellement \\(ex: `txt,gz,csv`\\)\\.",
                  KB_EXT)
    return ST_IDX_EXT


async def idx_ext_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reçoit l'extension tapée manuellement."""
    raw = update.message.text.strip()
    context.user_data.setdefault("step_data", {})["ext"] = raw
    label = "TOUTES" if raw in ("*", "all", "toutes") else f"`{raw}`"
    await replace(update, context,
                  f"✅ Extensions : *{label}*\n\n"
                  "⚡ Combien de *workers* \\(threads\\) ?\n_Défaut : `5`_",
                  KB_CANCEL)
    return ST_IDX_WORKERS


async def idx_workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        w = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        w = 5
    context.user_data["step_data"]["workers"] = w
    await replace(update, context,
                  "🔢 *Limite de fichiers ?*\n\n"
                  "Entre un nombre \\(ex: `50`\\) ou `0` pour *tous*\\.",
                  KB_CANCEL)
    return ST_IDX_LIMIT


async def idx_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        limit = max(0, int(update.message.text.strip()))
    except ValueError:
        limit = 0

    sd      = context.user_data["step_data"]
    url     = sd["url"]
    raw_ext = sd.get("ext", "txt,gz")
    exts    = [] if raw_ext.strip() in ("*", "all", "toutes", "tout") else [
        e.strip().lstrip(".").lower() for e in raw_ext.split(",") if e.strip()
    ]
    workers = sd.get("workers", 5)
    ext_lbl = "toutes" if not exts else ",".join(exts)

    await replace(update, context,
                  f"⏳ *Scraping en cours…*\n\n"
                  f"🔗 `{url}`\n"
                  f"📂 Extensions : `{ext_lbl}`\n"
                  f"⚡ Workers : `{workers}`\n"
                  f"🔢 Limite : `{limit or 'aucune'}`\n\n"
                  "_Patiente, ça peut prendre du temps\\.\\.\\._")

    await typing(context, update.effective_chat.id)

    results, total = await context.application.loop.run_in_executor(
        None, lambda: scrape_index(url, exts, limit, workers)
    )
    context.user_data.pop("step_data", None)
    return await send_results(update, context, results, f"Index Apache · {total} fichiers")


# ── Flux URL directe ──────────────────────────────────────────────────────────

async def url_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    context.user_data["step_data"]["url"] = url
    await replace(update, context,
                  "⚡ *Combien de workers \\(threads\\) ?*\n\n_Défaut : `3`_",
                  KB_CANCEL)
    return ST_URL_WORKERS


async def url_workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        w = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        w = 3

    url = context.user_data["step_data"]["url"]

    await replace(update, context,
                  f"⏳ *Scraping en cours…*\n\n🔗 `{url}`\n⚡ Workers : `{w}`\n\n"
                  "_Patiente\\.\\.\\._")
    await typing(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_url(url, follow=False, depth=1, workers=w)
    )
    context.user_data.pop("step_data", None)
    return await send_results(update, context, results, f"URL · {url[:40]}")


# ── Flux BGP ──────────────────────────────────────────────────────────────────

async def bgp_asn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    asn = update.message.text.strip().upper().replace("AS", "")
    await replace(update, context,
                  f"⏳ *Requête BGP…*\n\nAS`{asn}`\n\n_Patiente\\.\\.\\._")
    await typing(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_bgp(asn)
    )
    return await send_results(update, context, results, f"BGP AS{asn}")


# ── Flux RIPE ─────────────────────────────────────────────────────────────────

async def ripe_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    await replace(update, context,
                  f"⏳ *Requête RIPE…*\n\n`{query}`\n\n_Patiente\\.\\.\\._")
    await typing(context, update.effective_chat.id)

    results = await context.application.loop.run_in_executor(
        None, lambda: scrape_ripe(query)
    )
    return await send_results(update, context, results, f"RIPE · {query}")


# ── Fichier .txt reçu ─────────────────────────────────────────────────────────

async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        return ST_MENU

    await replace(update, context, "📥 *Fichier reçu…* extraction en cours\\.")
    await typing(context, update.effective_chat.id)

    tg_file = await context.bot.get_file(doc.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as tmp:
        await tg_file.download_to_drive(tmp.name)
        try:
            content = open(tmp.name, encoding="utf-8", errors="replace").read()
        finally:
            os.unlink(tmp.name)

    results = extract(content)
    return await send_results(update, context, results, f"Fichier · {doc.file_name}")


# ── Envoi des résultats ───────────────────────────────────────────────────────

async def send_results(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       results: dict, label: str) -> int:
    domains, all_ips = build_lists(results)
    chat_id = update.effective_chat.id

    prev_ids = context.user_data.get("bot_msgs", [])
    for mid in prev_ids:
        await delete_msg(context.bot, chat_id, mid)
    context.user_data["bot_msgs"] = []

    if not domains and not all_ips:
        await replace(update, context,
                      "⚠️ *Aucun résultat trouvé\\.*\n\nVérifie l'URL ou essaie un autre mode\\.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="m:home")]]))
        return ST_MENU

    # ── Résumé ──
    summary = (
        f"✅ *Scraping terminé*\n"
        f"_{label}_\n\n"
        f"📄 Domaines  : `{len(domains)}`\n"
        f"🌐 IPs/CIDRs : `{len(all_ips)}`"
    )
    sum_msg = await context.bot.send_message(
        chat_id=chat_id, text=summary, parse_mode="MarkdownV2"
    )
    sent_ids = [sum_msg.message_id]

    # ── Fichier domaines ──
    if domains:
        buf       = BytesIO("\n".join(domains).encode())
        buf.name  = "domains.txt"
        d_msg     = await context.bot.send_document(
            chat_id=chat_id, document=buf,
            filename="domains.txt", caption="📄 domains.txt"
        )
        sent_ids.append(d_msg.message_id)

    # ── Fichier IPs ──
    if all_ips:
        buf       = BytesIO("\n".join(all_ips).encode())
        buf.name  = "ips.txt"
        i_msg     = await context.bot.send_document(
            chat_id=chat_id, document=buf,
            filename="ips.txt", caption="🌐 ips.txt"
        )
        sent_ids.append(i_msg.message_id)

    # ── Bouton retour au menu ──
    nav_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🏠 Que veux\\-tu faire ensuite ?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Nouveau scan", callback_data="m:home"),
        ]]),
        parse_mode="MarkdownV2",
    )
    sent_ids.append(nav_msg.message_id)
    context.user_data["bot_msgs"] = sent_ids
    return ST_MENU


# ── Message texte inattendu ───────────────────────────────────────────────────

async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await replace(update, context,
                  "⬆️ Utilise les boutons ci\\-dessus ou tape /start\\.",
                  InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="m:home")]]))
    return ST_MENU


# ── Lancement ─────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN manquant. Crée un fichier .env avec BOT_TOKEN=<token>")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",   cmd_start),
            CommandHandler("help",    cmd_help),
            MessageHandler(filters.Document.ALL, on_file),
            CallbackQueryHandler(on_callback),
        ],
        states={
            ST_MENU: [
                CallbackQueryHandler(on_callback),
                MessageHandler(filters.Document.ALL, on_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu),
            ],
            ST_IDX_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, idx_url),
                CallbackQueryHandler(on_callback),
            ],
            ST_IDX_EXT: [
                CallbackQueryHandler(on_callback),                          # boutons ext:*
                MessageHandler(filters.TEXT & ~filters.COMMAND, idx_ext_text),  # texte manuel
            ],
            ST_IDX_WORKERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, idx_workers),
                CallbackQueryHandler(on_callback),
            ],
            ST_IDX_LIMIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, idx_limit),
                CallbackQueryHandler(on_callback),
            ],
            ST_URL_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, url_target),
                CallbackQueryHandler(on_callback),
            ],
            ST_URL_WORKERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, url_workers),
                CallbackQueryHandler(on_callback),
            ],
            ST_BGP_ASN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bgp_asn),
                CallbackQueryHandler(on_callback),
            ],
            ST_RIPE_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ripe_query),
                CallbackQueryHandler(on_callback),
            ],
        },
        fallbacks=[
            CommandHandler("annuler", cmd_cancel),
            CommandHandler("start",   cmd_start),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )

    app.add_handler(conv)
    print("🚀 ZeuScraper Bot démarré. Ctrl+C pour arrêter.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
