import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import base64
import time
import shutil
import subprocess
from playwright.sync_api import sync_playwright

def find_chrome():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chromium"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None

def login_flow(output_file="kofi_state.json", port=9223):
    print("=" * 64)
    print("      CONFIGURADOR DE SESION DE KO-FI PARA RATTLE BOT")
    print("=" * 64)
    
    chrome_path = find_chrome()
    if not chrome_path:
        print("❌ No se encontró Google Chrome ni Microsoft Edge instalado en el sistema.")
        return False
        
    profile_dir = os.path.abspath("./kofi_profile")
    os.makedirs(profile_dir, exist_ok=True)
    
    print(f"Navegador detectado: {chrome_path}")
    print(f"Lanzando navegador nativo en puerto CDP {port}...")
    
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "https://ko-fi.com/account/login"
    ]
    
    chrome_proc = subprocess.Popen(cmd)
    print("Esperando a que el navegador inicie...")
    time.sleep(3)
    
    with sync_playwright() as p:
        browser = None
        context = None
        for _ in range(10):
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                break
            except Exception:
                time.sleep(1)
                
        if not browser or not context:
            print("❌ No se pudo conectar al navegador mediante CDP.")
            try:
                chrome_proc.terminate()
            except Exception:
                pass
            return False
            
        print("\n" + "=" * 64)
        print(">> ACCIÓN REQUERIDA EN EL NAVEGADOR:")
        print("1. En la ventana que se abrió, ingresa tu usuario y contraseña de Ko-fi.")
        print("2. Marca 'Remember Me' (Mantener la sesión iniciada).")
        print("3. Supera cualquier captcha o verificación si aparece.")
        print("4. Cuando ya veas tu panel o feed principal de Ko-fi:")
        print("   Escribe 'listo' y presiona ENTER aquí.")
        print("=" * 64 + "\n")
        
        if sys.platform == "win32":
            try:
                import msvcrt
                while msvcrt.kbhit():
                    msvcrt.getch()
            except Exception:
                pass
                
        resp = ""
        while resp.strip().lower() != "listo":
            resp = input("Escribe 'listo' y presiona ENTER cuando ya estés adentro: ")
            
        context.storage_state(path=output_file)
        
        with open(output_file, "r", encoding="utf-8") as f:
            state_data = json.load(f)
        cookies = state_data.get("cookies", [])
        print(f"\n✅ [OK] Sesión guardada en '{output_file}' con {len(cookies)} cookies capturadas.")
        
        print("\nVerificando acceso a panel de publicaciones...")
        verify_page = context.new_page()
        verify_page.goto("https://ko-fi.com/manage/posts", wait_until="domcontentloaded")
        verify_page.wait_for_timeout(2500)
        
        if "login" in verify_page.url.lower():
            print("⚠️ [AVISO] Pareciera que la sesión no quedó activa (redirigió a login).")
            print("Vuelve a ejecutar 'python kofi_setup.py' asegurándote de completar el login.")
        else:
            print(f"🎉 [ÉXITO] Confirmado: Acceso correcto al panel (URL: {verify_page.url})")
            
        try:
            browser.close()
        except Exception:
            pass
        try:
            chrome_proc.terminate()
        except Exception:
            pass
            
    print_b64_instructions(output_file)
    return True

def print_b64_instructions(output_file="kofi_state.json"):
    if not os.path.exists(output_file):
        print(f"❌ Archivo '{output_file}' no encontrado.")
        return
        
    with open(output_file, "rb") as f:
        b64_val = base64.b64encode(f.read()).decode("utf-8")
        
    print("\n" + "=" * 64)
    print("PASO FINAL PARA GITHUB ACTIONS:")
    print("Para que Rattle pueda publicar automáticamente en la nube:")
    print("1. Ve a tu repositorio en GitHub -> Settings -> Secrets and variables -> Actions")
    print("2. Crea un nuevo Secret llamado: KOFI_STORAGE_STATE")
    print("3. Pega la siguiente cadena en base64 como valor:")
    print("-" * 64)
    print(b64_val)
    print("-" * 64)
    print("=" * 64)
    print(f"\nListo. '{output_file}' ya está protegido en .gitignore.")

def check_session(storage_file="kofi_state.json"):
    if not os.path.exists(storage_file):
        print(f"❌ No existe '{storage_file}'. Ejecuta primero 'python kofi_setup.py' para iniciar sesión.")
        return False
        
    print(f"🔍 Comprobando validez de sesión en '{storage_file}'...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=storage_file)
            page = context.new_page()
            page.goto("https://ko-fi.com/manage/posts", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2000)
            
            url = page.url.lower()
            if "login" in url:
                print("❌ La sesión está caducada o no es válida (redirige a login).")
                browser.close()
                return False
            else:
                title = page.title()
                print(f"✅ ¡Sesión activa y válida!")
                print(f"   URL: {page.url}")
                print(f"   Título: {title}")
                browser.close()
                return True
        except Exception as e:
            print(f"⚠️ Error al comprobar sesión: {e}")
            return False

def post_to_kofi_manual(title, content, storage_file="kofi_state.json"):
    if not os.path.exists(storage_file):
        print(f"❌ No existe '{storage_file}'. Inicia sesión primero.")
        return False
        
    print(f"📝 Publicando en Ko-fi: '{title}'...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=storage_file)
            page = context.new_page()
            page.goto("https://ko-fi.com/manage/posts", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            
            if "login" in page.url.lower():
                print("❌ Sesión inválida.")
                browser.close()
                return False
                
            new_btn = page.locator('a:has-text("Add Post"), button:has-text("Add Post"), a:has-text("Write Post"), a[href*="post"]').first
            if new_btn.is_visible(timeout=5000):
                new_btn.click()
                page.wait_for_timeout(2000)
            else:
                page.goto("https://ko-fi.com/post/create", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                
            title_box = page.locator('input[placeholder*="Title" i], input[name="title"], input[id*="title" i]').first
            if title_box.is_visible(timeout=5000):
                title_box.fill(title)
                
            body_box = page.locator('div[contenteditable="true"], textarea[name="content"], textarea[placeholder*="content" i], .ProseMirror').first
            if body_box.is_visible(timeout=5000):
                body_box.click()
                body_box.fill(content)
                
            publish_btn = page.locator('button:has-text("Publish"), button:has-text("Post"), input[value*="Publish"]').first
            if publish_btn.is_visible(timeout=5000):
                publish_btn.click()
                page.wait_for_timeout(4000)
                print(f"🎉 Publicación exitosa: {page.url}")
                browser.close()
                return True
            else:
                print("⚠️ No se encontró botón directo de publicar.")
                browser.close()
                return False
        except Exception as e:
            print(f"❌ Error al publicar en Ko-fi: {e}")
            return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ["--check", "-c", "--test", "-t"]:
            check_session()
        elif arg in ["--export", "-e", "--base64"]:
            print_b64_instructions()
        elif arg in ["--post", "-p"] and len(sys.argv) > 3:
            post_to_kofi_manual(sys.argv[2], sys.argv[3])
        elif arg in ["--help", "-h"]:
            print("Uso:")
            print("  python kofi_setup.py          # Iniciar sesión interactiva y guardar kofi_state.json")
            print("  python kofi_setup.py --check  # Verificar si la sesión actual sigue activa")
            print("  python kofi_setup.py --export # Mostrar el Secret base64 para GitHub Actions")
            print("  python kofi_setup.py --post <titulo> <contenido> # Probar publicación")
        else:
            print(f"Argumento desconocido: {arg}. Usa --help para más información.")
    else:
        login_flow()
