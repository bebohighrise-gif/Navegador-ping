from gpapi.googleplay import GooglePlayAPI
import os

# Configuración que proporcionaste
EMAIL = "botarreglo@gmail.com"
PASSWORD = "Daikel07092768223"
ANDROID_ID = "3c9a8f2e1b5d40a7"

def download_apk(url_usuario):
    try:
        # Extraer ID del paquete
        package_id = url_usuario.split("id=")[1].split("&")[0]
        api = GooglePlayAPI(locale="es_ES", timezone="UTC")
        
        # Login
        api.login(EMAIL, PASSWORD, ANDROID_ID)
        
        output_path = f"downloads/{package_id}.apk"
        os.makedirs("downloads", exist_ok=True)
        
        # Descarga
        data = api.download(package_id)
        with open(output_path, "wb") as f:
            for chunk in data:
                f.write(chunk)
        return output_path
    except Exception as e:
        print(f"Error Downloader: {e}")
        return None
