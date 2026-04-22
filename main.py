import telebot
import subprocess
import re
import os
import json

TOKEN = "8530157577:AAFV3q3X7W3lhNZi_tevNkWZUX8-9SUGVoQ"  # Lo dejas como está
bot = telebot.TeleBot(TOKEN)

# Estado por usuario: esperando URL o no
user_data = {}

def is_valid_playstore_url(url):
    return re.match(r'^https?://play\.google\.com/store/apps/details\?id=.*', url)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Envíame una URL de Google Play y te devolveré el APK modificado.")
    user_data[message.chat.id] = {'step': 'waiting_url'}

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'waiting_url')
def handle_url(message):
    url = message.text.strip()
    if not is_valid_playstore_url(url):
        bot.reply_to(message, "URL no válida. Debe ser de play.google.com/store/apps/details?id=...")
        return

    bot.reply_to(message, "Procesando... puede tardar unos minutos.")
    try:
        # Ejecutar downloader.py con la URL como argumento (sin shell=True)
        result = subprocess.run(
            ['python3', 'downloader.py', url],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            bot.reply_to(message, f"Error interno: {result.stderr}")
        else:
            # Se espera que downloader.py genere un archivo .apk en cierta ruta
            apk_path = "modificado.apk"  # Ajusta según lo que genere downloader.py
            if os.path.exists(apk_path):
                with open(apk_path, 'rb') as f:
                    bot.send_document(message.chat.id, f)
            else:
                bot.reply_to(message, "No se generó el APK.")
    except subprocess.TimeoutExpired:
        bot.reply_to(message, "Tiempo de espera agotado.")
    except Exception as e:
        bot.reply_to(message, f"Error inesperado: {str(e)}")
    finally:
        user_data.pop(message.chat.id, None)  # Limpiar estado

bot.infinity_polling()
