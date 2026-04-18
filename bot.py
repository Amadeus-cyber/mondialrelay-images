#!/usr/bin/env python3
"""
ZeuScraper — Bot Telegram v3.1
Lit le token depuis .env (BOT_TOKEN=...) ou variable d'environnement.
"""

import os
import asyncio
import logging
import tempfile
import time
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
    ST_URL_TARGET, ST_URL_FOLLOW, ST_URL_DEPTH, ST_URL_WORKERS,
    ST_BGP_ASN,
    ST_RIPE_QUERY,
    ST_SETTINGS,
) = range(12)

# ── Paramètres utilisateur ────────────────────────────────────────────────────

DEFAULT_CFG = {
    "workers_index": 5,
    "workers_url":   3,
    "timeout":       30,
    "depth":         2,
    "exts":          "txt,gz",
    "limit":         0,
}

def get_cfg(context: ContextTypes.DEFAULT_TYPE) -> dict:
    cfg = context.user_data.setdefault("cfg", {})
    for k, v in DEFAULT_CFG.items():
        cfg.setdefault(k, v)
    return cfg

# ── Claviers ──────────────────────────────────────────────────────────────────

KB_MAIN = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🗂  Index Apache",  callback_data="m:index"),
        InlineKeyboardButton("🔗  URL directe",   callback_data="m:url"),
    ],
    [
        InlineKeyboardButton("🌐  BGP / ASN",     callback_data="m:bgp"),
        InlineKeyboardButton("📡  RIPE NCC",      callback_data="m:ripe"),
    ],
    [
        InlineKeyboardButton("⚙️  Paramètres",    callback_data="m:settings"),
        InlineKeyboardButton("ℹ️  Aide",          callback_data="m:help"),
    ],
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
        InlineKeyboardButton("🗂 txt + gz",  callback_data="ext:txt,gz"),
        InlineKeyboardButton("✅ TOUTES (*)", callback_data="ext:*"),
    ],
    [InlineKeyboardButton("❌ Annuler",       callback_data="m:cancel")],
])

KB_FOLLOW = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✅ Oui — suivre les liens", callback_data="follow:yes"),
        InlineKeyboardButton("❌ Non",                    callback_data="follow:no"),
    ],
    [InlineKeyboardButton("❌ Annuler", callback_data="m:cancel")],
])

def kb_settings(cfg: dict) -> InlineKeyboardMarkup:
    limit_lbl = str(cfg["limit"]) if cfg["limit"] else "∞"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ Workers Index  : {cfg['workers_index']}", callback_data="set:workers_index")],
        [InlineKeyboardButton(f"⚡ Workers URL    : {cfg['workers_url']}",   callback_data="set:workers_url")],
        [InlineKeyboardButton(f"⏱ Timeout        : {cfg['timeout']}s",      callback_data="set:timeout")],
        [InlineKeyboardButton(f"🔁 Profondeur     : {cfg['depth']}",         callback_data="set:depth")],
        [InlineKeyboardButton(f"📂 Extensions     : {cfg['exts']}",          callback_data="set:exts")],
        [InlineKeyboardButton(f"🔢 Limite fichiers : {limit_lbl}",           callback_data="set:limit")],
        [InlineKeyboardButton("🏠 Retour au menu",                            callback_data="m:home")],
    ])

BANNER_TEXT = (
    "⚡ <b>ZeuScraper v3.1</b>\n"
    "━━━━━━━━━━━━━━━━━━━\n"
    "Domain &amp; IP Range Scraper\n\n"
    "Choisis un mode ci-dessous, ou envoie directement "
    "un fichier <code>.txt</code> pour extraire domaines et IPs automatiquement."
)

HELP_TEXT = (
    "📖 <b>ZeuScraper — Aide</b>\n\n"
    "<b>Modes disponibles :</b>\n"
    "🗂 <b>Index Apache</b> — Scrape tous les fichiers d'un répertoire web\n"
    "🔗 <b>URL directe</b> — Scrape une page ou un fichier précis\n"
    "🌐 <b>BGP/ASN</b> — Plages IP d'un ASN via BGP.HE.NET\n"
    "📡 <b>RIPE NCC</b> — Plages IP via l'API RIPE\n"
    "⚙️ <b>Paramètres</b> — Workers, timeout, extensions, profondeur\n\n"
    "<b>Fichier direct :</b>\n"
    "Envoie n'importe quel fichier → extraction immédiate\n\n"
    "<b>Commandes :</b>\n"
    "<code>/start</code>  <code>/help</code>  <code>/annuler</code>"
)

# ── Helpers messages ──────────────────────────────────────────────────────────

async def delete_msg(bot, chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except BadRequest:
        pass


async def replace(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  text: str, keyboard=None, parse_mode="HTML") -> Message:
    chat_id  = update.effective_chat.id
    prev_ids = context.user_data.get("bot_msgs", [])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode=parse_mode,
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
    context.user_data.pop("set_key", None)
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

    if data in ("m:home", "m:cancel"):
        context.user_data.pop("step_data", None)
        context.user_data.pop("set_key", None)
        return await show_main(update, context)

    if data == "m:help":
        return await cmd_help(update, context)

    # ── Paramètres ──
    if data == "m:settings":
        cfg = get_cfg(context)
        await replace(update, context,
                      "⚙️ <b>Paramètres</b>\n\nClique sur un paramètre pour le modifier.",
                      kb_settings(cfg))
        return ST_SETTINGS

    if data.startswith("set:"):
        return await settings_select(update, context, data[4:])

    # ── Modes ──
    if data == "m:index":
        await replace(update, context,
                      "🗂 <b>Index Apache</b>\n\n"
                      "Envoie l'URL du répertoire à scraper.\n"
                      "<i>ex: https://example.com/lists/</i>",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "index"}
        return ST_IDX_URL

    if data == "m:url":
        await replace(update, context,
                      "🔗 <b>URL directe</b>\n\nEnvoie l'URL de la page ou du fichier.",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "url"}
        return ST_URL_TARGET

    if data == "m:bgp":
        await replace(update, context,
                      "🌐 <b>BGP / ASN</b>\n\nEnvoie le numéro ASN.\n<i>ex: 15169 ou AS15169</i>",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "bgp"}
        return ST_BGP_ASN

    if data == "m:ripe":
        await replace(update, context,
                      "📡 <b>RIPE NCC</b>\n\nEnvoie ton terme de recherche (org, IP, réseau).",
                      KB_CANCEL)
        context.user_data["step_data"] = {"mode": "ripe"}
        return ST_RIPE_QUERY

    # ── Extensions ──
    if data.startswith("ext:"):
        val = data[4:]
        cfg = get_cfg(context)
        context.user_data.setdefault("step_data", {})["ext"] = val
        label = "TOUTES les extensions" if val == "*" else f"<code>.{val}</code>"
        await replace(update, context,
                      f"✅ Extensions : {label}\n\n"
                      f"⚡ Combien de <b>workers</b> (threads parallèles) ?\n"
                      f"<i>Défaut : {cfg['workers_index']} — entre 1 et 20</i>",
                      KB_CANCEL)
        return ST_IDX_WORKERS

    # ── Follow links ──
    if data.startswith("follow:"):
        follow = data[7:] == "yes"
        cfg    = get_cfg(context)
        context.user_data.setdefault("step_data", {})["follow"] = follow
        if follow:
            await replace(update, context,
                          f"🔁 <b>Profondeur de suivi</b>\n\n"
                          f"Jusqu'à combien de niveaux de liens suivre ?\n"
                          f"<i>Défaut : {cfg['depth']}</i>",
                          KB_CANCEL)
            return ST_URL_DEPTH
        else:
            await replace(update, context,
                          f"⚡ <b>Workers</b> (threads parallèles)\n\n"
                          f"<i>Défaut : {cfg['workers_url']}</i>",
                          KB_CANCEL)
            return ST_URL_WORKERS

    return ST_MENU


# ── Flux Settings ─────────────────────────────────────────────────────────────

SET_LABELS = {
    "workers_index": "⚡ Workers Index (1–20)",
    "workers_url":   "⚡ Workers URL (1–20)",
    "timeout":       "⏱ Timeout en secondes (5–300)",
    "depth":         "🔁 Profondeur de suivi (1–10)",
    "exts":          "📂 Extensions (ex: txt,gz  ou  * pour toutes)",
    "limit":         "🔢 Limite de fichiers (0 = aucune)",
}

SET_INT_BOUNDS = {
    "workers_index": (1, 20),
    "workers_url":   (1, 20),
    "timeout":       (5, 300),
    "depth":         (1, 10),
    "limit":         (0, 9999),
}


async def settings_select(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> int:
    cfg = get_cfg(context)
    context.user_data["set_key"] = key
    label   = SET_LABELS.get(key, key)
    current = str(cfg.get(key, "?"))
    await replace(update, context,
                  f"⚙️ <b>{label}</b>\n\n"
                  f"Valeur actuelle : <code>{current}</code>\n\n"
                  f"Envoie la nouvelle valeur :",
                  KB_CANCEL)
    return ST_SETTINGS


async def settings_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data.get("set_key")
    val = update.message.text.strip()
    cfg = get_cfg(context)

    if key in SET_INT_BOUNDS:
        lo, hi = SET_INT_BOUNDS[key]
        try:
            cfg[key] = max(lo, min(hi, int(val)))
        except ValueError:
            pass
    elif key == "exts":
        cfg[key] = val

    if update.message:
        await delete_msg(context.bot, update.effective_chat.id, update.message.message_id)

    prev_ids = context.user_data.get("bot_msgs", [])
    for mid in prev_ids:
        await delete_msg(context.bot, update.effective_chat.id, mid)

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⚙️ <b>Paramètres</b>\n\nClique sur un paramètre pour le modifier.",
        reply_markup=kb_settings(cfg),
        parse_mode="HTML",
    )
    context.user_data["bot_msgs"] = [msg.message_id]
    return ST_SETTINGS


# ── Flux Index Apache ─────────────────────────────────────────────────────────

async def idx_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    context.user_data.setdefault("step_data", {})["url"] = url
    await replace(update, context,
                  "📂 <b>Quelles extensions télécharger ?</b>\n\n"
                  "Choisis ci-dessous ou tape manuellement (ex: <code>txt,gz,csv</code>).",
                  KB_EXT)
    return ST_IDX_EXT


async def idx_ext_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    context.user_data.setdefault("step_data", {})["ext"] = raw
    cfg   = get_cfg(context)
    label = "TOUTES" if raw in ("*", "all", "toutes") else f"<code>{raw}</code>"
    await replace(update, context,
                  f"✅ Extensions : {label}\n\n"
                  f"⚡ Combien de <b>workers</b> (threads) ?\n"
                  f"<i>Défaut : {cfg['workers_index']}</i>",
                  KB_CANCEL)
    return ST_IDX_WORKERS


async def idx_workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = get_cfg(context)
    try:
        w = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        w = cfg["workers_index"]
    context.user_data["step_data"]["workers"] = w
    limit_lbl = str(cfg["limit"]) if cfg["limit"] else "∞"
    await replace(update, context,
                  f"🔢 <b>Limite de fichiers ?</b>\n\n"
                  f"Entre un nombre (ex: <code>50</code>) ou <code>0</code> pour <b>tous</b>.\n"
                  f"<i>Défaut : {limit_lbl}</i>",
                  KB_CANCEL)
    return ST_IDX_LIMIT


async def idx_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = get_cfg(context)
    try:
        limit = max(0, int(update.message.text.strip()))
    except ValueError:
        limit = cfg["limit"]

    sd      = context.user_data["step_data"]
    url     = sd["url"]
    raw_ext = sd.get("ext", cfg["exts"])
    exts    = [] if raw_ext.strip() in ("*", "all", "toutes", "tout") else [
        e.strip().lstrip(".").lower() for e in raw_ext.split(",") if e.strip()
    ]
    workers = sd.get("workers", cfg["workers_index"])
    ext_lbl = "toutes" if not exts else ",".join(exts)

    prog_msg = await replace(
        update, context,
        f"⏳ <b>Scraping en cours…</b>\n\n"
        f"🔗 <code>{url}</code>\n"
        f"📂 Extensions : <code>{ext_lbl}</code>\n"
        f"⚡ Workers : <code>{workers}</code>\n"
        f"🔢 Limite : <code>{limit or '∞'}</code>\n\n"
        f"<i>Démarrage…</i>"
    )
    await typing(context, update.effective_chat.id)

    chat_id   = update.effective_chat.id
    loop      = asyncio.get_running_loop()
    last_edit = [0.0]

    async def _edit(done, total, fname):
        pct = int(done / total * 10) if total else 0
        bar = "█" * pct + "░" * (10 - pct)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=prog_msg.message_id,
                text=(
                    f"⏳ <b>Scraping en cours…</b>\n\n"
                    f"🔗 <code>{url}</code>\n"
                    f"[{bar}] <b>{done}/{total}</b>\n\n"
                    f"📄 <code>{fname}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    def progress_cb(done, total, file_url, data):
        now = time.monotonic()
        if now - last_edit[0] < 2.0 and done < total:
            return
        last_edit[0] = now
        fname = file_url.split("/")[-1][:40]
        asyncio.run_coroutine_threadsafe(_edit(done, total, fname), loop)

    results, total_files = await asyncio.to_thread(
        scrape_index, url, exts, limit, workers, progress_cb
    )
    context.user_data.pop("step_data", None)
    return await send_results(update, context, results, f"Index Apache · {total_files} fichiers")


# ── Flux URL directe ──────────────────────────────────────────────────────────

async def url_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    context.user_data["step_data"]["url"] = url
    await replace(update, context,
                  "🔗 <b>Suivre les liens internes ?</b>\n\n"
                  "Si activé, le bot crawle toutes les pages liées du même domaine.",
                  KB_FOLLOW)
    return ST_URL_FOLLOW


async def url_depth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = get_cfg(context)
    try:
        depth = max(1, min(10, int(update.message.text.strip())))
    except ValueError:
        depth = cfg["depth"]
    context.user_data["step_data"]["depth"] = depth
    await replace(update, context,
                  f"⚡ <b>Workers</b> (threads parallèles)\n\n<i>Défaut : {cfg['workers_url']}</i>",
                  KB_CANCEL)
    return ST_URL_WORKERS


async def url_workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = get_cfg(context)
    try:
        w = max(1, min(20, int(update.message.text.strip())))
    except ValueError:
        w = cfg["workers_url"]

    sd     = context.user_data["step_data"]
    url    = sd["url"]
    follow = sd.get("follow", False)
    depth  = sd.get("depth", cfg["depth"])

    await replace(update, context,
                  f"⏳ <b>Scraping en cours…</b>\n\n"
                  f"🔗 <code>{url}</code>\n"
                  f"🔁 Follow links : <code>{'Oui' if follow else 'Non'}</code>\n"
                  f"📐 Profondeur : <code>{depth}</code>\n"
                  f"⚡ Workers : <code>{w}</code>\n\n"
                  f"<i>Patiente…</i>")
    await typing(context, update.effective_chat.id)

    results = await asyncio.to_thread(scrape_url, url, follow, depth, w)
    context.user_data.pop("step_data", None)
    return await send_results(update, context, results, f"URL · {url[:40]}")


# ── Flux BGP ──────────────────────────────────────────────────────────────────

async def bgp_asn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    asn = update.message.text.strip().upper().replace("AS", "")
    await replace(update, context,
                  f"⏳ <b>Requête BGP…</b>\n\nAS<code>{asn}</code>\n\n<i>Patiente…</i>")
    await typing(context, update.effective_chat.id)

    results = await asyncio.to_thread(scrape_bgp, asn)
    return await send_results(update, context, results, f"BGP AS{asn}")


# ── Flux RIPE ─────────────────────────────────────────────────────────────────

async def ripe_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    await replace(update, context,
                  f"⏳ <b>Requête RIPE…</b>\n\n<code>{query}</code>\n\n<i>Patiente…</i>")
    await typing(context, update.effective_chat.id)

    results = await asyncio.to_thread(scrape_ripe, query)
    return await send_results(update, context, results, f"RIPE · {query}")


# ── Fichier reçu ──────────────────────────────────────────────────────────────

async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        return ST_MENU

    await replace(update, context, "📥 <b>Fichier reçu…</b> extraction en cours.")
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
                      "⚠️ <b>Aucun résultat trouvé.</b>\n\nVérifie l'URL ou essaie un autre mode.",
                      InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="m:home")]]))
        return ST_MENU

    summary = (
        f"✅ <b>Scraping terminé</b>\n"
        f"<i>{label}</i>\n\n"
        f"📄 Domaines  : <code>{len(domains)}</code>\n"
        f"🌐 IPs/CIDRs : <code>{len(all_ips)}</code>"
    )
    sum_msg = await context.bot.send_message(
        chat_id=chat_id, text=summary, parse_mode="HTML"
    )
    sent_ids = [sum_msg.message_id]

    if domains:
        buf      = BytesIO("\n".join(domains).encode())
        buf.name = "domains.txt"
        d_msg    = await context.bot.send_document(
            chat_id=chat_id, document=buf,
            filename="domains.txt", caption="📄 domains.txt"
        )
        sent_ids.append(d_msg.message_id)

    if all_ips:
        buf      = BytesIO("\n".join(all_ips).encode())
        buf.name = "ips.txt"
        i_msg    = await context.bot.send_document(
            chat_id=chat_id, document=buf,
            filename="ips.txt", caption="🌐 ips.txt"
        )
        sent_ids.append(i_msg.message_id)

    nav_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🏠 Que veux-tu faire ensuite ?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Nouveau scan", callback_data="m:home"),
        ]]),
        parse_mode="HTML",
    )
    sent_ids.append(nav_msg.message_id)
    context.user_data["bot_msgs"] = sent_ids
    return ST_MENU


# ── Message texte inattendu ───────────────────────────────────────────────────

async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await replace(update, context,
                  "⬆️ Utilise les boutons ci-dessus ou tape /start.",
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
                CallbackQueryHandler(on_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, idx_ext_text),
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
            ST_URL_FOLLOW: [
                CallbackQueryHandler(on_callback),
            ],
            ST_URL_DEPTH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, url_depth),
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
            ST_SETTINGS: [
                CallbackQueryHandler(on_callback),
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_value),
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
