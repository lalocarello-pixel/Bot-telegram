#!/usr/bin/env python3
import os,threading,ast,functools,logging,math,random,re,secrets,sqlite3,string,time,urllib.parse,asyncio
from http.server import BaseHTTPRequestHandler,HTTPServer
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import httpx
from telegram import BotCommand,Update
from telegram.ext import Application,CommandHandler,ContextTypes,MessageHandler,filters
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b"Bot attivo")
 def log_message(self,*a): pass
def keep_alive():
 p=int(os.environ.get("PORT",10000))
 threading.Thread(target=lambda:HTTPServer(("0.0.0.0",p),H).serve_forever(),daemon=True).start()
keep_alive()
TOKEN=os.environ.get("BOT_TOKEN","")
OWNER_ID=int(os.environ.get("ALLOWED_USER_ID","0") or 0)
DB_PATH=os.path.join(os.path.dirname(os.path.abspath(__file__)),"bot_data.db")
HOME={"name":"Piacenza","lat":45.0526,"lon":9.6930,"tz":"Europe/Rome"}
TZ=ZoneInfo("Europe/Rome");START=time.time()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",level=logging.INFO)
log=logging.getLogger("bot")
DB=sqlite3.connect(DB_PATH,check_same_thread=False);DB.row_factory=sqlite3.Row
DB.executescript("CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT,ts INTEGER);CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT,text TEXT,done INTEGER DEFAULT 0,ts INTEGER);CREATE TABLE IF NOT EXISTS spesa(id INTEGER PRIMARY KEY AUTOINCREMENT,item TEXT);CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,amount REAL,descr TEXT);CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,ts INTEGER,text TEXT);CREATE TABLE IF NOT EXISTS contacts(name TEXT PRIMARY KEY COLLATE NOCASE,info TEXT);")
def db(s,*a):
 c=DB.execute(s,a);DB.commit();return c
def rows(s,*a): return DB.execute(s,a).fetchall()
GIORNI=["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]
MESI=["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"]
def fdate(d): return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month-1]} {d.year}"
def short(d): return f"{GIORNI[d.weekday()][:3]} {d:%d/%m}"
def now(): return datetime.now(TZ)
def num(s):
 try: return float(str(s).replace(",",".").replace("€","").strip())
 except: raise ValueError(f"Numero non valido: {s}")
def fmt(x,dec=2): return f"{x:,.{dec}f}".replace(",","X").replace(".",",").replace("X",".")
def eur(x): return f"{fmt(x)} €"
def plur(n,o,m): return f"{n} {o if n==1 else m}"
def arg_text(u):
 p=(u.effective_message.text or "").split(None,1);return p[1].strip() if len(p)>1 else ""
async def say(u,t):
 for i in range(0,max(len(t),1),4000): await u.effective_message.reply_text(t[i:i+4000])
async def http_get(url,**p):
 async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={"User-Agent":"Bot/1.0"}) as c:
  r=await c.get(url,params=p);r.raise_for_status();return r.json()
HANDLERS={}
def safe(fn):
 @functools.wraps(fn)
 async def w(u,ctx):
  if OWNER_ID and u.effective_user and u.effective_user.id!=OWNER_ID: await u.effective_message.reply_text("⛔ Questo bot è privato.");return
  try: await fn(u,ctx)
  except LookupError as e: await say(u,f"🤷 {e.args[0] if e.args else 'Non trovato'}")
  except ValueError as e: await say(u,f"❓ {e}")
  except httpx.HTTPError: await say(u,"⚠️ Servizio esterno non raggiungibile, riprova tra poco.")
  except Exception: log.exception("Errore");await say(u,"⚠️ Errore inatteso.")
 return w
def cmd(n):
 def d(fn): HANDLERS[n]=safe(fn);return fn
 return d
GROUPS=[("🌦️ METEO E TEMPO",[("meteo","[città]","meteo ora (basta scrivere: meteo)"),("previsioni","[città]","previsioni 5 giorni"),("alba","[città]","alba, tramonto, UV"),("luna","","fase lunare"),("ora","[città]","che ore sono altrove"),("data","","data, settimana, giorni a fine anno"),("festivi","","prossimi giorni festivi")]),("📝 ORGANIZZAZIONE",[("nota","testo","salva una nota"),("note","","elenco note"),("delnota","id","elimina una nota"),("todo","testo","aggiungi attività"),("todos","","attività da fare"),("fatto","id","segna attività completata"),("spesa","latte, pane","aggiungi alla lista della spesa"),("listaspesa","","mostra lista della spesa"),("tolto","id|nome","togli dalla lista della spesa"),("svuotaspesa","","svuota la lista della spesa"),("contatto","nome info","salva/mostra contatti"),("cerca","testo","cerca in note, todo, contatti, spesa")]),("⏰ PROMEMORIA",[("ricorda","quando testo","es: /ricorda 30m pasta · 18:30 pillola · domani 9:00 dentista"),("timer","10m [nome]","timer rapido"),("pomodoro","[min] [pausa]","sessione 25+5"),("promemoria","","promemoria attivi"),("annulla","id","annulla un promemoria")]),("💶 SOLDI",[("soldi","12,50 pranzo","registra una spesa"),("speso","[MM[/AAAA]]","riepilogo spese del mese"),("delsoldi","id","elimina una spesa registrata"),("iva","importo [aliquota]","calcolo IVA (default 22%)"),("sconto","prezzo %","prezzo scontato"),("dividi","conto persone [mancia%]","divisione del conto"),("valuta","100 usd [eur]","conversione valute")]),("🧮 CALCOLI",[("calc","espressione","calcolatrice (anche: 15% di 200)"),("converti","10 km mi","unità di misura e temperature"),("eta","GG/MM/AAAA","età e prossimo compleanno"),("giorni","GG/MM[/AAAA]","giorni mancanti a una data")]),("🎲 UTILITY",[("password","[lunghezza]","genera password sicura"),("dado","[facce | NdM]","tira i dadi (es: /dado 2d6)"),("scegli","a, b, c","sceglie a caso"),("traduci","en testo","traduce dall'italiano"),("wiki","argomento","riassunto da Wikipedia")]),("⚙️ BOT",[("comandi","","tutti i comandi"),("help","","tutti i comandi"),("id","","mostra il tuo ID Telegram"),("ping","","stato del bot")])]
def help_text():
 o=["🤖 TUTTI I COMANDI","(per il meteo basta scrivere: meteo)",""]
 for t,items in GROUPS:
  o.append(t)
  for n,u,d in items: o.append(f"/{n} {u}".rstrip()+f" — {d}")
  o.append("")
 return "\n".join(o)
WMO={0:("Sereno","☀️"),1:("Quasi sereno","🌤️"),2:("Parzialmente nuvoloso","⛅"),3:("Coperto","☁️"),45:("Nebbia","🌫️"),48:("Nebbia con brina","🌫️"),51:("Pioviggine debole","🌦️"),53:("Pioviggine","🌦️"),55:("Pioviggine intensa","🌧️"),61:("Pioggia debole","🌧️"),63:("Pioggia","🌧️"),65:("Pioggia forte","🌧️"),71:("Ne
