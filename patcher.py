import os

def apply_patches(decompile_dir, description):
    desc = description.lower()
    
    # Recorre los archivos de la app descompilada
    for root, dirs, files in os.walk(decompile_dir):
        for file in files:
            if file.endswith(".smali"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # MOD: Plan de pago / Premium
                if "premium" in desc or "paga" in desc:
                    # Forzamos que los chequeos de suscripción retornen True (0x1)
                    if "isPremium" in content or "isPro" in content or "hasSubscription" in content:
                        content = content.replace("return v0", "const/4 v0, 0x1\n    return v0")

                # MOD: Quitar anuncios
                if "anuncios" in desc:
                    if "showAds" in content or "loadAd" in content:
                        content = content.replace("return v0", "const/4 v0, 0x0\n    return v0")

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
