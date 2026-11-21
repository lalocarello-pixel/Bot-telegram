#!/usr/bin/env python3
"""
Main.py — Bot Telegram personale: meteo di Piacenza + 90 comandi (tutto in un unico file).

Installazione:  pip install "python-telegram-bot[job-queue]>=21.6,<23" httpx tzdata
Avvio:          python Main.py

Variabili d'ambiente:
  BOT_TOKEN         token di @BotFather                         (obbligatoria)
  ALLOWED_USER_ID   il tuo ID Telegram (scrivi /id al bot)      (obbligatoria)
  GITHUB_TOKEN      token GitHub fine-grained, Contents: read/write   (facoltativa, backup)
  BACKUP_REPO       repo PRIVATO per i backup, es. utente/bot-backup  (facoltativa, backup)
  PORT              porta del mini-server /health (Render la imposta da solo)
  RENDER_EXTERNAL_URL  impostata da Render: attiva l'auto-ping anti-sleep

Su Render: Start command  python Main.py  e Health check path  /health
Poi crea un monitor gratuito (cron-job.org / UptimeRobot) su https://TUO-SERVIZIO.onrender.com/health
"""
import ast
import base64
import calendar
import functools
import hashlib
import json
import logging
import math
import operator
import os
import platform
import random
import re
import secrets
import sqlite3
import string
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import httpx
from telegram import BotCommand, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

# ============================== CONFIGURAZIONE ==============================
TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("ALLOWED_USER_ID", "0") or 0)
DB_PATH = os.environ.get(
    "BOT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db"))
HOME = {"name": "Piacenza", "lat": 45.0526, "lon": 9.6930, "tz": "Europe/Rome"}
TZ = ZoneInfo("Europe/Rome")
START = time.time()

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ================================= DATABASE =================================
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.row_factory = sqlite3.Row
DB.executescript("""
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS spesa(id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, amount REAL NOT NULL, descr TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, ts INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contacts(name TEXT PRIMARY KEY COLLATE NOCASE, info TEXT NOT NULL);
""")


def db(sql, *args):
    cur = DB.execute(sql, args)
    DB.commit()
    return cur


def rows(sql, *args):
    return DB.execute(sql, args).fetchall()


# ================================ UTILITA' ==================================
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def fdate(d):
    return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]} {d.year}"


def short(d):
    return f"{GIORNI[d.weekday()][:3]} {d:%d/%m}"


def now():
    return datetime.now(TZ)


def num(s):
    try:
        return float(str(s).replace(",", ".").replace("€", "").strip())
    except ValueError:
        raise ValueError(f"Numero non valido: {s}")


def fmt(x, dec=2):
    """1234.5 -> 1.234,50"""
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def eur(x):
    return f"{fmt(x)} €"


def plur(n, one, many):
    return f"{n} {one if n == 1 else many}"


def arg_text(update):
    parts = (update.effective_message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def say(update, text):
    """Invia il testo spezzandolo (limite Telegram 4096) preferibilmente a fine riga."""
    msg, chunk = update.effective_message, ""
    for line in text.split("\n"):
        while len(line) > 4000:
            if chunk:
                await msg.reply_text(chunk.rstrip("\n"))
                chunk = ""
            await msg.reply_text(line[:4000])
            line = line[4000:]
        if len(chunk) + len(line) + 1 > 4000:
            await msg.reply_text(chunk.rstrip("\n"))
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await msg.reply_text(chunk.rstrip("\n"))


async def http_get(url, /, **params):
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers={"User-Agent": "PersonalTelegramBot/1.0"}) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


# ============================ REGISTRO DEI COMANDI ==========================
HANDLERS = {}


def safe(fn):
    @functools.wraps(fn)
    async def wrapper(update, ctx):
        try:
            await fn(update, ctx)
        except LookupError as e:
            await say(update, f"🤷 {e.args[0] if e.args else 'Non trovato'}")
        except ValueError as e:
            await say(update, f"❓ {e}")
        except httpx.HTTPError:
            await say(update, "⚠️ Servizio esterno non raggiungibile, riprova tra poco.")
        except Exception:
            log.exception("Errore nel comando")
            await say(update, "⚠️ Errore inatteso (guarda i log del bot).")
    return wrapper


def cmd(name):
    def deco(fn):
        HANDLERS[name] = safe(fn)
        return fn
    return deco


# (nome, uso, descrizione) raggruppati: servono per /help e per il menu di Telegram
GROUPS = [
    ("🌦️ METEO E TEMPO", [
        ("meteo", "[città]", "meteo ora (basta scrivere: meteo)"),
        ("previsioni", "[città]", "previsioni 5 giorni"),
        ("alba", "[città]", "alba, tramonto, UV"),
        ("luna", "", "fase lunare"),
        ("ora", "[città]", "che ore sono altrove"),
        ("data", "", "data, settimana, giorni a fine anno"),
        ("festivi", "", "prossimi giorni festivi"),
    ]),
    ("📝 ORGANIZZAZIONE", [
        ("nota", "testo", "salva una nota"),
        ("note", "", "elenco note"),
        ("delnota", "id", "elimina una nota"),
        ("todo", "testo", "aggiungi attività"),
        ("todos", "", "attività da fare"),
        ("fatto", "id", "segna attività completata"),
        ("spesa", "latte, pane", "aggiungi alla lista della spesa"),
        ("listaspesa", "", "mostra lista della spesa"),
        ("tolto", "id|nome", "togli dalla lista della spesa"),
        ("svuotaspesa", "", "svuota la lista della spesa"),
        ("contatto", "nome info", "salva/mostra contatti"),
        ("cerca", "testo", "cerca in note, todo, contatti, spesa"),
    ]),
    ("⏰ PROMEMORIA", [
        ("ricorda", "quando testo", "es: /ricorda 30m pasta · 18:30 pillola · domani 9:00 dentista · 25/12 10:00 regalo"),
        ("timer", "10m [nome]", "timer rapido"),
        ("pomodoro", "[min] [pausa]", "sessione di studio/lavoro (25+5)"),
        ("promemoria", "", "promemoria attivi"),
        ("annulla", "id", "annulla un promemoria"),
    ]),
    ("💶 SOLDI", [
        ("soldi", "12,50 pranzo", "registra una spesa"),
        ("speso", "[MM[/AAAA]]", "riepilogo spese del mese"),
        ("delsoldi", "id", "elimina una spesa registrata"),
        ("iva", "importo [aliquota]", "calcolo IVA (default 22%)"),
        ("sconto", "prezzo %", "prezzo scontato"),
        ("dividi", "conto persone [mancia%]", "divisione del conto"),
        ("valuta", "100 usd [eur]", "conversione valute (BCE)"),
    ]),
    ("🧮 CALCOLI", [
        ("calc", "espressione", "calcolatrice (anche: 15% di 200)"),
        ("converti", "10 km mi", "unità di misura e temperature"),
        ("eta", "GG/MM/AAAA", "età e prossimo compleanno"),
        ("giorni", "GG/MM[/AAAA]", "giorni mancanti a una data"),
    ]),
    ("🎲 UTILITY", [
        ("password", "[lunghezza]", "genera password sicura"),
        ("dado", "[facce | NdM]", "tira i dadi (es: /dado 2d6)"),
        ("scegli", "a, b, c", "sceglie a caso (anche testa, croce)"),
        ("traduci", "en testo", "traduce dall'italiano (o en>it testo)"),
        ("wiki", "argomento", "riassunto da Wikipedia"),
    ]),
    ("⚙️ BOT", [
        ("help", "", "questa lista"),
        ("id", "", "mostra il tuo ID Telegram"),
        ("ping", "", "stato del bot"),
    ]),
]


def help_text():
    out = ["🤖 COMANDI DISPONIBILI", "(per il meteo basta scrivere: meteo)", ""]
    for title, items in GROUPS:
        out.append(title)
        for name, usage, desc in items:
            out.append(f"/{name} {usage}".rstrip() + f" — {desc}")
        out.append("")
    return "\n".join(out)


# ================================== METEO ===================================
WMO = {
    0: ("Sereno", "☀️"), 1: ("Quasi sereno", "🌤️"), 2: ("Parzialmente nuvoloso", "⛅"),
    3: ("Coperto", "☁️"), 45: ("Nebbia", "🌫️"), 48: ("Nebbia con brina", "🌫️"),
    51: ("Pioviggine debole", "🌦️"), 53: ("Pioviggine", "🌦️"), 55: ("Pioviggine intensa", "🌧️"),
    56: ("Pioviggine gelata", "🌧️"), 57: ("Pioviggine gelata", "🌧️"),
    61: ("Pioggia debole", "🌧️"), 63: ("Pioggia", "🌧️"), 65: ("Pioggia forte", "🌧️"),
    66: ("Pioggia gelata", "🌧️"), 67: ("Pioggia gelata forte", "🌧️"),
    71: ("Neve debole", "🌨️"), 73: ("Neve", "🌨️"), 75: ("Neve forte", "❄️"),
    77: ("Granuli di neve", "❄️"), 80: ("Rovesci deboli", "🌦️"), 81: ("Rovesci", "🌧️"),
    82: ("Rovesci violenti", "⛈️"), 85: ("Rovesci di neve", "🌨️"),
    86: ("Forti rovesci di neve", "❄️"), 95: ("Temporale", "⛈️"),
    96: ("Temporale con grandine", "⛈️"), 99: ("Forte temporale con grandine", "⛈️"),
}


def wmo(code):
    return WMO.get(code, ("Variabile", "🌡️"))


def compass(deg):
    return ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][int((deg + 22.5) // 45) % 8]


async def resolve_place(query):
    query = (query or "").strip()
    if not query or query.lower() == "piacenza":
        return HOME
    data = await http_get("https://geocoding-api.open-meteo.com/v1/search",
                          name=query, count=1, language="it", format="json")
    res = data.get("results")
    if not res:
        raise LookupError(f"Non trovo il luogo «{query}»")
    r = res[0]
    cc = f" ({r['country_code']})" if r.get("country_code") else ""
    return {"name": r["name"] + cc, "lat": r["latitude"], "lon": r["longitude"],
            "tz": r.get("timezone") or "Europe/Rome"}


async def get_forecast(place, days):
    return await http_get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=place["lat"], longitude=place["lon"], timezone=place["tz"],
        forecast_days=days,
        current="temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
                "weather_code,wind_speed_10m,wind_direction_10m",
        daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
              "precipitation_probability_max,sunrise,sunset,uv_index_max")


def hm(iso):
    return datetime.fromisoformat(iso).strftime("%H:%M")


async def send_weather(update, query):
    place = await resolve_place(query)
    d = await get_forecast(place, 2)
    c, dl = d["current"], d["daily"]
    desc, em = wmo(c["weather_code"])
    lines = [
        f"📍 {place['name']} — adesso",
        f"{em} {desc}, {c['temperature_2m']:.0f}°C (percepita {c['apparent_temperature']:.0f}°C)",
        f"💧 Umidità {c['relative_humidity_2m']}% · 🌬️ Vento {c['wind_speed_10m']:.0f} km/h {compass(c['wind_direction_10m'])}",
    ]
    if c.get("precipitation"):
        lines.append(f"🌧️ Precipitazioni in corso: {c['precipitation']} mm")
    for i, label in enumerate(("Oggi", "Domani")):
        dd, de = wmo(dl["weather_code"][i])
        prob = dl["precipitation_probability_max"][i] or 0
        lines.append(f"\n{label}: {de} {dd}")
        lines.append(f"🌡️ {dl['temperature_2m_min'][i]:.0f}° / {dl['temperature_2m_max'][i]:.0f}° · "
                     f"☔ {prob}% ({dl['precipitation_sum'][i]} mm)")
        if i == 0:
            lines.append(f"🌅 {hm(dl['sunrise'][0])}  🌇 {hm(dl['sunset'][0])} · UV {dl['uv_index_max'][0]:.0f}")
    await say(update, "\n".join(lines))


@cmd("meteo")
async def c_meteo(update, ctx):
    await send_weather(update, " ".join(ctx.args))


METEO_RE = re.compile(r"^\s*meteo\b(.*)$", re.I | re.S)


@safe
async def t_meteo(update, ctx):
    m = METEO_RE.match(update.effective_message.text or "")
    await send_weather(update, m.group(1).strip() if m else "")


@cmd("previsioni")
async def c_previsioni(update, ctx):
    place = await resolve_place(" ".join(ctx.args))
    dl = (await get_forecast(place, 5))["daily"]
    lines = [f"📅 Previsioni {place['name']}"]
    for i, ds in enumerate(dl["time"]):
        desc, em = wmo(dl["weather_code"][i])
        prob = dl["precipitation_probability_max"][i] or 0
        lines.append(f"{short(date.fromisoformat(ds))} {em} {dl['temperature_2m_min'][i]:.0f}°/"
                     f"{dl['temperature_2m_max'][i]:.0f}° ☔{prob}% — {desc}")
    await say(update, "\n".join(lines))


@cmd("alba")
async def c_alba(update, ctx):
    place = await resolve_place(" ".join(ctx.args))
    dl = (await get_forecast(place, 1))["daily"]
    sr, ss = datetime.fromisoformat(dl["sunrise"][0]), datetime.fromisoformat(dl["sunset"][0])
    mins = int((ss - sr).total_seconds() // 60)
    await say(update, f"🌅 {place['name']}\nAlba: {sr:%H:%M}\nTramonto: {ss:%H:%M}\n"
                      f"Durata del giorno: {mins // 60} h {mins % 60} min\n"
                      f"☀️ Indice UV massimo: {dl['uv_index_max'][0]:.0f}")


def moon(dt):
    syn = 29.530588853
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    f = (((dt.astimezone(timezone.utc) - ref).total_seconds() / 86400) % syn) / syn
    if f < 0.03 or f >= 0.97:
        name, em = "Luna nuova", "🌑"
    elif f < 0.22:
        name, em = "Luna crescente", "🌒"
    elif f < 0.28:
        name, em = "Primo quarto", "🌓"
    elif f < 0.47:
        name, em = "Gibbosa crescente", "🌔"
    elif f < 0.53:
        name, em = "Luna piena", "🌕"
    elif f < 0.72:
        name, em = "Gibbosa calante", "🌖"
    elif f < 0.78:
        name, em = "Ultimo quarto", "🌗"
    else:
        name, em = "Luna calante", "🌘"
    illum = (1 - math.cos(2 * math.pi * f)) / 2
    to_full = ((0.5 - f) % 1) * syn
    to_new = ((1 - f) % 1) * syn
    return name, em, illum, to_full, to_new


@cmd("luna")
async def c_luna(update, ctx):
    name, em, illum, full, new = moon(now())
    await say(update, f"{em} {name}\nIlluminata al {illum * 100:.0f}%\n"
                      f"Prossima luna piena tra {full:.0f} giorni · luna nuova tra {new:.0f} giorni")


@cmd("ora")
async def c_ora(update, ctx):
    q = " ".join(ctx.args)
    if q:
        p = await resolve_place(q)
        t = datetime.now(ZoneInfo(p["tz"]))
        await say(update, f"🕒 {p['name']}: {t:%H:%M} — {fdate(t.date())}")
    else:
        lines = []
        for label, zone in (("Italia", "Europe/Rome"), ("Londra", "Europe/London"),
                            ("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo")):
            t = datetime.now(ZoneInfo(zone))
            lines.append(f"🕒 {label}: {t:%H:%M}")
        await say(update, "\n".join(lines))


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def holidays(y):
    e = easter(y)
    return sorted([
        (date(y, 1, 1), "Capodanno"), (date(y, 1, 6), "Epifania"), (e, "Pasqua"),
        (e + timedelta(days=1), "Lunedì dell'Angelo"), (date(y, 4, 25), "Festa della Liberazione"),
        (date(y, 5, 1), "Festa dei Lavoratori"), (date(y, 6, 2), "Festa della Repubblica"),
        (date(y, 7, 4), "Sant'Antonino (patrono di Piacenza)"), (date(y, 8, 15), "Ferragosto"),
        (date(y, 11, 1), "Ognissanti"), (date(y, 12, 8), "Immacolata"),
        (date(y, 12, 25), "Natale"), (date(y, 12, 26), "Santo Stefano"),
    ])


def next_holidays(today, n):
    out = []
    for y in (today.year, today.year + 1):
        out += [h for h in holidays(y) if h[0] >= today]
    return out[:n]


@cmd("festivi")
async def c_festivi(update, ctx):
    today = now().date()
    lines = ["🎉 Prossimi giorni festivi"]
    for d, name in next_holidays(today, 6):
        delta = (d - today).days
        when = "oggi" if delta == 0 else ("domani" if delta == 1 else f"tra {delta} giorni")
        lines.append(f"{short(d)} — {name} ({when})")
    await say(update, "\n".join(lines))


@cmd("data")
async def c_data(update, ctx):
    today = now().date()
    iso = today.isocalendar()
    left = (date(today.year, 12, 31) - today).days
    nh = next_holidays(today, 1)[0]
    await say(update, f"📅 {fdate(today).capitalize()}\n"
                      f"Settimana {iso[1]} · giorno {today.timetuple().tm_yday} dell'anno\n"
                      f"Mancano {left} giorni a fine anno\n"
                      f"Prossima festa: {nh[1]} ({short(nh[0])})")


# ============================ NOTE / TODO / SPESA ===========================
def need_text(update, usage):
    t = arg_text(update)
    if not t:
        raise ValueError(f"Uso: {usage}")
    return t


def need_id(ctx, usage):
    if not ctx.args or not ctx.args[0].isdigit():
        raise ValueError(f"Uso: {usage}")
    return int(ctx.args[0])


@cmd("nota")
async def c_nota(update, ctx):
    t = need_text(update, "/nota testo da salvare")
    cur = db("INSERT INTO notes(text, ts) VALUES (?,?)", t, int(time.time()))
    await say(update, f"📝 Nota #{cur.lastrowid} salvata")


@cmd("note")
async def c_note(update, ctx):
    r = rows("SELECT * FROM notes ORDER BY id")
    if not r:
        return await say(update, "Nessuna nota salvata.")
    await say(update, "📝 Le tue note\n\n" + "\n\n".join(
        f"#{n['id']} ({datetime.fromtimestamp(n['ts'], TZ):%d/%m/%y})\n{n['text']}" for n in r))


@cmd("delnota")
async def c_delnota(update, ctx):
    i = need_id(ctx, "/delnota id")
    n = db("DELETE FROM notes WHERE id=?", i).rowcount
    await say(update, "🗑️ Nota eliminata" if n else "Nota non trovata")


@cmd("todo")
async def c_todo(update, ctx):
    t = need_text(update, "/todo cosa devi fare")
    cur = db("INSERT INTO todos(text, ts) VALUES (?,?)", t, int(time.time()))
    await say(update, f"✅ Attività #{cur.lastrowid} aggiunta")


@cmd("todos")
async def c_todos(update, ctx):
    r = rows("SELECT * FROM todos WHERE done=0 ORDER BY id")
    if not r:
        return await say(update, "Niente da fare 🎉")
    await say(update, "📋 Da fare\n" + "\n".join(f"☐ #{t['id']} {t['text']}" for t in r))


@cmd("fatto")
async def c_fatto(update, ctx):
    i = need_id(ctx, "/fatto id")
    n = db("UPDATE todos SET done=1 WHERE id=? AND done=0", i).rowcount
    await say(update, "✔️ Completata, bravo!" if n else "Attività non trovata")


@cmd("spesa")
async def c_spesa(update, ctx):
    t = need_text(update, "/spesa latte, pane, uova")
    items = [x.strip() for x in re.split(r"[,;\n]", t) if x.strip()]
    for it in items:
        db("INSERT INTO spesa(item) VALUES (?)", it)
    await say(update, "🛒 Aggiunti: " + ", ".join(items))


@cmd("listaspesa")
as
