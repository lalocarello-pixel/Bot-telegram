import os
import requests
import threading
import time
import random
import string
from datetime import date
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)

def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        print(e)

def get_json(link):
    r = requests.get(link, timeout=15)
    return r.json()

def meteo():
    try:
        j = get_json('https://api.open-meteo.com/v1/forecast?latitude=45.05&longitude=9.7&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m&timezone=Europe%2FRome')
        cur = j['current']
        cod = cur['weather_code']
        desc = 'Nuvoloso'
        if cod == 0: desc = 'Sereno'
        if cod in [45,48]: desc = 'Nebbia'
        if cod in [61,63,65]: desc = 'Pioggia'
        if cod == 71: desc = 'Neve'
        if cod == 95: desc = 'Temporale'
        return f"Meteo Piacenza:\n{desc}\nTemp: {cur['temperature_2m']}C\nPercepita: {cur['apparent_temperature']}C\nUmidita: {cur['relative_humidity_2m']}%\nPioggia: {cur['precipitation']} mm\nVento: {cur['wind_speed_10m']} km/h"
    except:
        return 'Meteo non disponibile'

def anime_top():
    try:
        j = get_json('https://api.jikan.moe/v4/top/anime?limit=5&filter=airing')
        out = 'Top 5 Anime del momento:\n'
        for i, a in enumerate(j['data'], 1):
            out += f"\n{i}. {a['title']}"
        return out
    except:
        return 'Anime non disponibili'

def canzoni_top():
    try:
        j = get_json('https://itunes.apple.com/it/rss/topsongs/limit=5/json')
        out = 'Top 5 Canzoni in Italia:\n'
        for i, e in enumerate(j['feed']['entry'], 1):
            out += f"\n{i}. {e['im:name']['label']} - {e['im:artist']['label']}"
        return out
    except:
        return 'Canzoni non disponibili'

def quattro():
    try:
        j = get_json('https://api.mangadex.org/manga?title=Four%20Knights%20of%20the%20Apocalypse&limit=1')
        mid = j['data'][0]['id']
        c = get_json(f'https://api.mangadex.org/manga/{mid}/feed?limit=5&translatedLanguage[]=en&order[chapter]=desc')
        out = 'Four Knights - Ultimi capitoli:\n'
        for ch in c['data']:
            a = ch['attributes']
            out += f"\nCap. {a.get('chapter')} - {a.get('title')}"
        return out
    except:
        return 'Non riesco a controllare i capitoli'

def countdown():
    gg = (date(2027,6,4) - date.today()).days
    return f'Mancano {gg} giorni al 4 Giugno 2027'

MONSTER = 'Monster Energy:\n1. Original\n2. Ultra White\n3. Ultra Paradise\n4. Ultra Rosa\n5. Pipeline Punch\n6. Mango Loco\n7. Lewis Hamilton 44\n8. Juiced Monarch\n9. Rehab\n10. Java Monster'
REDBULL = 'Red Bull:\n1. Original\n2. Sugarfree\n3. Zero\n4. Red Edition Anguria\n5. Blue Edition Mirtillo\n6. Yellow Edition Tropicale\n7. Green Edition Fico d India\n8. White Edition Cocco\n9. Winter Edition\n10. Summer Edition'
COMANDI = 'Comandi:\n/meteo\n/macumbe\n/anime\n/canzoni\n/4koa\n/countdown\n/monster\n/redbull\n/toc\n/generat\n/crashout\n/start'

STATO = {}

def handle(update):
    msg = update.get("message")
    if not msg: return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    low = text.lower()
    if chat_id in STATO:
        fase = STATO[chat_id]
        if fase == 'nome':
            STATO[chat_id] = 'materia_' + text
            send(chat_id, f'Ok macumbe a {text}.\nOra dimmi: Storia / Matematica / Italiano / Inglese')
            return
        if fase.startswith('materia_'):
            nome = fase.replace('materia_', '')
            materia = text.capitalize()
            if materia not in ['Storia','Matematica','Italiano','Inglese']:
                send(chat_id, 'Scrivi una di queste: Storia, Matematica, Italiano, Inglese')
                return
            del STATO[chat_id]
            send(chat_id, f'Macumba lanciata a {nome} in {materia}. Domani prende 4.')
            return
    if low == '/start':
        send(chat_id, 'Ciao! Sono acceso.\nScrivi /comandi')
    elif low == '/comandi':
        send(chat_id, COMANDI)
    elif low == '/meteo':
        send(chat_id, meteo())
    elif low == '/macumbe':
        STATO[chat_id] = 'nome'
        send(chat_id, 'A chi vuoi fare le macumbe?\nScrivi il nome:')
    elif low == '/anime':
        send(chat_id, anime_top())
    elif low == '/canzoni':
        send(chat_id, canzoni_top())
    elif low == '/4koa':
        send(chat_id, quattro())
    elif low == '/countdown':
        send(chat_id, countdown())
    elif low == '/monster':
        send(chat_id, MONSTER)
    elif low == '/redbull':
        send(chat_id, REDBULL)
    elif low == '/toc':
        send(chat_id, 'TESTA' if random.random() > 0.5 else 'CROCE')
    elif low == '/generat':
        lettere = ''.join(random.choice(string.ascii_letters) for _ in range(5))
        numeri = ''.join(random.choice(string.digits) for _ in range(4))
        send(chat_id, 'Password: ' + lettere + numeri)
    elif low == '/crashout':
        send(chat_id, 'CRASHOUT!!!')
        for _ in range(5):
            time.sleep(1)
            send(chat_id, 'Crush Crush Crush')
    elif low == '/buongiorno':
        send(chat_id, 'Buongiorno automatico attivo alle 8:00')
    elif low == '/buonanotte':
        send(chat_id, 'Buonanotte automatica attiva alle 22:00')

@app.route('/', methods=['GET'])
def home():
    return 'Bot acceso!'

@app.route('/', methods=['POST'])
def webhook():
    try:
        handle(request.get_json(force=True))
    except Exception as e:
        print(e)
    return 'ok'

def polling():
    o = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates?offset={o}&timeout=10", timeout=20)
            for u in r.json().get('result', []):
                o = u['update_id'] + 1
                handle(u)
        except:
            pass
        time.sleep(1)

threading.Thread(target=polling, daemon=True).start()
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
