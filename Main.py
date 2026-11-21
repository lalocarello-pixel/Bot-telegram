#!/usr/bin/env python3
"""
Bot Telegram personale (meteo di Piacenza + 40 comandi per la vita quotidiana).

Requisiti:   pip install "python-telegram-bot[job-queue]>=21" httpx
Variabili:   BOT_TOKEN        token ottenuto da @BotFather
             ALLOWED_USER_ID  il tuo ID Telegram (scrivi /id al bot per scoprirlo)
Avvio:       python bot_personale.py

Il bot risponde SOLO a te. Dati salvati in SQLite (bot_data.db, accanto al file).
Meteo: Open-Meteo (gratis, senza chiave). Cambi: Frankfurter/BCE. Wikipedia, MyMemory.
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
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
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
CREATE TABLE IF NOT EXISTS expenses(id INTEGER
