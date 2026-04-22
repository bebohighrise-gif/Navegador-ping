#!/usr/bin/env python3
# downloader.py - Descarga y modifica APKs desde Google Play

import sys
import subprocess
import os
import re
import json
import shutil
from urllib.parse import urlparse, parse_qs

# ========= CONFIGURACIÓN =========
# Si usas gplaycli, descomenta y configura tu cuenta
# GPLAY_USER = "botarreglo@gmail.com"
# GPLAY_PASS = "Daikel07092768223"

def check_tool(tool):
    """Verifica que una herramienta esté instalada y en el PATH."""
    try:
        subprocess.run([tool, '--version'], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def extract_package_name(url):
    """Extrae el nombre del paquete de una URL de Play Store."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    if 'id' in query_params:
        return query_params['id'][0]
    # Fallback con regex
    match = re.search(r'id=([^&]+)', url)
    if match:
        return match.group(1)
    raise ValueError(f"No se pudo extraer package name de {url}")

def download_apk(package_name):
    """
    Descarga el APK original desde Google Play.
    Aquí debes implementar la descarga real. Ejemplo con gplaycli (sin shell=True):
    """
    output_apk = f"{package_name}.apk"
    
    # === MÉTODO 1: Usar gplaycli (requiere credenciales) ===
    # if not check_tool('gplaycli'):
    #     raise RuntimeError("gplaycli no está instalado. Instálalo con: pip install gplaycli")
    # subprocess.run([
    #     'gplaycli', '-d', package_name,
    #     '-u', GPLAY_USER, '-p', GPLAY_PASS,
    #     '-f', output_apk
    # ], check=True)
    
    # === MÉTODO 2: Simulación (solo para pruebas) ===
    print(f"Simulando descarga de {package_name} -> {output_apk}")
    with open(output_apk, 'wb') as f:
        f.write(b'Fake APK content for testing')
    
    return output_apk

def apply_mods(work_dir, mods):
    """
    Aplica las modificaciones solicitadas en los archivos smali.
    work_dir: directorio donde apktool descompuso el APK.
    mods: lista de strings como ['remove_ads', 'unlock_premium']
    """
    print(f"Aplicando modificaciones: {mods}")
    # Ejemplo: eliminar anuncios de Google
    if 'remove_ads' in mods:
        ads_paths = [
            os.path.join(work_dir, 'smali', 'com', 'google', 'android', 'gms', 'ads'),
            os.path.join(work_dir, 'smali', 'com', 'google', 'android', 'gms', 'adsettings'),
            os.path.join(work_dir, 'smali', 'com', 'google', 'android', 'gms', 'internal'),
        ]
        for path in ads_paths:
            if os.path.exists(path):
                shutil.rmtree(path)
                print(f"  - Eliminado: {path}")
    
    # Desbloquear premium (búsqueda simple de smali)
    if 'unlock_premium' in mods:
        premium_files = []
        for root, dirs, files in os.walk(work_dir):
            for file in files:
                if file.endswith('.smali') and ('premium' in file.lower() or 'pro' in file.lower()):
                    premium_files.append(os.path.join(root, file))
        for fpath in premium_files:
            # Aquí podrías editar el archivo smali para forzar premium
            # Ejemplo: cambiar const/4 v0, 0x0 a const/4 v0, 0x1
            print(f"  - Archivo premium encontrado: {fpath}")
            # Edición simple (esto es muy básico, cada app requiere análisis propio)
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if 'isPremium' in content or 'isPro' in content:
                # Reemplazar condicionales (esto es solo ilustrativo)
                content = content.replace('const/4 v0, 0x0', 'const/4 v0, 0x1')
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  - Modificado para premium: {fpath}")
    
    # Eliminar marca de agua (placeholder)
    if 'remove_watermark' in mods:
        # Buscar archivos con "watermark" y eliminarlos o parchearlos
        for root, dirs, files in os.walk(work_dir):
            for file in files:
                if 'watermark' in file.lower():
                    os.remove(os.path.join(root, file))
                    print(f"  - Eliminado archivo watermark: {file}")
    
    return True

def modify_apk(original_apk, mods):
    """Descompila, aplica mods, recompila, alinea y firma el APK."""
    # Verificar herramientas necesarias
    required_tools = ['apktool', 'zipalign', 'apksigner']
    missing = [tool for tool in required_tools if not check_tool(tool)]
    if missing:
        raise RuntimeError(f"Herramientas faltantes: {', '.join(missing)}. Instálalas con tu gestor de paquetes.")

    work_dir = "workdir"
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    
    # Descompilar
    print("Descompilando APK...")
    subprocess.run(['apktool', 'd', original_apk, '-o', work_dir, '-f'], check=True)
    
    # Aplicar modificaciones
    apply_mods(work_dir, mods)
    
    # Recompilar
    print("Recompilando APK...")
    unaligned_apk = "unaligned.apk"
    subprocess.run(['apktool', 'b', work_dir, '-o', unaligned_apk], check=True)
    
    # Alinear
    print("Alineando APK (zipalign)...")
    aligned_apk = "aligned.apk"
    subprocess.run(['zipalign', '-v', '-p', '4', unaligned_apk, aligned_apk], check=True)
    
    # Firmar
    print("Firmando APK...")
    final_apk = "modificado.apk"
    # Verificar si existe key.jks
    if not os.path.exists('key.jks'):
        print("ADVERTENCIA: No se encontró key.jks. Genera uno con:")
        print("keytool -genkey -v -keystore key.jks -alias mykey -keyalg RSA -keysize 2048 -validity 10000")
        raise FileNotFoundError("Falta key.jks para firmar el APK")
    
    subprocess.run([
        'apksigner', 'sign',
        '--ks', 'key.jks',
        '--ks-pass', 'pass:123456',
        '--out', final_apk, aligned_apk
    ], check=True)
    
    # Limpiar archivos temporales
    os.remove(unaligned_apk)
    os.remove(aligned_apk)
    shutil.rmtree(work_dir)
    os.remove(original_apk)
    
    print(f"APK modificado generado: {final_apk}")
    return final_apk

def main():
    if len(sys.argv) < 3:
        print("Uso: python downloader.py <url_de_playstore> <json_mods>")
        print("Ejemplo: python downloader.py 'https://play.google.com/store/apps/details?id=com.whatsapp' '[\"remove_ads\", \"unlock_premium\"]'")
        sys.exit(1)
    
    url = sys.argv[1]
    try:
        mods = json.loads(sys.argv[2])
    except json.JSONDecodeError:
        print("Error: el segundo argumento debe ser un JSON válido, por ejemplo '[\"remove_ads\"]'")
        sys.exit(1)
    
    # Validar URL
    if not re.match(r'^https?://play\.google\.com/store/apps/details\?id=', url):
        print("URL inválida. Debe ser de play.google.com/store/apps/details?id=...")
        sys.exit(1)
    
    try:
        package = extract_package_name(url)
        print(f"Nombre del paquete: {package}")
        original_apk = download_apk(package)
        modified_apk = modify_apk(original_apk, mods)
        print(f"OK: {modified_apk}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
