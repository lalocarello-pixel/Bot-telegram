#!/usr/bin/env python3
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Mini server web per tenere vivo Render (Web Service)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot attivo")
    def log_message(self, *args):
        pass

def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), Handler).serve_forever(), daemon=True).start()

keep_alive()

import ast
import functools
import logging
import math
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
    parts = (update.effective_message.text or
