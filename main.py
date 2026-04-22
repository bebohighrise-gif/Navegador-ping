import telebot
import subprocess
import os
import json
import shutil
import google.generativeai as genai
from datetime import datetime

# ========= CONFIGURACIÓN =========
TELEGRAM_TOKEN = "8530157577:AAFV3q3X7W3lhNZi_tevNkWZUX8-9SUGVoQ"
GEMINI_API_KEY = "AIzaSyCEgTnVak5WkUl-SOzA5yyp1y4TLtdSvxg"

genai.configure(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ========= ALMACENAMIENTO POR USUARIO =========
# Cada usuario tiene: historial de chat, datos recopilados, estado
user_sessions = {}

def get_gemini_model():
    return genai.GenerativeModel('gemini-1.5-flash')

# ========= PROMPT SISTEMA PARA GEMINI =========
SYSTEM_PROMPT = """
Eres un asistente que ayuda a un bot a descargar y modificar APKs de Google Play.
Tu trabajo es mantener una conversación natural con el usuario para averiguar:
1. La URL de Google Play de la app (si no la da, pídesela).
2. Las modificaciones que quiere: remove_ads, unlock_premium, remove_watermark, custom_patch.
3. Cualquier otro detalle relevante.

Reglas:
- Si el usuario ya dio la URL, no la pidas de nuevo.
- Si no ha dado modificaciones, pregúntale qué le gustaría cambiar (anuncios, premium, etc.).
- Cuando tengas URL y al menos una modificación, responde con un JSON exactamente así:
  {"accion": "procesar", "url": "la_url", "mods": ["remove_ads", ...]}
- Si aún necesitas más información, responde con un mensaje natural haciendo una pregunta.
- No inventes URLs. Si no está segura, pide confirmación.
- Sé breve y amable.
"""

def construir_historial(user_id):
    """Construye el contexto para Gemini a partir del historial del usuario."""
    session = user_sessions.get(user_id, {"history": []})
    historial = session["history"]
    # Convertir historial a formato de Gemini
    gemini_history = []
    for msg in historial:
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})
    return gemini_history

def llamar_gemini(user_id, user_message):
    """Envía el historial + nuevo mensaje a Gemini y devuelve su respuesta."""
    session = user_sessions.setdefault(user_id, {"history": []})
    # Añadir mensaje del usuario al historial
    session["history"].append({"role": "user", "content": user_message, "timestamp": datetime.now()})
    
    # Construir historial para Gemini
    gemini_history = construir_historial(user_id)
    
    model = get_gemini_model()
    # Iniciar chat con el prompt de sistema
    chat = model.start_chat(history=gemini_history)
    # Enviar el prompt de sistema como primer mensaje (si es nuevo usuario)
    if len(gemini_history) == 0:
        response = chat.send_message(SYSTEM_PROMPT + "\n\nUsuario: " + user_message)
    else:
        response = chat.send_message(user_message)
    
    respuesta_texto = response.text.strip()
    # Guardar respuesta en historial
    session["history"].append({"role": "assistant", "content": respuesta_texto, "timestamp": datetime.now()})
    
    # Intentar parsear JSON si Gemini decide procesar
    try:
        if respuesta_texto.startswith('{'):
            data = json.loads(respuesta_texto)
            if data.get("accion") == "procesar":
                # Guardar datos en la sesión
                session["url"] = data.get("url")
                session["mods"] = data.get("mods", [])
                session["listo"] = True
                return {"type": "procesar", "url": session["url"], "mods": session["mods"]}
    except:
        pass
    
    # Si no es JSON, es una pregunta normal
    return {"type": "pregunta", "texto": respuesta_texto}

# ========= VALIDACIÓN URL =========
def is_valid_playstore_url(url):
    import re
    return re.match(r'^https?://play\.google\.com/store/apps/details\?id=', url) is not None

# ========= COMANDO START =========
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    user_sessions[user_id] = {"history": [], "listo": False}
    bot.reply_to(message, "🧠 Hola, soy tu asistente con Gemini. Cuéntame qué app quieres modificar y qué cambios deseas (quitar anuncios, desbloquear premium, etc.).")
    # Iniciar conversación con Gemini (mensaje vacío para que el sistema pregunte)
    respuesta = llamar_gemini(user_id, "Hola, quiero modificar una app.")
    if respuesta["type"] == "pregunta":
        bot.reply_to(message, respuesta["texto"])

# ========= MANEJO DE MENSAJES =========
@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    user_id = message.chat.id
    if user_id not in user_sessions:
        user_sessions[user_id] = {"history": [], "listo": False}
        bot.reply_to(message, "Usa /start para comenzar.")
        return
    
    session = user_sessions[user_id]
    if session.get("listo"):
        # Ya estamos procesando, ignorar nuevos mensajes hasta terminar
        bot.reply_to(message, "⏳ Ya estoy procesando tu solicitud anterior. Espera un momento.")
        return
    
    # Mostrar indicador de escritura
    bot.send_chat_action(user_id, 'typing')
    
    # Consultar a Gemini
    respuesta = llamar_gemini(user_id, message.text)
    
    if respuesta["type"] == "procesar":
        url = respuesta["url"]
        mods = respuesta["mods"]
        
        if not is_valid_playstore_url(url):
            bot.reply_to(message, "❌ La URL proporcionada no es válida. Por favor, envíame una URL de Play Store correcta.")
            # Limpiar estado para reintentar
            session["listo"] = False
            session["url"] = None
            session["mods"] = []
            return
        
        bot.reply_to(message, f"✅ Entendido. Procedo a descargar y aplicar: {', '.join(mods) if mods else 'modificaciones estándar'}. Esto puede tomar unos minutos.")
        
        # Ejecutar downloader.py
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
                        bot.send_document(user_id, f, caption="✅ ¡Listo! APK modificado según tus preferencias.")
                    os.remove(apk_path)
                else:
                    bot.reply_to(message, "⚠️ No se generó el APK. Revisa los logs.")
        except subprocess.TimeoutExpired:
            bot.reply_to(message, "⏰ Tiempo agotado. La app es demasiado grande.")
        except Exception as e:
            bot.reply_to(message, f"💥 Error: {str(e)}")
        finally:
            # Limpiar sesión y archivos temporales
            for d in ["temp", "workdir", "downloads"]:
                if os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]  # Resetear conversación
    else:
        # Enviar la pregunta de Gemini
        bot.reply_to(message, respuesta["texto"])

# ========= LIMPIEZA AUTOMÁTICA DE SESIONES ANTIGUAS (opcional) =========
def cleanup_old_sessions():
    """Elimina sesiones inactivas por más de 1 hora."""
    now = datetime.now()
    to_delete = []
    for uid, session in user_sessions.items():
        if session["history"]:
            last_msg = max(session["history"], key=lambda x: x["timestamp"])
            if (now - last_msg["timestamp"]).seconds > 3600:
                to_delete.append(uid)
    for uid in to_delete:
        del user_sessions[uid]

# Ejecutar limpieza cada 10 minutos (en un hilo aparte si quieres, pero simple)
import threading
def schedule_cleanup():
    while True:
        import time
        time.sleep(600)
        cleanup_old_sessions()

threading.Thread(target=schedule_cleanup, daemon=True).start()

# ========= INICIAR BOT =========
if __name__ == "__main__":
    print("🤖 Bot con Gemini conversacional iniciado...")
    bot.infinity_polling()
