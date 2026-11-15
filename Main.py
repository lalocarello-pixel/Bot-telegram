import os
import requests
import threading
import time
from flask import Flask

TOKEN = os.environ.get("TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)

def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(e)

def handle(update):
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if text == "/start":
        send(chat_id, "Ciao! Sono FallouDiop_Bot, sono online ✅\nScrivi qualsiasi cosa e ti rispondo.")
    else:
        send(chat_id, f"Hai scritto: {text}")

def polling():
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35)
            data = r.json()
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                handle(u)
        except Exception as e:
            print(e)
            time.sleep(3)

@app.route("/")
def home():
    return "Bot attivo!"

if __name__ == "__main__":
    threading.Thread(target=polling, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
