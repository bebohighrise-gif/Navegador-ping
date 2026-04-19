import os
import subprocess
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from downloader import download_apk
from patcher import apply_patches

# --- CONFIGURACIÓN ---
TOKEN = "TU_TOKEN_AQUI"
BRANDING = "app.daikel ⚡"
SIGN_NAME = "GhostProtocol"

def preparar_entorno():
    """Genera la firma y carpetas si no existen"""
    for d in ["downloads", "workdir", "output"]:
        os.makedirs(d, exist_ok=True)
    
    if not os.path.exists("key.jks"):
        print("Generando firma GhostProtocol...")
        cmd = (
            'keytool -genkey -v -keystore key.jks -keyalg RSA -keysize 2048 '
            '-validity 10000 -alias ghost -storepass 123456 -keypass 123456 '
            '-dname "CN=GhostProtocol, O=Daikel, C=US"'
        )
        subprocess.run(cmd, shell=True)

# Ejecutar preparación
preparar_entorno()

user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💀 **{SIGN_NAME} Activo**\nEnvía un link de Play Store.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if "play.google.com" in update.message.text:
        user_data[uid] = {"url": update.message.text.split()[0]}
        buttons = [
            [InlineKeyboardButton("🔓 Premium / Paga Gratis", callback_data='mod_premium')],
            [InlineKeyboardButton("🚫 Quitar Anuncios", callback_data='mod_ads')],
            [InlineKeyboardButton("✍️ Personalizar", callback_data='mod_custom')]
        ]
        await update.message.reply_text("¿Qué acción deseas aplicar?", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()

    if query.data == 'mod_custom':
        user_data[uid]["waiting_desc"] = True
        await query.edit_message_text("✍️ Dime qué quieres que haga la app:")
    else:
        user_data[uid]["desc"] = query.data
        await run_engine(query, context, uid)

async def run_engine(update, context, uid):
    data = user_data[uid]
    desc_txt = data.get("desc", "Mod General")
    
    status = await (update.message.reply_text if hasattr(update, 'message') else update.edit_message_text)(
        f"⚙️ **GhostProtocol trabajando...**\n`Mod: {desc_txt}`"
    )

    # 1. Descargar
    path = download_apk(data["url"])
    if not path:
        return await context.bot.send_message(uid, "❌ Error al descargar.")

    # 2. Modding (Descompilar -> Parchear -> Reconstruir)
    work = f"workdir/{uid}"
    final = f"output/Ghost_{uid}.apk"
    
    subprocess.run(f"apktool d {path} -o {work} -f", shell=True)
    apply_patches(work, desc_txt)
    subprocess.run(f"apktool b {work} -o {final}", shell=True)
    
    # 3. Firmar
    sign_cmd = f"apksigner sign --ks key.jks --ks-pass pass:123456 --out {final} {final}"
    subprocess.run(sign_cmd, shell=True)

    # 4. Enviar
    await context.bot.send_document(
        chat_id=uid,
        document=open(final, "rb"),
        caption=f"✅ **Listo por GhostProtocol**\nFirma: {BRANDING}"
    )
    # Limpieza
    if os.path.exists(final): os.remove(final)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == '__main__':
    main()                  
