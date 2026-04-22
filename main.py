import telebot
import subprocess
import re
import os
import json
import shutil
import google.generativeai as genai

# ========= CONFIGURACIÓN (tokens visibles) =========
TELEGRAM_TOKEN = "8530157577:AAFV3q3X7W3lhNZi_tevNkWZUX8-9SUGVoQ"
GEMINI_API_KEY = "AIzaSyCEgTnVak5WkUl-SOzA5yyp1y4TLtdSvxg"  # Obtén gratis en https://aistudio.google.com

genai.configure(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Estado por usuario
user_data = {}

# ========= VALIDACIÓN DE URL =========
def is_valid_playstore_url(url):
    return re.match(r'^https?://play\.google\.com/store/apps/details\?id=', url) is not None

# ========= IA RAZONADORA CON GEMINI (GRATIS) =========
def razonar_intencion(mensaje_usuario):
    prompt = f"""
Eres un asistente que analiza mensajes de usuarios para un bot que descarga y modifica APKs de Google Play.
Extrae la siguiente información en formato JSON:
- "url": si el usuario proporciona una URL de Play Store, escríbela; si no, pon null.
- "app_name": nombre de la app que quiere modificar (si no da URL).
- "mods": lista de modificaciones deseadas, puede ser: ["remove_ads", "unlock_premium", "remove_watermark", "custom_patch"].
- "confianza": número del 0 al 1 indicando qué tan claro está.

Mensaje: "{mensaje_usuario}"
Responde SOLO con el JSON, sin texto adicional.
"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')  # Modelo gratuito y rápido
        respuesta = model.generate_content(prompt)
        texto = respuesta.text.strip()
        # Limpiar posibles marcadores de código
        if texto.startswith('```json'):
            texto = texto.split('```json')[1].split('```')[0]
        elif texto.startswith('```'):
            texto = texto.split('```')[1].split('```')[0]
        return json.loads(texto)
    except Exception as e:
        print(f"Error con Gemini: {e}")
        return {"url": None, "app_name": None, "mods": [], "confianza": 0}

# ========= COMANDOS =========
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🧠 Bot con IA Gemini (gratis). Envíame un mensaje como:\n'Quita anuncios de WhatsApp'\no directamente una URL de Play Store.")
    user_data[message.chat.id] = {'step': 'esperando_instruccion'}

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'esperando_instruccion')
def procesar_con_ia(message):
    bot.reply_to(message, "🤖 Analizando tu petición con Gemini...")
    intent = razonar_intencion(message.text)
    
    if intent.get("confianza", 0) < 0.5:
        bot.reply_to(message, "No entendí bien. Por favor, escribe la URL de Play Store y dime qué modificación quieres.\nEjemplo: 'Descarga Spotify y quita anuncios'")
        return
    
    url = intent.get("url")
    mods = intent.get("mods", [])
    
    if not url:
        bot.reply_to(message, "🔍 No veo una URL. Envíame el enlace de Google Play.")
        return
    
    if not is_valid_playstore_url(url):
        bot.reply_to(message, "❌ URL no válida. Debe ser de play.google.com/store/apps/details?id=...")
        return
    
    bot.reply_to(message, f"✅ Descargando y aplicando: {', '.join(mods) if mods else 'modificación estándar'}. Puede tardar unos minutos.")
    
    # Ejecutar downloader.py pasando URL y modificaciones
    try:
        result = subprocess.run(
            ['python3', 'downloader.py', url, json.dumps(mods)],
            capture_output=True,
            text=True,
            timeout=180
        )
        if result.returncode != 0:
            bot.reply_to(message, f"❌ Error interno: {result.stderr}")
        else:
            apk_path = "modificado.apk"
            if os.path.exists(apk_path):
                with open(apk_path, 'rb') as f:
                    bot.send_document(message.chat.id, f, caption="✅ APK modificado según tu petición")
                os.remove(apk_path)
            else:
                bot.reply_to(message, "⚠️ No se generó el APK.")
    except subprocess.TimeoutExpired:
        bot.reply_to(message, "⏰ Tiempo agotado. La app es muy grande.")
    except Exception as e:
        bot.reply_to(message, f"💥 Error: {str(e)}")
    finally:
        # Limpiar directorios temporales
        for d in ["temp", "workdir", "downloads"]:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        user_data.pop(message.chat.id, None)

bot.infinity_polling()
