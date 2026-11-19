#!/usr/bin/env python3
"""
Bot Telegram personale (meteo di Piacenza + comandi quotidiani).
Variabili: BOT_TOKEN, ALLOWED_USER_ID
Avvio: python Main.py
"""
import ast
import calendar
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
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import httpx
from telegram import BotCommand, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

# ===== TRUCCO PER RENDER GRATIS - NON TOGLIERE =====
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot online")
        def log_message(self, *args):
            pass
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()
# ===== FINE TRUCCO =====

# ============================== CONFIGURAZIONE ==============================
TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("ALLOWED_USER_ID", "0") or 0)
DB_PATH = os.environ.get(
    "BOT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db"))
HOME = {"name": "Piacenza", "lat": 45.0526, "lon": 9.6930, "tz": "Europe/Rome"}
TZ = ZoneInfo("Europe/Rome")
START = time.time()

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
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

# ============================ REGISTRO DEI COMANDI ==========================
HANDLERS = {}

def safe(fn):
    @functools.wraps(fn)
    async def wrapper(update, ctx):
        if OWNER_ID and update.effective_user and update.effective_user.id != OWNER_ID:
            return
        try:
            await fn(update, ctx)
        except LookupError as e:
            await say(update, f"🤷 {e.args[0] if e.args else 'Non trovato'}")
        except ValueError as e:
            await say(update, f"❓ {e}")
        except httpx.HTTPError as e:
            log.warning("HTTPError: %s", e)
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
        ("nota", "testo", "salva una nota"), ("note", "", "elenco note"),
        ("delnota", "id", "elimina una nota"), ("todo", "testo", "aggiungi attività"),
        ("todos", "", "attività da fare"), ("fatto", "id", "segna attività completata"),
        ("spesa", "latte, pane", "aggiungi alla lista della spesa"),
        ("listaspesa", "", "mostra lista della spesa"), ("tolto", "id|nome", "togli dalla lista"),
        ("svuotaspesa", "", "svuota la lista"), ("contatto", "nome info", "salva/mostra contatti"),
        ("cerca", "testo", "cerca in note, todo, contatti, spesa"),
    ]),
    ("⏰ PROMEMORIA", [
        ("ricorda", "quando testo", "es: /ricorda 30m pasta"), ("timer", "10m [nome]", "timer rapido"),
        ("pomodoro", "[min] [pausa]", "sessione 25+5"), ("promemoria", "", "promemoria attivi"),
        ("annulla", "id", "annulla un promemoria"),
    ]),
    ("💶 SOLDI", [
        ("soldi", "12,50 pranzo", "registra una spesa"), ("speso", "[MM[/AAAA]]", "riepilogo mese"),
        ("delsoldi", "id", "elimina spesa"), ("iva", "importo [aliquota]", "calcolo IVA"),
        ("sconto", "prezzo %", "prezzo scontato"), ("dividi", "conto persone [mancia%]", "divisione conto"),
        ("valuta", "100 usd [eur]", "conversione valute"),
    ]),
    ("🧮 CALCOLI", [
        ("calc", "espressione", "calcolatrice"), ("converti", "10 km mi", "unità di misura"),
        ("eta", "GG/MM/AAAA", "età e compleanno"), ("giorni", "GG/MM[/AAAA]", "giorni mancanti"),
    ]),
    ("🎲 UTILITY", [
        ("password", "[lunghezza]", "genera password"), ("dado", "[facce | NdM]", "tira i dadi"),
        ("scegli", "a, b, c", "sceglie a caso"), ("traduci", "en testo", "traduce"),
        ("wiki", "argomento", "riassunto Wikipedia"),
    ]),
    ("⚙️ BOT", [("help", "", "questa lista"), ("id", "", "mostra il tuo ID"), ("ping", "", "stato del bot")]),
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
        current="temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,sunrise,sunset,uv_index_max")

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
        lines.append(f"🌡️ {dl['temperature_2m_min'][i]:.0f}° / {dl['temperature_2m_max'][i]:.0f}° · ☔ {prob}% ({dl['precipitation_sum'][i]} mm)")
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
    if not m:
        return
    await send_weather(update, m.group(1).strip() if m else "")

@cmd("previsioni")
async def c_previsioni(update, ctx):
    place = await resolve_place(" ".join(ctx.args))
    dl = (await get_forecast(place, 5))["daily"]
    lines = [f"📅 Previsioni {place['name']}"]
    for i, ds in enumerate(dl["time"]):
        desc, em = wmo(dl["weather_code"][i])
        prob = dl["precipitation_probability_max"][i] or 0
        lines.append(f"{short(date.fromisoformat(ds))} {em} {dl['temperature_2m_min'][i]:.0f}°/{dl['temperature_2m_max'][i]:.0f}° ☔{prob}% — {desc}")
    await say(update, "\n".join(lines))

@cmd("alba")
async def c_alba(update, ctx):
    place = await resolve_place(" ".join(ctx.args))
    dl = (await get_forecast(place, 1))["daily"]
    sr, ss = datetime.fromisoformat(dl["sunrise"][0]), datetime.fromisoformat(dl["sunset"][0])
    mins = int((ss - sr).total_seconds() // 60)
    await say(update, f"🌅 {place['name']}\nAlba: {sr:%H:%M}\nTramonto: {ss:%H:%M}\nDurata del giorno: {mins // 60} h {mins % 60} min\n☀️ Indice UV massimo: {dl['uv_index_max'][0]:.0f}")

def moon(dt):
    syn = 29.530588853
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    f = (((dt.astimezone(timezone.utc) - ref).total_seconds() / 86400) % syn) / syn
    if f < 0.03 or f >= 0.97: name, em = "Luna nuova", "🌑"
    elif f < 0.22: name, em = "Luna crescente", "🌒"
    elif f < 0.28: name, em = "Primo quarto", "🌓"
    elif f < 0.47: name, em = "Gibbosa crescente", "🌔"
    elif f < 0.53: name, em = "Luna piena", "🌕"
    elif f < 0.72: name, em = "Gibbosa calante", "🌖"
    elif f < 0.78: name, em = "Ultimo quarto", "🌗"
    else: name, em = "Luna calante", "🌘"
    illum = (1 - math.cos(2 * math.pi * f)) / 2
    to_full = ((0.5 - f) % 1) * syn
    to_new = ((1 - f) % 1) * syn
    return name, em, illum, to_full, to_new

@cmd("luna")
async def c_luna(update, ctx):
    name, em, illum, full, new = moon(now())
    await say(update, f"{em} {name}\nIlluminata al {illum * 100:.0f}%\nProssima luna piena tra {full:.0f} giorni · luna nuova tra {new:.0f} giorni")

@cmd("ora")
async def c_ora(update, ctx):
    q = " ".join(ctx.args)
    if q:
        p = await resolve_place(q)
        t = datetime.now(ZoneInfo(p["tz"]))
        await say(update, f"🕒 {p['name']}: {t:%H:%M} — {fdate(t.date())}")
    else:
        lines = []
        for label, zone in (("Italia", "Europe/Rome"), ("Londra", "Europe/London"), ("New York", "America/New_York"), ("Tokyo", "Asia/Tokyo")):
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
    await say(update, f"📅 {fdate(today).capitalize()}\nSettimana {iso[1]} · giorno {today.timetuple().tm_yday} dell'anno\nMancano {left} giorni a fine anno\nProssima festa: {nh[1]} ({short(nh[0])})")

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
    if not r: return await say(update, "Nessuna nota salvata.")
    await say(update, "📝 Le tue note\n\n" + "\n\n".join(f"#{n['id']} ({datetime.fromtimestamp(n['ts'], TZ):%d/%m/%y})\n{n['text']}" for n in r))

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
    if not r: return await say(update, "Niente da fare 🎉")
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
    for it in items: db("INSERT INTO spesa(item) VALUES (?)", it)
    await say(update, "🛒 Aggiunti: " + ", ".join(items))

@cmd("listaspesa")
async def c_listaspesa(update, ctx):
    r = rows("SELECT * FROM spesa ORDER BY id")
    if not r: return await say(update, "La lista della spesa è vuota 🎉")
    await say(update, "🛒 Lista della spesa\n" + "\n".join(f"{x['id']}. {x['item']}" for x in r))

@cmd("tolto")
async def c_tolto(update, ctx):
    if not ctx.args: raise ValueError("Uso: /tolto 3 oppure /tolto latte")
    if all(a.isdigit() for a in ctx.args):
        n = sum(db("DELETE FROM spesa WHERE id=?", int(a)).rowcount for a in ctx.args)
    else:
        n = db("DELETE FROM spesa WHERE item LIKE ?", f"%{' '.join(ctx.args)}%").rowcount
    await say(update, f"✔️ Tolti {plur(n, 'articolo', 'articoli')}" if n else "Non trovato")

@cmd("svuotaspesa")
async def c_svuotaspesa(update, ctx):
    n = db("DELETE FROM spesa").rowcount
    await say(update, f"🧹 Lista svuotata ({plur(n, 'articolo', 'articoli')})")

@cmd("contatto")
async def c_contatto(update, ctx):
    if not ctx.args:
        r = rows("SELECT * FROM contacts ORDER BY name")
        if not r: return await say(update, "Nessun contatto salvato.")
        return await say(update, "👥 Contatti\n" + "\n".join(f"{c['name']}: {c['info']}" for c in r))
    parts = arg_text(update).split(None, 1)
    if len(parts) == 1:
        r = rows("SELECT * FROM contacts WHERE name=?", parts[0])
        if not r: raise LookupError(f"Contatto «{parts[0]}» non trovato")
        return await say(update, f"👤 {r[0]['name']}: {r[0]['info']}")
    db("INSERT INTO contacts(name, info) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET info=excluded.info", parts[0], parts[1])
    await say(update, f"👤 Contatto {parts[0]} salvato")

@cmd("cerca")
async def c_cerca(update, ctx):
    q = arg_text(update)
    if not q: raise ValueError("Uso: /cerca testo")
    out = []
    for n in rows("SELECT * FROM notes WHERE text LIKE ? ORDER BY id DESC LIMIT 5", f"%{q}%"):
        out.append(f"📝 Nota #{n['id']}: {n['text'][:80]}")
    for t in rows("SELECT * FROM todos WHERE text LIKE ? ORDER BY id DESC LIMIT 5", f"%{q}%"):
        out.append(f"✅ Todo #{t['id']}: {t['text'][:80]}")
    for c in rows("SELECT * FROM contacts WHERE name LIKE ? OR info LIKE ? LIMIT 5", f"%{q}%", f"%{q}%"):
        out.append(f"👤 {c['name']}: {c['info'][:80]}")
    for s in rows("SELECT * FROM spesa WHERE item LIKE ? LIMIT 5", f"%{q}%"):
        out.append(f"🛒 Spesa #{s['id']}: {s['item']}")
    await say(update, "🔍 Risultati\n" + "\n".join(out) if out else "Niente trovato.")

def parse_delay(s):
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smhd])", s)
    if not m: raise ValueError("Uso: 30s, 10m, 2h, 1d  es: /ricorda 30m pasta")
    n, u = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.send_message(chat_id=job.chat_id, text=f"⏰ PROMEMORIA: {job.data}")
        db("DELETE FROM reminders WHERE chat_id=? AND text=?", job.chat_id, job.data)
    except Exception:
        log.exception("Reminder failed")

@cmd("ricorda")
async def c_ricorda(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /ricorda 30m testo")
    secs = parse_delay(ctx.args[0])
    text = " ".join(ctx.args[1:])
    ts = int(time.time()) + secs
    cur = db("INSERT INTO reminders(chat_id, ts, text) VALUES (?,?,?)", update.effective_chat.id, ts, text)
    ctx.job_queue.run_once(send_reminder, secs, chat_id=update.effective_chat.id, data=text, name=f"rem_{cur.lastrowid}")
    await say(update, f"⏰ Ok, ti avviso tra {ctx.args[0]}: {text} (id #{cur.lastrowid})")

@cmd("timer")
async def c_timer(update, ctx):
    if not ctx.args: raise ValueError("Uso: /timer 10m [nome]")
    secs = parse_delay(ctx.args[0])
    name = " ".join(ctx.args[1:]) or "Timer"
    ctx.job_queue.run_once(send_reminder, secs, chat_id=update.effective_chat.id, data=f"{name} scaduto!", name=f"timer_{int(time.time())}")
    await say(update, f"⏱️ Timer {name} partito, ti avviso tra {ctx.args[0]}")

@cmd("pomodoro")
async def c_pomodoro(update, ctx):
    mins = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 25
    pausa = int(ctx.args[1]) if len(ctx.args) > 1 and ctx.args[1].isdigit() else 5
    ctx.job_queue.run_once(send_reminder, mins * 60, chat_id=update.effective_chat.id, data=f"Pomodoro finito! Pausa di {pausa} min.", name=f"pom_{int(time.time())}")
    await say(update, f"🍅 Pomodoro partito: {mins} min di lavoro, poi {pausa} min di pausa.")

@cmd("promemoria")
async def c_promemoria(update, ctx):
    r = rows("SELECT * FROM reminders WHERE chat_id=? ORDER BY ts", update.effective_chat.id)
    if not r: return await say(update, "Nessun promemoria attivo.")
    lines = []
    for x in r:
        left = max(0, x["ts"] - int(time.time()))
        lines.append(f"#{x['id']} tra {left//60}m {left%60}s — {x['text']}")
    await say(update, "⏰ Promemoria attivi\n" + "\n".join(lines))

@cmd("annulla")
async def c_annulla(update, ctx):
    i = need_id(ctx, "/annulla id")
    n = db("DELETE FROM reminders WHERE id=?", i).rowcount
    for job in ctx.job_queue.jobs():
        if job.name == f"rem_{i}":
            job.schedule_removal()
    await say(update, "🗑️ Promemoria annullato" if n else "Promemoria non trovato")

@cmd("soldi")
async def c_soldi(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /soldi 12,50 pranzo")
    amount = num(ctx.args[0])
    descr = " ".join(ctx.args[1:])
    cur = db("INSERT INTO expenses(ts, amount, descr) VALUES (?,?,?)", int(time.time()), amount, descr)
    await say(update, f"💶 Registrato #{cur.lastrowid}: {eur(amount)} — {descr}")

@cmd("speso")
async def c_speso(update, ctx):
    today = now()
    if ctx.args:
        parts = ctx.args[0].split("/")
        mm = int(parts[0]); yy = int(parts[1]) if len(parts) > 1 else today.year
    else:
        mm, yy = today.month, today.year
    start = datetime(yy, mm, 1, tzinfo=TZ).timestamp()
    if mm == 12: end = datetime(yy + 1, 1, 1, tzinfo=TZ).timestamp()
    else: end = datetime(yy, mm + 1, 1, tzinfo=TZ).timestamp()
    r = rows("SELECT * FROM expenses WHERE ts>=? AND ts<? ORDER BY ts", int(start), int(end))
    tot = sum(x["amount"] for x in r)
    lines = [f"💶 Speso {mm:02d}/{yy}: {eur(tot)} ({len(r)} spese)"]
    for x in r:
        lines.append(f"#{x['id']} {datetime.fromtimestamp(x['ts'], TZ):%d/%m} {eur(x['amount'])} — {x['descr']}")
    await say(update, "\n".join(lines) if r else f"Nessuna spesa in {mm:02d}/{yy}")

@cmd("delsoldi")
async def c_delsoldi(update, ctx):
    i = need_id(ctx, "/delsoldi id")
    n = db("DELETE FROM expenses WHERE id=?", i).rowcount
    await say(update, "🗑️ Spesa eliminata" if n else "Spesa non trovata")

@cmd("iva")
async def c_iva(update, ctx):
    if not ctx.args: raise ValueError("Uso: /iva 100 [22]")
    base = num(ctx.args[0]); aliq = num(ctx.args[1]) if len(ctx.args) > 1 else 22
    iva = base * aliq / 100
    await say(update, f"🧾 Base: {eur(base)}\nIVA {fmt(aliq, 0)}%: {eur(iva)}\nTotale: {eur(base + iva)}")

@cmd("sconto")
async def c_sconto(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /sconto 100 20")
    prezzo = num(ctx.args[0]); perc = num(ctx.args[1].replace("%", ""))
    ris = prezzo * perc / 100
    await say(update, f"🏷️ Prezzo: {eur(prezzo)}\nSconto {fmt(perc, 0)}%: -{eur(ris)}\nPaghi: {eur(prezzo - ris)}")

@cmd("dividi")
async def c_dividi(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /dividi 60 3 [10%]")
    totale = num(ctx.args[0]); persone = int(num(ctx.args[1]))
    mancia = num(ctx.args[2].replace("%", "")) if len(ctx.args) > 2 else 0
    tot2 = totale * (1 + mancia / 100)
    await say(update, f"🧾 Totale con mancia {fmt(mancia, 0)}%: {eur(tot2)}\nA testa ({persone} persone): {eur(tot2 / persone)}")

@cmd("valuta")
async def c_valuta(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /valuta 100 usd [eur]")
    amount = num(ctx.args[0]); frm = ctx.args[1].upper(); to = ctx.args[2].upper() if len(ctx.args) > 2 else "EUR"
    data = await http_get(f"https://open.er-api.com/v6/latest/{frm}")
    rate = data.get("rates", {}).get(to)
    if not rate: raise LookupError(f"Valuta {frm}->{to} non trovata")
    await say(update, f"💱 {fmt(amount)} {frm} = {fmt(amount * rate)} {to}")

ALLOWED_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}

def eval_expr(node):
    if isinstance(node, ast.Expression): return eval_expr(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPS:
        return ALLOWED_OPS[type(node.op)](eval_expr(node.left), eval_expr(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPS:
        return ALLOWED_OPS[type(node.op)](eval_expr(node.operand))
    raise ValueError("Espressione non consentita")

@cmd("calc")
async def c_calc(update, ctx):
    expr = arg_text(update)
    if not expr: raise ValueError("Uso: /calc 2+3*4")
    res = eval_expr(ast.parse(expr.replace(",", "."), mode="eval"))
    await say(update, f"🧮 {expr} = {fmt(res) if isinstance(res, float) else res}")

UNITS = {"km": 1000, "m": 1, "cm": 0.01, "mm": 0.001, "mi": 1609.34, "kg": 1000, "g": 1, "lb": 453.592, "l": 1, "ml": 0.001, "c": 1, "f": 1}
@cmd("converti")
async def c_converti(update, ctx):
    if len(ctx.args) < 3: raise ValueError("Uso: /converti 10 km mi")
    v = num(ctx.args[0]); a = ctx.args[1].lower(); b = ctx.args[2].lower()
    if a in ("c", "f") or b in ("c", "f"):
        if a == "c" and b == "f": res = v * 9 / 5 + 32
        elif a == "f" and b == "c": res = (v - 32) * 5 / 9
        else: raise ValueError("Conversione temperatura: solo C <-> F")
        return await say(update, f"🌡️ {fmt(v)}°{a.upper()} = {fmt(res)}°{b.upper()}")
    if a not in UNITS or b not in UNITS: raise ValueError("Unità non supportate")
    res = v * UNITS[a] / UNITS[b]
    await say(update, f"🔄 {fmt(v)} {a} = {fmt(res)} {b}")

@cmd("eta")
async def c_eta(update, ctx):
    if not ctx.args: raise ValueError("Uso: /eta 15/05/1990")
    try: birth = datetime.strptime(ctx.args[0], "%d/%m/%Y").date()
    except ValueError: raise ValueError("Usa GG/MM/AAAA es: /eta 15/05/1990")
    today = now().date()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    nxt = date(today.year, birth.month, birth.day)
    if nxt < today: nxt = date(today.year + 1, birth.month, birth.day)
    await say(update, f"🎂 Hai {age} anni\nProssimo compleanno: {fdate(nxt)} (tra {(nxt - today).days} giorni)")

@cmd("giorni")
async def c_giorni(update, ctx):
    if not ctx.args: raise ValueError("Uso: /giorni 25/12 oppure /giorni 25/12/2026")
    parts = ctx.args[0].split("/")
    today = now().date()
    if len(parts) == 2: target = date(today.year, int(parts[1]), int(parts[0]))
    else: target = date(int(parts[2]), int(parts[1]), int(parts[0]))
    if target < today: target = date(target.year + 1, target.month, target.day)
    await say(update, f"📅 Mancano {(target - today).days} giorni a {fdate(target)}")
  @cmd("password")
async def c_password(update, ctx):
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 16
    n = max(6, min(64, n))
    chars = string.ascii_letters + string.digits + "!@#$%*"
    pw = "".join(secrets.choice(chars) for _ in range(n))
    await say(update, f"🔐 Password ({n} caratteri):\n`{pw}`")

@cmd("dado")
async def c_dado(update, ctx):
    arg = " ".join(ctx.args) or "6"
    if "d" in arg.lower():
        m = re.fullmatch(r"(\d+)d(\d+)", arg.lower().strip())
        if not m: raise ValueError("Uso: /dado 2d6 oppure /dado 20")
        n, f = int(m.group(1)), int(m.group(2))
        rolls = [random.randint(1, f) for _ in range(min(n, 20))]
        return await say(update, f"🎲 {rolls} = totale {sum(rolls)}")
    f = int(arg) if arg.isdigit() else 6
    await say(update, f"🎲 Hai tirato: {random.randint(1, f)} (d{f})")

@cmd("scegli")
async def c_scegli(update, ctx):
    t = arg_text(update)
    if not t: raise ValueError("Uso: /scegli pizza, pasta, sushi")
    opts = [x.strip() for x in re.split(r"[,;\n]", t) if x.strip()]
    await say(update, f"🎯 Scelgo: {random.choice(opts)}")

@cmd("traduci")
async def c_traduci(update, ctx):
    if len(ctx.args) < 2: raise ValueError("Uso: /traduci en ciao come stai")
    lang = ctx.args[0]; text = " ".join(ctx.args[1:])
    data = await http_get("https://api.mymemory.translated.net/get", q=text, langpair=f"it|{lang}")
    tr = data.get("responseData", {}).get("translatedText", "")
    await say(update, f"🌐 {text}\n➡️ {tr}")

@cmd("wiki")
async def c_wiki(update, ctx):
    q = arg_text(update)
    if not q: raise ValueError("Uso: /wiki Leonardo da Vinci")
    data = await http_get(f"https://it.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(q)}")
    if not data.get("extract"): raise LookupError(f"Niente su «{q}» su Wikipedia")
    await say(update, f"📖 {data.get('title')}\n\n{data['extract'][:1500]}")

@cmd("help")
async def c_help(update, ctx):
    await say(update, help_text())

@cmd("id")
async def c_id(update, ctx):
    await say(update, f"🆔 Il tuo ID è: {update.effective_user.id}")

@cmd("ping")
async def c_ping(update, ctx):
    up = int(time.time() - START)
    await say(update, f"🏓 Pong! Bot online da {up//60}m {up%60}s ✅")

def main():
    if not TOKEN:
        raise RuntimeError("Manca BOT_TOKEN nelle variabili d'ambiente")
    app = Application.builder().token(TOKEN).build()
    for name, fn in HANDLERS.items():
        app.add_handler(CommandHandler(name, fn))
        app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, t_meteo))
    cmds = []
    for _, items in GROUPS: 
        for name, usage, desc in items:
            cmds.append(BotCommand(name, desc))
    async def set_cmds(app_):
        try: await app_.bot.set_my_commands(cmds)
        except Exception: pass
    app.post_init = set_cmds
    log.info("Bot avviato, OWNER_ID=%s", OWNER_ID)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
