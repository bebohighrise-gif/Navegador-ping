import sys
import subprocess
import os
import re
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# Credenciales expuestas (las dejas)
EMAIL = "botarreglo@gmail.com"
PASSWORD = "Daikel07092768223"

def check_tool(tool_name):
    """Verifica si una herramienta está instalada y accesible."""
    try:
        subprocess.run([tool_name, '--version'], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def download_apk_from_playstore(url):
    # Simulación de descarga real (deberías usar gdown o similar)
    # Por seguridad, evitamos comandos con shell=True
    apk_id = re.search(r'id=([^&]+)', url).group(1)
    output_apk = f"{apk_id}.apk"
    # Aquí iría la lógica real sin shell. Ejemplo con gplaycli:
    # subprocess.run(['gplaycli', '-d', apk_id, '-f', output_apk], check=True)
    # Como placeholder, creamos un archivo vacío (solo para pruebas)
    with open(output_apk, 'wb') as f:
        f.write(b'Fake APK content')
    return output_apk

def modify_apk(original_apk):
    if not check_tool('apktool'):
        raise RuntimeError("apktool no está instalado o no está en el PATH")
    if not check_tool('apksigner'):
        raise RuntimeError("apksigner no está instalado")
    if not check_tool('zipalign'):
        raise RuntimeError("zipalign no está instalado")

    # Descompilar
    subprocess.run(['apktool', 'd', original_apk, '-o', 'temp'], check=True)
    # Aquí modificas los archivos (ej: eliminar anuncios editando smali)
    # Recompilar
    subprocess.run(['apktool', 'b', 'temp', '-o', 'unaligned.apk'], check=True)
    # Alinear
    subprocess.run(['zipalign', '-v', '-p', '4', 'unaligned.apk', 'aligned.apk'], check=True)
    # Firmar (usa un keystore existente o genera uno)
    subprocess.run(['apksigner', 'sign', '--ks', 'mi-keystore.jks', '--ks-pass', 'pass:123456', 'aligned.apk'], check=True)
    os.rename('aligned.apk', 'modificado.apk')
    # Limpiar
    for f in ['unaligned.apk', original_apk]:
        if os.path.exists(f):
            os.remove(f)
    return 'modificado.apk'

def main():
    if len(sys.argv) < 2:
        print("Uso: python downloader.py <url_de_playstore>")
        sys.exit(1)
    url = sys.argv[1]
    if not re.match(r'^https?://play\.google\.com/store/apps/details\?id=', url):
        print("URL inválida")
        sys.exit(1)

    try:
        original = download_apk_from_playstore(url)
        modified = modify_apk(original)
        print(f"APK modificado guardado en {modified}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
