#!/usr/bin/env python3
"""
Bot Telegram personale - versione FIX per Render
Comandi: /comandi per vedere tutti i comandi
"""
import ast
import functools
import logging
import math
import operator
import os
import random
import re
import secrets
import sqlite3
import string
import time
import urllib.parse
import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from telegram import BotCommand, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("ALLOWED_USER_ID", "0") or 0)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")
HOME = {"name": "Piacenza", "lat": 45.0526, "lon": 9.6930, "tz": "Europe/Rome"}
TZ = ZoneInfo("Europe/Rome")
START = time.time()

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

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
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def eur(x):
    return f"{fmt(x)} €"

def plur(n, one, many):
    return f"{n} {one if n == 1 else many}"

def arg_text(update):
    parts = (update.effective_message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""

async def say(update, text):
    for i in range(0, max(len(text), 1), 4000):
        await update.effective_message.reply_text(text[i:i + 4000])

async def http_get(url, **params):
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers={"User-Agent": "PersonalTelegramBot/1.0"}) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()

HANDLERS = {}

def safe(fn):
    @functools.wraps(fn)
    async def wrapper(update, ctx):
        # Blocco altri utenti - FIX: fatto qui, non con process_update
        if OWNER_ID and update.effective_user and update.effective_user.id != OWNER_ID:
            await update.effective_message.reply_text("⛔ Questo bot è privato.")
            return
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
        ("comandi", "", "tutti i comandi"),
        ("help", "", "tutti i comandi"),
        ("id", "", "mostra il tuo ID Telegram"),
        ("ping", "", "stato del bot"),
    ]),
]

def help_text():
    out = ["🤖 TUTTI I COMANDI", "(per il meteo basta scrivere: meteo)", ""]
    for title, items in GROUPS:
        out.append(title)
        for name, usage, desc in items:
            out.append(f"/{name} {usage}".rstrip() + f" — {desc}")
        out.append("")
    return "\n".join(out)

# --- METEO ---
WMO = {
    0: ("Sereno", "☀️"), 1: ("Quasi sereno", "🌤️"), 2: ("Parzialmente nuvoloso", "⛅"),
    3: ("Coperto", "☁️"), 45: ("Nebbia", "🌫️"), 48: ("Nebbia con brina", "🌫️"),
    51: ("Pioviggine debole", "🌦️"), 53: ("Pioviggine", "🌦️"), 55: ("Pioviggine intensa", "🌧️"),
    61: ("Pioggia debole", "🌧️"), 63: ("Pioggia", "🌧️"), 65: ("Pioggia forte", "🌧️"),
    71: ("Neve debole", "🌨️"), 73: ("Neve", "🌨️"), 75: ("Neve forte", "❄️"),
    80: ("Rovesci deboli", "🌦️"), 81: ("Rovesci", "🌧️"), 82: ("Rovesci violenti", "⛈️"),
    95: ("Temporale", "⛈️"), 96: ("Temporale con grandine", "⛈️"), 99: ("Forte temporale con grandine", "⛈️"),
}
def wmo(code):
    return WMO.get(code, ("Variabile", "🌡️"))
def compass(deg):
    return ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][int((deg + 22.5) // 45) % 8]

async def resolve_place(query):
    query = (query or "").strip()
    if not query or query.lower() == "piacenza":
        return HOME
    data = await http_get("https://geocoding-api.open-meteo.com/v1/search", name=query, count=1, language="it", format="json")
    res = data.get("results")
    if not res:
        raise LookupError(f"Non trovo il luogo «{query}»")
    r = res[0]
    cc = f" ({r['country_code']})" if r.get("country_code") else ""
    return {"name": r["name"] + cc, "lat": r["latitude"], "lon": r["longitude"], "tz": r.get("timezone") or "Europe/Rome"}

async def get_forecast(place, days):
    return await http_get("https://api.open-meteo.com/v1/forecast", latitude=place["lat"], longitude=place["lon"], timezone=place["tz"], forecast_days=days, current="temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_direction_10m", daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,sunrise,sunset,uv_index_max")

def hm(iso):
    return datetime.fromisoformat(iso).strftime("%H:%M")

async def send_weather(update, query):
    place = await resolve_place(query)
    d = await get_forecast(place, 2)
    c, dl = d["current"], d["daily"]
    desc, em = wmo(c["weather_code"])
    lines = [f"📍 {place['name']} — adesso", f"{em} {desc}, {c['temperature_2m']:.0f}°C (percepita {c['apparent_temperature']:.0f}°C)", f"💧 Umidità {c['relative_humidity_2m']}% · 🌬️ Vento {c['wind_speed_10m']:.0f} km/h {compass(c['wind_direction_10m'])}"]
    if c.get("precipitation"):
        lines.append(f"🌧️ Precipitazioni: {c['precipitation']} mm")
    for i, label in enumerate(("Oggi", "Domani")):
        dd, de = wmo(dl["weather_code"][i])
        prob = dl["precipitation_probability_max"][i] or 0
        lines.append(f"\n{label}: {de} {dd}")
        lines.append(f"🌡️ {dl['temperature_2m_min'][i]:.0f}° / {dl['temperature_2m_max'][i]:.0f}° · ☔ {prob}% ({dl['precipitation_sum'][i]} mm)")
        if i == 0:
            lines.append(f"🌅 {hm(dl['sunrise'][0])}  🌇 {hm(dl['sunset'][0])} · UV {dl['uv_index_max'][0]:.0f}")
    await say(update, "\n".join(lines))

@cmd("meteo")
async def c_meteo(update, ctx):
    await send_weather(update, " ".
