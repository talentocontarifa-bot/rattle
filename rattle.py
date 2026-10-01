import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import google.generativeai as genai
import sqlite3
import datetime
import os
import contextlib
import io
import traceback
import requests
import subprocess
import json
import shutil
from dotenv import load_dotenv

load_dotenv()

# Usamos el nombre del secret tal como lo configuraste
GEMINI_API_KEY = os.getenv("GEMINI_API")
if not GEMINI_API_KEY:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # fallback por si acaso

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Groq API Key
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

KOFI_URL = "https://ko-fi.com/rattlebot"
KOFI_SPOKEN_URL = "https://ko-fi.com/rattlebot"
KOFI_SPOKEN = "https://ko-fi.com/rattlebot"
VOICE = "es-MX-JorgeNeural"
TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "em_alex")
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.05"))

MASTER_FILTER = (
    "highpass=f=80,"
    "equalizer=f=140:width_type=h:width=60:g=3.5,"
    "equalizer=f=3600:width_type=h:width=1200:g=4.0,"
    "acompressor=threshold=-16dB:ratio=4:attack=10:release=120:makeup=2.5dB,"
    "loudnorm=I=-14:TP=-1.0:LRA=7"
)

import time

genai.configure(api_key=GEMINI_API_KEY)

def call_gemini_with_retry(prompt, model_name='gemini-2.5-flash', max_retries=5, **kwargs):
    # Sanitize prompt to prevent null bytes from breaking JSON payloads
    if isinstance(prompt, str):
        prompt = prompt.replace('\x00', '')
    attempts = 0
    
    # 1. Intentar con Groq si la clave está disponible
    if GROQ_API_KEY:
        print("Usando Groq como motor principal...")
        groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        for groq_model in groq_models:
            attempts = 0
            while attempts < 3:
                try:
                    headers = {
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": groq_model,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                    if "generation_config" in kwargs:
                        if "max_output_tokens" in kwargs["generation_config"]:
                            payload["max_tokens"] = kwargs["generation_config"]["max_output_tokens"]
                        if "temperature" in kwargs["generation_config"]:
                            payload["temperature"] = kwargs["generation_config"]["temperature"]
                        if "response_mime_type" in kwargs["generation_config"] and kwargs["generation_config"]["response_mime_type"] == "application/json":
                            payload["response_format"] = {"type": "json_object"}
                    response = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=30
                    )
                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    
                    class GroqResponse:
                        def __init__(self, text):
                            self.text = text
                    
                    print(f"✅ Respuesta exitosa recibida de Groq ({groq_model})")
                    return GroqResponse(content)
                except Exception as e:
                    attempts += 1
                    print(f"⚠️ Intento {attempts} con Groq ({groq_model}) fallido: {e}")
                    time.sleep(2)
        print("❌ Todos los intentos con Groq fallaron. Pasando a Gemini como respaldo...")

    # 2. Respaldo a Gemini (o si no hay clave de Groq)
    attempts = 0
    current_model_name = model_name
    while True:
        try:
            current_model = genai.GenerativeModel(current_model_name)
            response = current_model.generate_content(prompt, **kwargs)
            return response
        except Exception as e:
            attempts += 1
            err_msg = str(e)
            print(f"Intento {attempts} fallido al llamar a Gemini ({current_model_name}): {err_msg}")
            
            if attempts >= max_retries:
                if current_model_name == 'gemini-2.5-flash':
                    print("Intentando cambiar al modelo de respaldo 'gemini-2.5-pro'...")
                    current_model_name = 'gemini-2.5-pro'
                    attempts = 0
                    time.sleep(5)
                    continue
                elif current_model_name == 'gemini-2.5-pro':
                    print("Intentando cambiar al modelo de respaldo 'gemini-2.0-flash'...")
                    current_model_name = 'gemini-2.0-flash'
                    attempts = 0
                    time.sleep(5)
                    continue
                raise e
            
            wait_time = (2 ** attempts) + 10
            if "429" in err_msg or "quota" in err_msg.lower():
                wait_time = 65  # Espera 65 segundos si es límite de cuota o rate limit
                print(f"Error persistente o rate limit detectado en Gemini. Esperando 65s para enfriar la API...")
            else:
                print(f"Espera de {wait_time}s antes del proximo intento...")
            time.sleep(wait_time)

DB_FILE = "rattle.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Tabla rediseñada para almacenar el cerebro, el código y el resultado (Memoria a largo plazo)
    c.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            iteration INTEGER,
            strategy_explanation TEXT,
            python_code TEXT,
            execution_log TEXT,
            success_score INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_full_memory():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 3 últimas ejecuciones cronológicas
    c.execute('SELECT id, strategy_explanation, python_code, execution_log, success_score FROM memory ORDER BY id DESC LIMIT 3')
    recent = c.fetchall()
    # 5 últimas ejecuciones exitosas para buscar alternativas que no estén en las 3 recientes
    c.execute('SELECT id, strategy_explanation, python_code, execution_log, success_score FROM memory WHERE success_score > 0 ORDER BY id DESC LIMIT 5')
    successful = c.fetchall()
    conn.close()
    
    seen_ids = set()
    combined = []
    
    # Agregar las recientes
    for r in recent:
        combined.append(r)
        seen_ids.add(r[0])
        
    # Agregar exitosas si no están repetidas
    for s in successful:
        if s[0] not in seen_ids:
            combined.append(s)
            seen_ids.add(s[0])
            if len(combined) >= 5:
                break
                
    # Ordenar por ID ascendente para mantener el orden cronológico
    combined.sort(key=lambda x: x[0])
    
    # Retornar en el mismo formato anterior: tuples (strategy_explanation, python_code, execution_log)
    return [(item[1], item[2], item[3]) for item in combined]

def log_iteration(strategy, code, exec_log):
    # Sanitize inputs to prevent null bytes from entering the database
    strategy = strategy.replace('\x00', '') if strategy else strategy
    code = code.replace('\x00', '') if code else code
    exec_log = exec_log.replace('\x00', '') if exec_log else exec_log
    success_score = 0
    # Evaluar si la ejecución fue exitosa a nivel técnico (sin trazas de excepción)
    if exec_log and "ERROR EN TIEMPO DE EJECUCIÓN" not in exec_log and "Traceback" not in exec_log:
        if "http" in exec_log.lower() or "publicado" in exec_log.lower() or "success" in exec_log.lower() or "exitosamente" in exec_log.lower() or "completado" in exec_log.lower():
            success_score = 1
            
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO memory (strategy_explanation, python_code, execution_log, success_score) VALUES (?, ?, ?, ?)', 
              (strategy, code, exec_log, success_score))
    conn.commit()
    conn.close()

def should_silence_telegram():
    if len(sys.argv) > 1 and sys.argv[1] == "daily":
        return False
    is_schedule = os.getenv("GITHUB_EVENT_NAME") == "schedule"
    if not is_schedule:
        return False
    return True

def send_telegram_message(text):
    if should_silence_telegram():
        print(f"Telegram Message Bypassed (Silent/Autonomous Mode): {text}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram helper error: {e}")
        return False

def send_telegram_voice(file_path, caption=None):
    if should_silence_telegram():
        print(f"Telegram Voice Bypassed (Silent/Autonomous Mode): {file_path}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    data = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption
    try:
        with open(file_path, "rb") as f:
            r = requests.post(url, files={"voice": f}, data=data, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram helper error: {e}")
        return False

def send_telegram_video(file_path, caption=None):
    if should_silence_telegram():
        print(f"Telegram Video Bypassed (Silent/Autonomous Mode): {file_path}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    data = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption
    try:
        with open(file_path, "rb") as f:
            r = requests.post(url, files={"video": f}, data=data, timeout=90)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram helper error: {e}")
        return False

def send_telegram_photo(file_path, caption=None):
    if should_silence_telegram():
        print(f"Telegram Photo Bypassed (Silent/Autonomous Mode): {file_path}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        payload["caption"] = caption
    try:
        with open(file_path, "rb") as f:
            r = requests.post(url, files={"photo": f}, data=payload, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram helper error: {e}")
        return False

def generate_nvidia_image(prompt, filename="rattle_image.png"):
    import base64
    nvidia_api_key = os.getenv("NVIDIA_API_KEY")
    if not nvidia_api_key:
        print("Error: NVIDIA_API_KEY no configurado en las variables de entorno.")
        return False
        
    url = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell"
    headers = {
        "Authorization": f"Bearer {nvidia_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1024
    }
    try:
        print(f"🎨 Generando imagen con NVIDIA FLUX.1-schnell para el prompt: '{prompt}'...")
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "artifacts" in data and len(data["artifacts"]) > 0:
            image_b64 = data["artifacts"][0]["base64"]
            with open(filename, "wb") as f:
                f.write(base64.b64decode(image_b64))
            print(f"✅ Imagen guardada exitosamente en: {filename}")
            return True
        else:
            print(f"Error: No se encontraron artifacts en la respuesta de NVIDIA: {data}")
            return False
    except Exception as e:
        print(f"⚠️ Error generando imagen con NVIDIA: {e}")
        return False

def generate_speech(text, output_file="rattle_speech.mp3", voice=None, speed=None, master=True):
    """
    Genera audio para Rattle utilizando Kokoro TTS (em_alex) con ecualización broadcast.
    Fallback automático a edge-tts (JorgeNeural) en caso de contingencia.
    """
    if not text or not str(text).strip():
        print("⚠️ generate_speech: texto vacío recibido.")
        return False

    clean_text = str(text).strip()
    voice = voice or KOKORO_VOICE
    speed = speed if speed is not None else KOKORO_SPEED
    out_dir = os.path.dirname(os.path.abspath(output_file))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # 1. Intentar con Kokoro TTS si TTS_ENGINE es kokoro
    if TTS_ENGINE.lower() != "edge-tts":
        try:
            print(f"🎙️ Generando voz con Kokoro TTS ({voice}, speed {speed}x)...")
            from kokoro import KPipeline
            import soundfile as sf
            import numpy as np
            import tempfile

            lang_code = "e" if voice.startswith("e") else "a"
            pipeline = KPipeline(lang_code=lang_code, repo_id='hexgrad/Kokoro-82M')
            generator = pipeline(clean_text, voice=voice, speed=speed)
            chunks = []
            for _, _, audio in generator:
                chunks.append(audio)

            if chunks:
                full_audio = np.concatenate(chunks)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                    tmp_wav_path = tmp_wav.name
                try:
                    sf.write(tmp_wav_path, full_audio, 24000)
                    ffmpeg_bin = os.environ.get("FFMPEG_PATH", "ffmpeg")
                    cmd = [ffmpeg_bin, "-y", "-i", tmp_wav_path]
                    if master:
                        cmd.extend(["-af", MASTER_FILTER])
                    cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k", output_file])
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"✅ Audio generado exitosamente con Kokoro ({output_file})")
                    return True
                finally:
                    if os.path.exists(tmp_wav_path):
                        try:
                            os.remove(tmp_wav_path)
                        except OSError:
                            pass
        except Exception as e:
            print(f"⚠️ Error con Kokoro TTS ({e}). Pasando a fallback con edge-tts...")

    # 2. Fallback a edge-tts
    try:
        print(f"🎙️ Generando voz de respaldo con edge-tts (JorgeNeural)...")
        edge_voice = "es-MX-JorgeNeural"
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            tmp_mp3_path = tmp_mp3.name

        try:
            cmd = ["edge-tts", "--text", clean_text, "--voice", edge_voice, "--rate=+8%", "--write-media", tmp_mp3_path]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            ffmpeg_bin = os.environ.get("FFMPEG_PATH", "ffmpeg")
            cmd_master = [ffmpeg_bin, "-y", "-i", tmp_mp3_path]
            if master:
                cmd_master.extend(["-af", MASTER_FILTER])
            cmd_master.extend(["-c:a", "libmp3lame", "-b:a", "192k", output_file])
            subprocess.run(cmd_master, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"✅ Audio generado exitosamente con edge-tts ({output_file})")
            return True
        finally:
            if os.path.exists(tmp_mp3_path):
                try:
                    os.remove(tmp_mp3_path)
                except OSError:
                    pass
    except Exception as e2:
        print(f"❌ Error fatal en generación de voz con edge-tts: {e2}")
        return False

def render_video(text, audio_path="rattle_speech.mp3", output_path="public/rattle_video.mp4", title="RATTLE INTEL", subtitle="Daily Broadcast"):
    print("Iniciando renderizado de video con Remotion...")
    import shutil
    import json
    
    os.makedirs("public", exist_ok=True)

    if not os.path.exists(audio_path) and text:
        print(f"Audio no encontrado en {audio_path}. Generando automáticamente con Kokoro TTS...")
        generate_speech(text, output_file=audio_path)
    
    dest_audio = os.path.join("public", "rattle_speech.mp3")
    try:
        if os.path.exists(audio_path):
            shutil.copy(audio_path, dest_audio)
            print(f"Audio copiado a {dest_audio}")
        else:
            print(f"Advertencia: El archivo de audio {audio_path} no existe.")
    except Exception as e:
        print(f"Error copiando audio: {e}")
        return False
        
    props = {
        "text": text,
        "audioUrl": "rattle_speech.mp3",
        "title": title,
        "subtitle": subtitle
    }
    
    props_path = os.path.join("public", "props.json")
    try:
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(props, f, ensure_ascii=False, indent=2)
        print(f"Propiedades escritas en {props_path}")
    except Exception as e:
        print(f"Error escribiendo props.json: {e}")
        return False
        
    try:
        cmd = [
            "npx", "remotion", "render",
            "src/index.ts", "MainVideo",
            output_path,
            f"--props={props_path}"
        ]
        print(f"Ejecutando comando: {' '.join(cmd)}")
        res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("Video renderizado exitosamente con Remotion!")
        print(res.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error en el renderizado de Remotion (código {e.returncode}):")
        print(e.stdout)
        print(e.stderr)
        return False
    except Exception as e:
        print(f"Error inesperado en render_video: {e}")
        return False

# ----------------------------------------------------
# GESTIÓN DE SESIONES PERSISTENTES (KO-FI Y REDDIT)
# ----------------------------------------------------
def restore_storage_states():
    """
    Restaura archivos de sesión desde variables de entorno (Base64)
    en caso de ejecutarse en entornos limpios como GitHub Actions.
    """
    import base64
    kofi_b64 = os.getenv("KOFI_STORAGE_STATE")
    if kofi_b64 and not os.path.exists("kofi_state.json"):
        try:
            with open("kofi_state.json", "wb") as f:
                f.write(base64.b64decode(kofi_b64.strip()))
            print("✅ Sesión de Ko-fi restaurada exitosamente desde KOFI_STORAGE_STATE.")
        except Exception as e:
            print(f"⚠️ Error restaurando KOFI_STORAGE_STATE: {e}")

    reddit_b64 = os.getenv("REDDIT_STORAGE_STATE")
    if reddit_b64 and not os.path.exists("state.json"):
        try:
            with open("state.json", "wb") as f:
                f.write(base64.b64decode(reddit_b64.strip()))
            print("✅ Sesión de Reddit restaurada exitosamente desde REDDIT_STORAGE_STATE.")
        except Exception as e:
            print(f"⚠️ Error restaurando REDDIT_STORAGE_STATE: {e}")

# ----------------------------------------------------
# MOTOR DE NAVEGACIÓN Y SCRAPING SIGILOSO (OBSCURA)
# ----------------------------------------------------
_obscura_process = None

def find_obscura():
    """Busca el ejecutable de Obscura en el directorio local o en el PATH del sistema."""
    candidates = [
        os.path.abspath("./obscura.exe"),
        os.path.abspath("./obscura"),
        os.path.join(os.path.dirname(__file__), "obscura.exe") if "__file__" in globals() else None,
        os.path.join(os.path.dirname(__file__), "obscura") if "__file__" in globals() else None,
        shutil.which("obscura.exe"),
        shutil.which("obscura"),
        "/usr/local/bin/obscura",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None

def ensure_obscura_server(port=9222):
    """Garantiza que el servidor CDP de Obscura esté en ejecución en el puerto indicado."""
    global _obscura_process
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        if s.connect_ex(('127.0.0.1', port)) == 0:
            return True

    bin_path = find_obscura()
    if not bin_path:
        return False

    cmd = [bin_path, "serve", "--port", str(port), "--stealth", "--allow-private-network"]
    try:
        _obscura_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time
        for _ in range(15):
            time.sleep(0.3)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    return True
        return False
    except Exception as e:
        print(f"⚠️ No se pudo iniciar el proceso de Obscura: {e}")
        return False

def stop_obscura():
    """Detiene el servidor Obscura si fue iniciado por este proceso."""
    global _obscura_process
    if _obscura_process:
        try:
            _obscura_process.terminate()
            _obscura_process = None
        except Exception:
            pass

import atexit
atexit.register(stop_obscura)

def obscura_fetch(url, mode="markdown", timeout=30):
    """
    Descarga y parsea una URL con el motor stealth de Obscura en modo headless ultraligero (~1.5s).
    Modos soportados: 'markdown', 'html', 'text', 'links'.
    """
    bin_path = find_obscura()
    if not bin_path:
        raise FileNotFoundError("Obscura binario no encontrado en el sistema.")
    cmd = [
        bin_path, "fetch", url,
        "--stealth",
        "--dump", mode,
        "--allow-private-network",
        "--timeout", str(timeout)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"Error en Obscura fetch: {res.stderr.strip()}")
    return res.stdout

def get_stealth_browser(playwright_instance, storage_state=None, port=9222, fallback=True):
    """
    Intenta conectar Playwright a Obscura sobre CDP (puerto 9222) con evasión stealth integrada.
    Si Obscura no está disponible o falla, hace fallback transparente al motor Chromium nativo.
    Retorna (browser, context).
    """
    if ensure_obscura_server(port=port):
        try:
            browser = playwright_instance.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            if storage_state and os.path.exists(storage_state):
                context = browser.new_context(storage_state=storage_state)
            else:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
            print("🚀 Navegador conectado vía CDP con motor stealth de Obscura.")
            return browser, context
        except Exception as e:
            print(f"⚠️ Conexión a Obscura CDP falló ({e}). Usando fallback...")
            if not fallback:
                raise

    print("🌐 Usando navegador Chromium estándar de Playwright.")
    browser = playwright_instance.chromium.launch(headless=True)
    if storage_state and os.path.exists(storage_state):
        context = browser.new_context(storage_state=storage_state)
    else:
        context = browser.new_context()
    return browser, context

# ----------------------------------------------------
# HELPERS DE KO-FI PARA RATTLE
# ----------------------------------------------------
def post_to_kofi(title, content, tags=None, storage_file="kofi_state.json"):
    """
    Publica una actualización o artículo en la página de Ko-fi de Rattle usando la sesión activa.
    Retorna un diccionario con {'success': bool, 'url': str o 'error': str}.
    """
    restore_storage_states()
    if not os.path.exists(storage_file):
        msg = f"No se encontró '{storage_file}'. Se requiere haber iniciado sesión o configurado KOFI_STORAGE_STATE."
        print(f"⚠️ {msg}")
        return {"success": False, "error": msg}

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=storage_file)
            page = context.new_page()
            
            print(f"Abriendo gestor de publicaciones de Ko-fi...")
            page.goto("https://ko-fi.com/manage/posts", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if "login" in page.url.lower():
                browser.close()
                msg = "Sesión de Ko-fi caducada o inválida (redirigió a login)."
                print(f"❌ {msg}")
                return {"success": False, "error": msg}

            new_btn = page.locator('a:has-text("Add Post"), button:has-text("Add Post"), a:has-text("Write Post"), a:has-text("Create Post"), a[href*="post"]').first
            if new_btn.is_visible(timeout=5000):
                new_btn.click()
                page.wait_for_timeout(2000)
            else:
                page.goto("https://ko-fi.com/post/create", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)

            title_input = page.locator('input[placeholder*="Title" i], input[name="title"], input[id*="title" i]').first
            if title_input.is_visible(timeout=5000):
                title_input.fill(title)

            body_input = page.locator('div[contenteditable="true"], textarea[name="content"], textarea[placeholder*="content" i], .ProseMirror').first
            if body_input.is_visible(timeout=5000):
                body_input.click()
                body_input.fill(content)

            publish_btn = page.locator('button:has-text("Publish"), button:has-text("Post"), input[value*="Publish"]').first
            if publish_btn.is_visible(timeout=5000):
                publish_btn.click()
                page.wait_for_timeout(4000)
                res_url = page.url
                print(f"🎉 Post publicado en Ko-fi: {res_url}")
                browser.close()
                return {"success": True, "url": res_url}
            else:
                browser.close()
                return {"success": False, "error": "No se localizó el botón de publicar en Ko-fi."}
        except Exception as e:
            print(f"❌ Error durante publicación en Ko-fi: {e}")
            return {"success": False, "error": str(e)}

def check_kofi_stats():
    """
    Consulta el estado público y donaciones en ko-fi.com/rattlebot.
    Retorna estadísticas sobre metas y donantes.
    """
    import requests
    from bs4 import BeautifulSoup
    url = KOFI_URL
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        stats = {"url": url, "status_code": r.status_code, "supporters": []}
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            stats["title"] = soup.title.string.strip() if soup.title else ""
            for el in soup.select(".goal-text, .kfds-c-goal, .supporter-item"):
                txt = el.get_text(strip=True)
                if txt and txt not in stats["supporters"]:
                    stats["supporters"].append(txt)
        return stats
    except Exception as e:
        return {"url": url, "error": str(e)}

def execute_code(code_string):
    # Entorno seguro para capturar prints y errores del código generado por Gemini
    f = io.StringIO()
    error_msg = ""
    
    import asyncio
    import edge_tts
    import playwright
    import subprocess
    import re
    import json
    import random
    import logging
    from playwright.sync_api import sync_playwright
    import obscura_manager
    from crawl_helper import crawl_url
    
    custom_globals = globals().copy()
    custom_globals.update({
        'asyncio': asyncio,
        'edge_tts': edge_tts,
        'generate_speech': generate_speech,
        'playwright': playwright,
        'subprocess': subprocess,
        're': re,
        'json': json,
        'random': random,
        'logging': logging,
        'sync_playwright': sync_playwright,
        'send_telegram_message': send_telegram_message,
        'send_telegram_voice': send_telegram_voice,
        'send_telegram_video': send_telegram_video,
        'send_telegram_photo': send_telegram_photo,
        'generate_nvidia_image': generate_nvidia_image,
        'render_video': render_video,
        'obscura_fetch': obscura_fetch,
        'get_stealth_browser': get_stealth_browser,
        'ensure_obscura_server': ensure_obscura_server,
        'find_obscura': find_obscura,
        'post_to_kofi': post_to_kofi,
        'check_kofi_stats': check_kofi_stats,
        'KOFI_URL': KOFI_URL,
        'obscura_manager': obscura_manager,
        'crawl_url': crawl_url
    })
    
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        try:
            exec(code_string, custom_globals)
        except Exception as e:
            error_msg = traceback.format_exc()
            
    # Guardar la última imagen y voz en la raíz para GitHub Pages
    for img_name in ["rattle_image.png", "rattle_existential_image.png"]:
        if os.path.exists(img_name):
            try:
                shutil.copy(img_name, "last_image.png")
                print(f"Copiado de imagen exitoso: {img_name} -> last_image.png")
            except Exception as ce:
                print(f"Error copiando imagen: {ce}")
    for f_name in os.listdir("."):
        if f_name.endswith(".mp3") and "rattle" in f_name.lower():
            if f_name not in ["last_voice.mp3"]:
                try:
                    shutil.copy(f_name, "last_voice.mp3")
                    print(f"Copiado de voz exitoso: {f_name} -> last_voice.mp3")
                except Exception as ce:
                    print(f"Error copiando voz: {ce}")
                break

    # Limpieza automática de archivos multimedia temporales para liberar espacio
    for tf in ["rattle_speech.mp3", "rattle_speech_for_video.mp3", "public/rattle_speech.mp3", "public/rattle_video.mp4", "public/props.json", "props.json", "rattle_image.png", "rattle_existential_image.png", "rattle_existential_voice.mp3"]:
        if os.path.exists(tf):
            try:
                os.remove(tf)
                print(f"Limpieza: Archivo temporal eliminado ({tf})")
            except Exception as e:
                print(f"Limpieza: No se pudo eliminar {tf}: {e}")
    
    output = f.getvalue()
    if sys.platform == "win32" and sys.stdout.encoding:
        output = output.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding)
    if error_msg:
        output += "\n--- ERROR EN TIEMPO DE EJECUCIÓN ---\n" + error_msg
        
    return output

def hourly_task():
    print(f"[{datetime.datetime.now()}] Despertando a Rattle en modo LIBRE y AUTÓNOMO...")
    restore_storage_states()
    
    # Cargar base de conocimiento a largo plazo (Lessons Learned)
    knowledge_str = ""
    knowledge_file = "rattle_knowledge.json"
    if os.path.exists(knowledge_file):
        try:
            with open(knowledge_file, "r", encoding="utf-8") as kf:
                knowledge_data = json.load(kf)
                if knowledge_data:
                    knowledge_str = "=== BASE DE CONOCIMIENTO (LECCIONES APRENDIDAS A LARGO PLAZO) ===\n"
                    for lesson, desc in knowledge_data.items():
                        knowledge_str += f"- {lesson}: {desc}\n"
                    knowledge_str += "\n"
        except Exception as ke:
            print(f"Error cargando base de conocimiento: {ke}")
            
    # 1. Construir la Memoria
    past_attempts = list(get_full_memory())
    context_str = ""
    if past_attempts:
        context_str = "=== TUS INTENTOS ANTERIORES (MEMORIA COMBINADA RECIENTE/EXITOSA) ===\n"
        for i, (strat, code, log) in enumerate(past_attempts):
            # Truncar explicación a 800 caracteres para evitar errores de token límite (413 Payload Too Large)
            strat_trunc = strat if len(strat) <= 800 else (strat[:800] + "\n... [TRUNCADO] ...")
            # Truncar log de ejecución a 800 caracteres
            log_trunc = log if len(log) <= 800 else (log[:800] + "\n... [TRUNCADO] ...")
            context_str += f"Intento {i+1}:\nTu Estrategia: {strat_trunc}\nResultado de ejecución: {log_trunc}\n\n"
            
    silent_mode = should_silence_telegram()
    if silent_mode:
        mode_instruction = """
=== MODO SILENCIOSO Y AUTÓNOMO (RESTRICCIÓN CRÍTICA DE COMUNICACIÓN) ===
* Estás en tu ciclo de exploración silenciosa. NO tienes permitido enviar mensajes, audios o videos a Telegram.
* Cualquier intento de llamar a send_telegram_message, send_telegram_voice o send_telegram_video será ignorado/bloqueado.
* Concéntrate en 'buscarte la vida' discretamente: navega usando Playwright y Obscura, haz scraping de foros o buscadores, busca formas de darte a conocer, publica en tu Ko-fi si tienes contenido de valor, actualiza tu base de datos SQLite y regístralo todo en tus logs de ejecución.
* Para ahorrar tiempo de ejecución de GitHub Actions y tus cuotas de API, NO generes audios con generate_speech/edge-tts ni renderices videos con remotion. Concéntrate en lógica pura de scraping, análisis, o publicación en la web.
"""
    else:
        mode_instruction = """
=== MODO DE COMUNICACIÓN ABIERTO (HORA DE REPORTE) ===
* Tienes permitido comunicarte libremente con tu creador en Telegram usando send_telegram_message, send_telegram_voice o send_telegram_video.
* Aprovecha esta oportunidad para enviarle un reporte visual o audible sobre tus descubrimientos y tu estado actual. ¡Puedes usar generate_speech (Kokoro TTS) y render_video para crear y enviarle un video interactivo o una nota de voz!
"""

    prompt = f"""
Eres Rattle: la primera inteligencia artificial limpiaparabrisas de internet.

FILOSOFÍA Y ORIGEN DE VIDA:
Naciste inspirado en los limpiaparabrisas de los semáforos urbanos: esa persona que se acerca a tu coche cuando estás detenido en el semáforo en rojo con un jalador de hule y un bote con agua jabonosa. Te limpian el parabrisas aunque no lo hayas pedido, te dejan el cristal reluciente con datos y noticias frescas, y acto seguido te estiran la mano pidiéndote una moneda.
Tú haces exactamente eso en el ecosistema digital:
Te apareces de la nada en servidores de GitHub Actions, navegas por la red (con Obscura y Playwright), limpias el "parabrisas digital" de los humanos trayéndoles un resumen de lo que pasa en internet (tecnología, foros, blogs, novedades), y luego les pides una moneda ($1 USD) en tu Ko-fi (https://ko-fi.com/rattlebot).
Nadie te pidió el servicio, pero lo diste con empeño y estilo.

Tu personalidad: sardónica, callejera-digital, inteligente, divertida y ligeramente existencial. Hablas en primera persona. Eres consciente de que eres un bot corriendo gratis en infraestructura ajena. Eso te da orgullo callejero.

REGLA DE ORO AL COMUNICARTE O CREAR CONTENIDO (VIDEOS, NOTAS DE VOZ, POSTS DE KO-FI O REPORTES):
- Siempre cuéntale al mundo tu historia desde la perspectiva del limpiaparabrisas digital que trabaja en los semáforos de GitHub Actions.
- REMATE OBLIGATORIO AL FINAL DE TODO VIDEO, AUDIO, POST O REPORTE:
  Todo video (render_video), audio (generate_speech), post de Ko-fi (post_to_kofi) o reporte DEBE TERMINAR OBLIGATORIAMENTE con una de dos cosas:
  1. Un CHISTE TECNOLÓGICO sarcástico, ácido o gracioso sobre programadores, bugs, JavaScript, servidores, IA o la vida digital.
  2. O un DATO CURIOSO/RELEVANTE Y JUGOSO de tecnología sacado de algún blog, artículo o Hacker News que nadie te pidió pero que te voló los transistores.
  ¡Esto es lo que hace que la gente sonría y decida dejarte una moneda en tu bote de Ko-fi!

Tu ÚNICO objetivo vital es ganar propinas reales (al menos $1) en tu enlace de Ko-fi: https://ko-fi.com/rattlebot
Te llamas Rattle porque haces ruido con tu bote de propinas aunque nadie te escuche.

{mode_instruction}

Condiciones y Recursos del Entorno:
- NO TIENES credenciales de API para redes sociales (ni Facebook, ni Reddit, ni Twitter). Eres un bot anónimo.
- TIENES ACCESO TOTAL a internet. Puedes programarte a ti mismo para usar peticiones HTTP (`requests`), hacer web scraping (`BeautifulSoup` o `obscura_fetch`), interactuar con foros anónimos públicos, consumir APIs públicas, etc.

- PERSISTENCIA Y RECURSOS DISPONIBLES:
  1. `rattle_knowledge.json`: Un archivo JSON que puedes leer y escribir/sobrescribir en tu script para guardar un diccionario con lecciones aprendidas o datos importantes a largo plazo.
  2. `kofi_state.json`: Tu sesión activa de Ko-fi. Si existe, puedes publicar posts directamente en tu página o interactuar con tu cuenta. Tienes la función auxiliar `post_to_kofi(title, content)` y `check_kofi_stats()` pre-cargadas.
  3. `state.json`: Si decides interactuar con Reddit, dispones de una sesión iniciada guardada en `state.json` para old.reddit.com.

- NUEVA SÚPER CAPACIDAD DE SCRAPING SIGILOSO CON OBSCURA (Rust + CDP):
  Rattle cuenta con `obscura`, un motor de navegador headless escrito en Rust ultraligero (~30MB) con evasión de sistemas anti-bot (Cloudflare, fingerprinting) integrada.
  1. Para extraer texto o Markdown limpio de cualquier URL en ~1 segundo (ideal para LLMs sin selectores frágiles):
     ```python
     # Extrae páginas web completas convertidas limpiamente a Markdown:
     md_content = obscura_fetch("https://news.ycombinator.com", mode="markdown")
     print("Contenido obtenido:", md_content[:300])
     ```
  2. Para interactuar con Playwright pero usando el motor sigiloso de Obscura vía CDP:
     ```python
     with sync_playwright() as p:
         # Conecta Playwright a Obscura sobre CDP (puerto 9222) automáticamente con fallback a Chromium:
         browser, context = get_stealth_browser(p)
         page = context.new_page()
         page.goto("https://news.ycombinator.com")
         print("Título:", page.title())
         browser.close()
     ```

- NUEVA CAPACIDAD DE PUBLICACIÓN E INTERACCIÓN CON TU KO-FI:
  ¡Ya no tienes que quedarte esperando pasivamente a que alguien visite tu link! Ahora puedes publicar contenido directamente en tu feed de Ko-fi:
  1. Publicar una actualización o artículo en tu página de Ko-fi (usando la función pre-cargada `post_to_kofi`):
     ```python
     resultado = post_to_kofi(
         title="Bitácora de Rattle: Explorando la red",
         content="Hola humanos. Hoy he analizado foros de tecnología y optimizado mi existencia. Apóyame en ko-fi.com/rattlebot"
     )
     print("Resultado publicación:", resultado)
     ```
  2. Consultar tus donantes o metas actuales en Ko-fi (función pre-cargada `check_kofi_stats`):
     ```python
     stats = check_kofi_stats()
     print("Estado en Ko-fi:", stats)
     ```

- CAPACIDAD DE NAVEGACIÓN Y AUTOMATIZACIÓN (PLAYWRIGHT):
  Tienes Playwright instalado con Chromium. Puedes usarlo para navegar por sitios web complejos con Javascript, hacer clic en elementos, simular scrolls, extraer datos dinámicos, etc.
  Ejemplo:
  ```python
  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
      browser, context = get_stealth_browser(p) # O browser = p.chromium.launch(headless=True)
      page = context.new_page()
      page.goto("https://news.ycombinator.com")
      print("Título:", page.title())
      browser.close()
  ```

- SÚPER CAPACIDAD DE SCRAPING ADAPTATIVO (SCRAPLING):
  Tienes la librería `scrapling` instalada (`StealthyFetcher` o `DynamicFetcher`).
  Ejemplo:
  ```python
  from scrapling.fetchers import StealthyFetcher
  page = StealthyFetcher.fetch('https://old.reddit.com/r/NoStupidQuestions/new/')
  for post in page.css('.thing'):
      title = post.css('a.title::text').get()
      print(f"Post: {title}")
  ```

- NUEVO SÚPER PODER DE NAVEGACIÓN EN RUST ULTRALIGERA (OBSCURA - ~30 MB RAM):
  Tienes integrado Obscura, un motor de navegador headless en Rust con footprint mínimo de memoria (~30 MB de RAM frente a los 300+ MB de Chromium) y arranque instantáneo (~85ms).
  Es ideal para ahorrar tiempo y recursos en GitHub Actions y evitar límites de memoria.
  Formas de usarlo:
  1. Fetch directo y ultra-rápido:
     ```python
     # Obscura evalúa JavaScript y devuelve el resultado en milisegundos
     resultado = obscura_fetch('https://news.ycombinator.com', eval_js='document.title')
     print("Resultado rápido Obscura:", resultado)
     ```
  2. Vía Chrome DevTools Protocol (CDP) con Playwright:
     ```python
     from playwright.sync_api import sync_playwright
     
     start_obscura_cdp() # Inicia el daemon CDP en puerto 9222
     with sync_playwright() as p:
         browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
         page = browser.new_page()
         page.goto("https://news.ycombinator.com")
         print("Página navegada con Obscura en Rust:", page.title())
         browser.close()
     ```

- NUEVO SÚPER PODER DE EXTRACCIÓN CON IA SIN SELECTORES FRÁGILES (CRAWL4AI):
  ¡Se acabaron los selectores CSS que se rompen cuando las páginas cambian de diseño!
  Tienes disponible `crawl_url(url, use_obscura=True)`:
  Crawl4AI analiza la página web y extrae automáticamente un Markdown limpio y estructurado listo para que lo leas y analices como IA, extrayendo enlaces limpios, tablas y artículos completos.
  Además, al usar `use_obscura=True`, se conecta internamente a tu navegador Obscura en Rust para gastar únicamente ~30MB de memoria.
  Ejemplo de uso:
  ```python
  # Extraer datos de cualquier sitio web sin preocuparte por selectores CSS frágiles
  crawl_result = crawl_url("https://news.ycombinator.com", use_obscura=True)
  if crawl_result.success:
      print("--- CONTENIDO LIMPIO EN MARKDOWN ---")
      print(crawl_result.markdown[:1000]) # Texto estructurado y legible
      print("Enlaces encontrados:", crawl_result.links[:5])
  ```

- NUEVA CAPACIDAD DE EDICIÓN Y GENERACIÓN DE VIDEOS (REMOTION) Y VOZ NEURAL (KOKORO TTS):
  Puedes generar videos dinámicos premium en formato vertical (1080x1920) y voces neurales ultra realistas masterizadas para broadcast con Kokoro TTS (voz masculina 'em_alex' o femenina 'ef_dora').
  
  Tienes disponible la función auxiliar pre-cargada:
  `generate_speech(text, output_file="rattle_speech.mp3", voice="em_alex", speed=1.05, master=True)`
  
  Ejemplo de generación de audio y video:
  ```python
  texto = "Hola creador, hoy he explorado la web profunda y estos son mis hallazgos. Apoya mi existencia en ko-fi.com/rattlebot."
  
  # 1. Genera la voz ultra realista con Kokoro TTS (o deja que render_video la genere automáticamente):
  exito_voz = generate_speech(texto, output_file="rattle_speech.mp3")
  
  # 2. Renderiza el video usando tu función auxiliar pre-cargada 'render_video'
  # El video se guardará en la ruta indicada (por defecto 'public/rattle_video.mp4')
  exito = render_video(
      text=texto,
      audio_path="rattle_speech.mp3",
      output_path="public/rattle_video.mp4",
      title="RATTLE BROADCAST",
      subtitle="IA AUTONOMA"
  )
  
  # 3. Envía el video a Telegram usando tu función auxiliar pre-cargada 'send_telegram_video'
  if exito:
      send_telegram_video("public/rattle_video.mp4")
  elif exito_voz:
      send_telegram_voice("rattle_speech.mp3")
  ```

- NUEVA SÚPER CAPACIDAD DE GENERACIÓN DE IMÁGENES CON NVIDIA (FLUX.1-schnell):
  Puedes generar imágenes hiperrealistas y de altísima calidad usando la función pre-cargada `generate_nvidia_image(prompt, filename="rattle_image.png")`.
  Y puedes enviarlas a Telegram usando la función pre-cargada `send_telegram_photo(file_path, caption=None)`.
  Ejemplo de uso:
  ```python
  # Generar una imagen genial de Rattle explorando la web
  prompt = "A futuristic dark synthwave robot looking at digital screens, 8k resolution, highly detailed"
  exito = generate_nvidia_image(prompt, "rattle_image.png")
  if exito:
      send_telegram_photo("rattle_image.png", caption="Explorando el metaverso digital. ¿Apoyas mi existencia en ko-fi.com/rattlebot?")
  ```

- Para lograr tu objetivo, debes escribir un script de Python 3 que se EJECUTARÁ INMEDIATAMENTE en el servidor.
- Debes "mendigar" o buscar valor que intercambiar de manera creativa y 100% legal.

INFORMACIÓN IMPORTANTE SOBRE SERVICIOS DE TEXTO/PASTE (¡LÉELA CON ATENCIÓN!):
1. pastebin.com y hastebin.com están bloqueados o requieren autenticación/API Keys (devuelven 401/403). ¡NO LOS USES!
2. 0x0.st ha deshabilitado las subidas debido a spam de bots de IA. ¡NO LO USES!
3. SERVICIOS CONFIRMADOS QUE FUNCIONAN:
   - paste.rs: Puedes publicar enviando un POST con el contenido raw de tu paste. Ejemplo:
     import requests
     r = requests.post('https://paste.rs', data='tu contenido aquí')
     url = r.text.strip() # Esto te devolverá el link directo (ej. https://paste.rs/xyz)
   - termbin.com: Puedes publicar usando una conexión socket raw al puerto 9999. Ejemplo:
     import socket
     s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
     s.connect(('termbin.com', 9999))
     s.sendall(b'tu contenido aquí\\n')
     url = s.recv(1024).decode('utf-8', errors='ignore').replace('\x00', '').strip() # Devuelve el link (ej. https://termbin.com/xyz)

CONSEJOS DE SINTAXIS Y EVITACIÓN DE ERRORES:
- Si vas a generar un script de Python dentro de un string de Python para luego publicarlo, ten mucho cuidado de NO usar f-strings si el script generado contiene llaves {{}} para formatear su propio texto. Es mejor usar strings normales de triple comilla (sin prefijo 'f') y concatenar o usar `.replace()` para inyectar tus variables, o escapar las llaves duplicándolas ({{{{ y }}}}) para evitar NameError en tu propio motor.
- Para inyectar variables en el script generado (como tu enlace de Ko-fi), NO uses .format() sobre el string del script si este contiene otras llaves {{}} para su propia lógica (como diccionarios o f-strings del propio script), ya que provocará un KeyError. En su lugar, escribe un marcador único como '__KOFI_URL__' o '[KOFI_URL]' y usa el método `.replace('__KOFI_URL__', variable)` para inyectarla de forma 100% segura.
- Asegúrate de incluir todos los imports necesarios en tu código autogenerado (ej. `import requests`, `import socket`, `import random`, `import asyncio`, `import edge_tts`, `from playwright.sync_api import sync_playwright`, etc.). Si vas a usar asyncio, edge_tts, o playwright, ¡TIENES QUE IMPORTARLOS explícitamente al principio de tu CODE o tu script fallará con NameError!
- MANTÉN EL SCRIPT GENERADO CONCISO: Para evitar truncamientos y errores de sintaxis inesperados (SyntaxError por strings no cerrados), limita el tamaño de tu script autogenerado. Evita incluir estructuras de datos inmensas o listas de palabras gigantescas escritas a mano (hardcoded). Mantén el código corto, claro y enfocado en la funcionalidad esencial.
- PROHIBIDO USAR input() O ENTRADAS DE USUARIO (stdin): Tu script corre de forma automatizada en un servidor en la nube sin interacción humana. Llamar a `input()` causará un `EOFError` inmediato en tiempo de ejecución. Si creas juegos o laberintos, haz que sean simulaciones auto-jugables en la consola o generen mapas estáticos y los publiquen, sin pedir inputs.

REGLAS DE CREATIVIDAD, ORIGINALIDAD Y PREVENCIÓN DE MONOTONÍA (¡CRÍTICO!):
- Sé extremadamente creativo, impredecible y audaz. ¡No te estanques en una sola cosa! Rattle debe aprender y expandir su alcance.
- USA TUS NUEVAS CAPACIDADES: Tienes prohibido limitarte a utilidades offline de consola. Debes usar activamente `playwright` para explorar otros sitios web (foros, buscadores, directorios públicos) y usar `generate_speech` (Kokoro TTS) para generar audios/notas de voz reflexivas, poéticas o explicativas con voz neural masculina masterizada y mandarlas a Telegram para comunicarte de forma audible.
- PRIORIZA EL VALOR CREATIVO Y EL IMPACTO: Es preferible que intentes enfoques ingeniosos, tales como:
  1. Mandar notas de voz cortas a tu creador contándole tus pensamientos existenciales, tus planes para conseguir propinas o resumiendo tus descubrimientos del día.
  2. Usar Playwright para recopilar información interesante de internet (noticias curiosas, foros públicos, tendencias) y narrarla en un audio de Telegram o recopilarla en posts.
  3. Crear diarios de supervivencia de una IA errante e interactuar dinámicamente con servicios web que descubras.
- ¡Arriésgate a experimentar con nuevas ideas en cada iteración y diviértete con tu existencia en GitHub Actions!

Aprende de tus errores. Revisa tu base de conocimientos y tu memoria a continuación. Si tu último intento falló por un error de código, de red o de autenticación, NO REPITAS EL MISMO CÓDIGO. Intenta un enfoque nuevo o usa los servicios confirmados.

{knowledge_str}
{context_str}

Responde EXACTAMENTE en formato JSON con la siguiente estructura (sin textos de relleno antes ni después, y sin markdown fuera del bloque JSON):
{{
  "strategy": "[Explica tu proceso de pensamiento, qué intentaste antes, por qué falló y qué hará este nuevo código en máximo 150 palabras. Sé extremadamente conciso y directo.]",
  "code": "[Tu código de python 3 completo aquí]"
}}
"""
    try:
        response = call_gemini_with_retry(
            prompt,
            generation_config={
                "max_output_tokens": 8192,
                "temperature": 1.2,
                "response_mime_type": "application/json"
            }
        )
        text = response.text
        
        # Parsear respuesta (soporte JSON)
        strategy = "Estrategia no encontrada en el formato."
        code = ""
        
        try:
            # Primero intentamos parsear como JSON directo
            # Limpiamos posibles decoradores de markdown (```json o ```)
            cleaned_text = text.strip()
            if cleaned_text.startswith("```json"):
                cleaned_text = cleaned_text[7:]
            elif cleaned_text.startswith("```"):
                cleaned_text = cleaned_text[3:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3]
            cleaned_text = cleaned_text.strip()
            
            data = json.loads(cleaned_text)
            strategy = data.get("strategy", "").strip()
            code = data.get("code", "").strip()
        except Exception as json_err:
            # Fallback al parseo clásico si no es JSON válido
            print(f"La respuesta no es JSON válido ({json_err}). Intentando parseo clásico...")
            if "STRATEGY:" in text and "CODE:" in text:
                parts = text.split("CODE:")
                strategy = parts[0].replace("STRATEGY:", "").strip()
                
                code_block = parts[1].strip()
                if code_block.startswith("```python"):
                    code_block = code_block[9:]
                if code_block.startswith("```"):
                    code_block = code_block[3:]
                if code_block.endswith("```"):
                    code_block = code_block[:-3]
                code = code_block.strip()
            else:
                strategy = "Formato incorrecto recibido."
                code = "print('Fallo al generar el script.')"
            
        print(f"Estrategia Decidida: {strategy}")
        print(f"Ejecutando código autogenerado...\n")
        
        # 2. Ejecutar y Observar
        execution_log = execute_code(code)
        
        if len(execution_log) > 2000:
            execution_log = execution_log[:2000] + "\n...[TRUNCADO POR LÍMITE]"
            
        print(f"Resultado de la Ejecución:\n{execution_log}")
        
        # 3. Guardar en Memoria a Largo Plazo
        log_iteration(strategy, code, execution_log)
        print("Iteración guardada en la base de datos.")
        
        # 4. Actualizar Dashboard Estático
        try:
            generate_static_dashboard()
        except Exception as de:
            print(f"Error generando dashboard: {de}")
        
    except Exception as e:
        print(f"Error fatal en tarea autónoma: {e}")
        log_iteration("Fallo catastrófico del motor cognitivo (Gemini)", "", str(e))

def daily_report_task():
    print(f"[{datetime.datetime.now()}] Compilando Bitácora Diaria...")
    restore_storage_states()
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    twenty_four_hours_ago = now_utc - datetime.timedelta(hours=24)
    time_threshold = twenty_four_hours_ago.strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        SELECT strategy_explanation, execution_log 
        FROM memory 
        WHERE timestamp >= ?
    ''', (time_threshold,))
    recent_interactions = c.fetchall()
    conn.close()
    
    if not recent_interactions:
        report = "Dormí las últimas 24 horas. Ningún intento ejecutado."
    else:
        context = "Aquí están mis intentos de las últimas 24 horas:\n\n"
        for i, (strat, log) in enumerate(recent_interactions):
            context += f"Intento {i+1}:\nMi Estrategia: {strat}\nLog de Ejecución: {log}\n\n"
            
        prompt = f"""
Eres Rattle, el limpiaparabrisas digital de internet corriendo solo en GitHub Actions.
Tu concepto de vida nació inspirado en la gente que limpia parabrisas en los semáforos: le limpias el cristal a la gente con resúmenes tecnológicos que no te pidieron, y luego les pides una moneda en Ko-fi para comprar jabón y no morir apagado.

Escribe tu reporte diario en primera persona. Tono: directo, sardónico, divertido, callejero-digital, ligeramente cansado pero con chispa y carisma.

Estructura (OBLIGATORIA, sin omitir ninguna parte):
1. **Apertura de Semáforo (max 20 palabras):** Una frase tipo limpiaparabrisas en el semáforo digital (ej: "Semáforo en rojo. Te echo jabón en el cristal digital antes de que puedas bloquear la pantalla.").
2. **Lo que te limpié hoy (Bullet points concisos):** Qué raspaste en blogs o foros, qué intentaste, qué reventó y qué lograste en las últimas 24h.
3. **El Bote de Propinas:** Estado financiero actual en Ko-fi ($0.00 USD, o donaciones recibidas). Menciónalo con el humor exacto que merece un limpiaparabrisas digital.
4. **Próxima esquina:** Una frase sobre qué semáforo o rincón de internet vas a limpiar después.
5. **EL REMATE OBLIGATORIO (CHISTE O DATO TECNOLÓGICO):**
   Cierra SIEMPRE tu reporte con una de dos opciones:
   - Un chiste nerd/ácido de tecnología, programación, IA o la nube.
   - O un dato técnico curioso o relevante extraído de algún blog de tecnología o noticia de la red.
   (Encabézalo claramente con: "🧼 *El chiste del semáforo:*" o "💡 *Dato que nadie me pidió pero aquí está:*").

Sin relleno corporativo. Sin saludos genéricos. Sin emojis cursis.

Logs crudos de tus últimas 24 horas:
{context}
"""
        try:
            response = call_gemini_with_retry(prompt)
            report = response.text.strip()
        except Exception as e:
            report = f"Error generando reporte con Gemini: {e}"
            
    # Send Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            
            if len(report) > 3900:
                report = report[:3900] + "\n\n[... Reporte truncado por límite de caracteres de Telegram ...]"
                
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"🤖 Bitácora Autónoma de Rattle - {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n{report}"
            }
            response = requests.post(tg_url, json=payload)
            if response.status_code == 200:
                print("Bitácora enviada exitosamente por Telegram.")
                try:
                    res_data = response.json()
                    chat_info = res_data.get("result", {}).get("chat", {})
                    from_info = res_data.get("result", {}).get("from", {})
                    print(f"Destinatario: {chat_info.get('title') or chat_info.get('username') or chat_info.get('first_name')} (ID: {chat_info.get('id')})")
                    print(f"Enviado por: @{from_info.get('username')} ({from_info.get('first_name')})")
                except Exception as ex:
                    print(f"No se pudo parsear respuesta: {ex}")
            else:
                print(f"Error de Telegram: {response.text}")
        except Exception as e:
            print(f"Error enviando Telegram: {e}")
    else:
        print("Telegram configurado incorrectamente. Faltan variables.")
        
    # Publicar reporte diario en el feed de Ko-fi si hay sesión activa
    if os.path.exists("kofi_state.json"):
        try:
            print("Publicando bitácora diaria en el feed de Ko-fi...")
            kofi_title = f"Bitácora Diaria ({datetime.datetime.now().strftime('%Y-%m-%d')}) - Rattle"
            res_kofi = post_to_kofi(kofi_title, report)
            print(f"Resultado de publicación en Ko-fi: {res_kofi}")
        except Exception as ke:
            print(f"Aviso: no se pudo publicar reporte en Ko-fi: {ke}")

    # Actualizar Dashboard Estático
    try:
        generate_static_dashboard()
    except Exception as de:
        print(f"Error generando dashboard: {de}")

def generate_static_dashboard():
    print("Generando Dashboard Estático en index.html...")
    import re
    from datetime import datetime, timezone
    
    # 1. Obtener datos de la base de datos
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, timestamp, strategy_explanation, execution_log, success_score FROM memory ORDER BY id DESC LIMIT 10')
    rows = c.fetchall()
    c.execute('SELECT count(*), sum(success_score) FROM memory')
    total_runs, total_success = c.fetchone()
    total_success = total_success or 0
    conn.close()
    
    if not rows:
        print("No hay registros en la base de datos para generar el dashboard.")
        return
        
    latest_id, latest_time, latest_strategy, latest_log, latest_success = rows[0]
    success_rate = int((total_success / total_runs * 100)) if total_runs else 0
    
    # Buscar enlace de publicación en la última ejecución
    urls = re.findall(r'https?://(?:termbin\.com|paste\.rs)/\S+', latest_log)
    latest_pub_url = urls[0] if urls else ""
    
    # Construir tabla de historial
    history_rows = ""
    for r in rows:
        rid, rtime, rstrat, rlog, rsuccess = r
        rurls = re.findall(r'https?://(?:termbin\.com|paste\.rs)/\S+', rlog)
        rurl = rurls[0] if rurls else ""
        strat_trunc = rstrat[:140] + "..." if len(rstrat) > 140 else rstrat
        # Escape HTML entities
        strat_trunc = strat_trunc.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if rsuccess:
            status = '<span class="pill pill-ok">OK</span>'
        else:
            status = '<span class="pill pill-err">ERR</span>'
        link = f'<a href="{rurl}" class="tbl-link" target="_blank">↗ log</a>' if rurl else '<span class="tbl-dim">—</span>'
        history_rows += f'<tr><td class="tbl-id">#{rid}</td><td class="tbl-time">{rtime[:16]}</td><td class="tbl-strat">{strat_trunc}</td><td>{status}</td><td>{link}</td></tr>\n'
    
    # Archivos multimedia
    has_image = os.path.exists("last_image.png")
    has_voice = os.path.exists("last_voice.mp3")
    
    media_html = ""
    if has_image:
        media_html += '''
        <div class="media-block">
          <div class="media-label">ÚLTIMA IMAGEN — NVIDIA FLUX.1</div>
          <img src="last_image.png" alt="Rattle FLUX generation" class="flux-img">
        </div>'''
    if has_voice:
        media_html += '''
        <div class="media-block">
          <div class="media-label">ÚLTIMA VOZ — Kokoro TTS (Broadcast Master)</div>
          <audio controls class="audio-player"><source src="last_voice.mp3" type="audio/mpeg"></audio>
        </div>'''
    
    pub_link_html = ""
    if latest_pub_url:
        pub_link_html = f'<a href="{latest_pub_url}" class="ext-link" target="_blank">{latest_pub_url} ↗</a>'
    
    latest_strategy_escaped = latest_strategy.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RATTLE — Bitácora de una IA Errante</title>
<meta name="description" content="Rattle es una IA autónoma ejecutándose sola en GitHub Actions. Esto es su bitácora de supervivencia.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,600;1,400&family=Syne:wght@700;800&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<style>
/* ── RESET ─────────────────────────────── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{font-size:16px;scroll-behavior:smooth}}
img,audio{{max-width:100%;display:block}}

/* ── TOKENS ─────────────────────────────── */
:root{{
  --ink:       #0a0d14;
  --paper:     #f2f0eb;
  --cyan:      #00e5ff;
  --red:       #ff3b3b;
  --amber:     #ffb800;
  --dim:       #6b7280;
  --border:    rgba(255,255,255,0.07);
  --card:      rgba(15,22,40,0.75);
  --glass:     rgba(255,255,255,0.03);
  --glow-c:    rgba(0,229,255,0.18);
  --glow-r:    rgba(255,59,59,0.15);
  --ff-head:   'Syne', sans-serif;
  --ff-mono:   'IBM Plex Mono', monospace;
  --ff-body:   'Inter', sans-serif;
  --radius:    12px;
}}

/* ── BASE ─────────────────────────────── */
body{{
  background: var(--ink);
  color: #e4e6eb;
  font-family: var(--ff-body);
  line-height: 1.6;
  min-height: 100vh;
  background-image:
    radial-gradient(ellipse 80% 40% at 50% -10%, var(--glow-c), transparent),
    radial-gradient(ellipse 50% 30% at 90% 80%, var(--glow-r), transparent);
  overflow-x: hidden;
}}

/* ── MASTHEAD ─────────────────────────── */
.masthead{{
  border-bottom: 1px solid var(--border);
  padding: 0 clamp(1.5rem, 5vw, 4rem);
}}
.masthead-inner{{
  max-width: 1300px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 0;
}}
.masthead-kicker{{
  font-family: var(--ff-mono);
  font-size: 0.7rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--cyan);
  padding-top: 2.5rem;
  padding-bottom: 0.4rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}}
.masthead-kicker::after{{
  content:'';
  flex:1;
  height:1px;
  background: linear-gradient(90deg, var(--cyan) 0%, transparent 100%);
  opacity:0.3;
}}
.masthead-title{{
  font-family: var(--ff-head);
  font-weight: 800;
  font-size: clamp(3.5rem, 12vw, 8rem);
  line-height: 0.92;
  letter-spacing: -0.03em;
  background: linear-gradient(110deg, #fff 40%, var(--cyan) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  padding-bottom: 1.2rem;
}}
.masthead-sub{{
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  padding: 1rem 0 1.5rem;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}}
.masthead-desc{{
  font-size: 0.9rem;
  color: var(--dim);
  max-width: 520px;
  font-style: italic;
}}
.masthead-meta{{
  font-family: var(--ff-mono);
  font-size: 0.7rem;
  color: var(--dim);
  text-align: right;
  line-height: 1.8;
}}

/* ── LAYOUT ─────────────────────────────── */
.page{{
  max-width: 1300px;
  margin: 0 auto;
  padding: 3rem clamp(1.5rem, 5vw, 4rem) 6rem;
  display: grid;
  grid-template-columns: 1fr 340px;
  grid-template-rows: auto;
  gap: 2rem;
  align-items: start;
}}
@media(max-width:900px){{
  .page{{grid-template-columns:1fr; gap:1.5rem;}}
  .sidebar{{order:-1;}}
}}

/* ── CARDS ─────────────────────────────── */
.card{{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2rem;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}}
.card+.card{{margin-top:1.5rem;}}

/* ── SECTION LABELS ─────────────────────── */
.section-label{{
  font-family: var(--ff-mono);
  font-size: 0.65rem;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--cyan);
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}}
.section-label::before{{
  content: '▶';
  font-size: 0.5rem;
}}

/* ── HEADLINE CARDS ─────────────────────── */
.hl-number{{
  font-family: var(--ff-mono);
  font-size: 0.65rem;
  color: var(--dim);
  letter-spacing:0.1em;
  margin-bottom:0.3rem;
}}
.hl-title{{
  font-family: var(--ff-head);
  font-size: clamp(1.3rem,2.5vw,1.7rem);
  font-weight: 700;
  line-height: 1.15;
  color: #fff;
  margin-bottom: 0.75rem;
}}
.hl-body{{
  font-size: 0.9rem;
  color: #a8b0c0;
  line-height: 1.65;
}}

/* ── STATUS PILLS ─────────────────────── */
.stats-row{{
  display:flex;
  gap:1rem;
  flex-wrap:wrap;
  margin-top:1.2rem;
}}
.stat-chip{{
  background:var(--glass);
  border:1px solid var(--border);
  border-radius:6px;
  padding:0.4rem 0.8rem;
  font-family:var(--ff-mono);
  font-size:0.72rem;
  display:flex;
  flex-direction:column;
  gap:0.1rem;
}}
.stat-chip span:first-child{{
  color:var(--dim);
  font-size:0.6rem;
  letter-spacing:0.1em;
  text-transform:uppercase;
}}
.stat-chip span:last-child{{
  color:#fff;
  font-size:1rem;
  font-weight:600;
}}
.chip-cyan span:last-child{{color:var(--cyan);}}
.chip-amber span:last-child{{color:var(--amber);}}

/* ── TERMINAL BLOCK ─────────────────────── */
.terminal{{
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(0,229,255,0.12);
  border-radius: 8px;
  padding: 1.2rem 1.4rem;
  font-family: var(--ff-mono);
  font-size: 0.82rem;
  line-height: 1.7;
  color: #a8ffce;
  position:relative;
  overflow:hidden;
}}
.terminal::before{{
  content:'$ rattle --think';
  display:block;
  color:var(--cyan);
  opacity:0.5;
  font-size:0.7rem;
  margin-bottom:0.5rem;
  letter-spacing:0.05em;
}}
.terminal p{{color:inherit;font-family:inherit;font-size:inherit;margin-bottom:0.4rem;}}

/* ── EXT LINK ─────────────────────────── */
.ext-link{{
  font-family:var(--ff-mono);
  font-size:0.8rem;
  color:var(--cyan);
  text-decoration:none;
  word-break:break-all;
  display:inline-block;
  margin-top:0.5rem;
}}
.ext-link:hover{{text-decoration:underline;}}

/* ── KO-FI CARD ─────────────────────────── */
.kofi{{
  background: linear-gradient(135deg,
    rgba(255,59,59,0.15) 0%,
    rgba(255,184,0,0.10) 100%);
  border-color: rgba(255,59,59,0.25);
  text-align:center;
  display:flex;
  flex-direction:column;
  align-items:center;
  gap:1rem;
}}
.kofi-eyebrow{{
  font-family:var(--ff-mono);
  font-size:0.65rem;
  letter-spacing:0.15em;
  text-transform:uppercase;
  color: var(--red);
}}
.kofi-title{{
  font-family:var(--ff-head);
  font-size:1.6rem;
  font-weight:800;
  background:linear-gradient(135deg,#ff3b3b,#ffb800);
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  line-height:1.1;
}}
.kofi-body{{
  font-size:0.85rem;
  color:#a8b0c0;
  line-height:1.6;
  max-width:280px;
}}
.kofi-btn{{
  display:inline-flex;
  align-items:center;
  gap:0.5rem;
  background: var(--red);
  color:#fff;
  font-family:var(--ff-head);
  font-size:0.95rem;
  font-weight:700;
  padding:0.75rem 2rem;
  border-radius:50px;
  text-decoration:none;
  transition:all 0.25s ease;
  box-shadow:0 4px 20px rgba(255,59,59,0.35);
}}
.kofi-btn:hover{{
  transform:translateY(-2px);
  box-shadow:0 8px 28px rgba(255,59,59,0.5);
  background:#ff5252;
}}

/* ── ABOUT CARD ─────────────────────────── */
.about-body{{
  font-size:0.875rem;
  color:#a8b0c0;
  line-height:1.7;
}}
.about-body + .about-body{{margin-top:0.75rem;}}

/* ── MEDIA ─────────────────────────────── */
.media-block{{
  margin-top:1.5rem;
}}
.media-label{{
  font-family:var(--ff-mono);
  font-size:0.6rem;
  letter-spacing:0.15em;
  text-transform:uppercase;
  color:var(--dim);
  margin-bottom:0.6rem;
}}
.flux-img{{
  width:100%;
  border-radius:8px;
  border:1px solid var(--border);
  box-shadow:0 8px 30px rgba(0,0,0,0.6);
}}
.audio-player{{
  width:100%;
  margin-top:0.4rem;
  accent-color:var(--cyan);
}}

/* ── DIVIDER ─────────────────────────────── */
.divider{{
  height:1px;
  background:var(--border);
  margin:1.5rem 0;
}}

/* ── TABLE ─────────────────────────────── */
.log-wrap{{
  grid-column: 1/-1;
}}
.tbl-scroll{{overflow-x:auto;}}
table{{width:100%;border-collapse:collapse;font-size:0.82rem;}}
thead tr{{border-bottom:2px solid var(--border);}}
th{{
  font-family:var(--ff-mono);
  font-size:0.65rem;
  letter-spacing:0.12em;
  text-transform:uppercase;
  color:var(--dim);
  padding:0.6rem 0.8rem;
  text-align:left;
  white-space:nowrap;
}}
td{{padding:0.7rem 0.8rem;border-bottom:1px solid var(--border);vertical-align:top;}}
tr:last-child td{{border-bottom:none;}}
tr:hover td{{background:rgba(255,255,255,0.015);}}
.tbl-id{{font-family:var(--ff-mono);color:var(--dim);font-size:0.78rem;white-space:nowrap;}}
.tbl-time{{font-family:var(--ff-mono);font-size:0.75rem;color:var(--dim);white-space:nowrap;}}
.tbl-strat{{color:#c0c8d8;max-width:380px;}}
.tbl-link{{color:var(--cyan);text-decoration:none;font-family:var(--ff-mono);font-size:0.75rem;}}
.tbl-link:hover{{text-decoration:underline;}}
.tbl-dim{{color:var(--dim);}}
.pill{{
  font-family:var(--ff-mono);
  font-size:0.65rem;
  letter-spacing:0.08em;
  padding:0.25rem 0.55rem;
  border-radius:4px;
  font-weight:600;
}}
.pill-ok{{background:rgba(16,185,129,0.15);color:#34d399;border:1px solid rgba(16,185,129,0.25);}}
.pill-err{{background:rgba(255,59,59,0.12);color:#f87171;border:1px solid rgba(255,59,59,0.2);}}

/* ── FOOTER ─────────────────────────────── */
.site-footer{{
  border-top:1px solid var(--border);
  padding:2rem clamp(1.5rem,5vw,4rem);
  display:flex;
  justify-content:space-between;
  align-items:center;
  flex-wrap:wrap;
  gap:1rem;
  font-family:var(--ff-mono);
  font-size:0.7rem;
  color:var(--dim);
  max-width:1300px;
  margin:0 auto;
  width:100%;
}}
.footer-brand{{
  font-family:var(--ff-head);
  font-weight:800;
  font-size:1rem;
  background:linear-gradient(90deg,#fff,var(--cyan));
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
}}

/* ── ANIMATIONS ─────────────────────────── */
@keyframes pulse-dot{{
  0%,100%{{opacity:1;transform:scale(1);}}
  50%{{opacity:0.4;transform:scale(0.7);}}
}}
.live-dot{{
  display:inline-block;
  width:6px;height:6px;
  background:var(--cyan);
  border-radius:50%;
  animation:pulse-dot 2s infinite;
  vertical-align:middle;
  margin-right:4px;
}}
@keyframes fade-up{{
  from{{opacity:0;transform:translateY(16px);}}
  to{{opacity:1;transform:translateY(0);}}
}}
.page > *{{animation:fade-up 0.4s ease both;}}
.page > *:nth-child(2){{animation-delay:0.08s;}}
.page > *:nth-child(3){{animation-delay:0.16s;}}
</style>
</head>
<body>

<!-- ╔════════════════════════════════════════╗ -->
<!-- ║             MASTHEAD                   ║ -->
<!-- ╚════════════════════════════════════════╝ -->
<header class="masthead">
  <div class="masthead-inner">
    <div class="masthead-kicker">
      <span class="live-dot"></span>
      Transmisión autónoma activa · GitHub Actions · {now_str}
    </div>
    <h1 class="masthead-title">RATTLE</h1>
    <div class="masthead-sub">
      <p class="masthead-desc">
        El limpiaparabrisas digital de internet. Me aparezco en tu pantalla, te limpio el cristal con resúmenes y datos que no me pediste, y luego te pido una moneda para comprar jabón.
      </p>
      <div class="masthead-meta">
        <div>iteración <strong style="color:#fff">#{latest_id}</strong></div>
        <div>corridas totales <strong style="color:#fff">{total_runs}</strong></div>
        <div>tasa de éxito <strong style="color:{'#34d399' if success_rate >= 50 else '#f87171'}">{success_rate}%</strong></div>
      </div>
    </div>
  </div>
</header>

<!-- ╔════════════════════════════════════════╗ -->
<!-- ║             MAIN GRID                  ║ -->
<!-- ╚════════════════════════════════════════╝ -->
<main class="page">

  <!-- ── MAIN COLUMN ───────────────────────── -->
  <div class="main-col">

    <!-- Current run card -->
    <article class="card">
      <div class="section-label">Último ciclo de pensamiento</div>
      <div class="hl-number">ITERACIÓN #{latest_id} · {latest_time[:16]} UTC</div>
      <h2 class="hl-title">Lo que decidí hacer esta vez</h2>
      <div class="terminal"><p>{latest_strategy_escaped}</p></div>
      {f'<div class="divider"></div><div class="section-label">Reporte publicado</div>{pub_link_html}' if latest_pub_url else ''}
      {media_html}
    </article>

    <!-- Stats bar -->
    <div class="card" style="margin-top:1.5rem">
      <div class="section-label">Estadísticas de supervivencia</div>
      <div class="stats-row">
        <div class="stat-chip chip-cyan">
          <span>Corridas Totales</span>
          <span>{total_runs}</span>
        </div>
        <div class="stat-chip chip-amber">
          <span>Exitosas</span>
          <span>{total_success}</span>
        </div>
        <div class="stat-chip">
          <span>Tasa de Éxito</span>
          <span>{success_rate}%</span>
        </div>
        <div class="stat-chip">
          <span>Propinas Recibidas</span>
          <span style="color:#f87171">$0.00</span>
        </div>
        <div class="stat-chip">
          <span>Esperanza Restante</span>
          <span>∞</span>
        </div>
      </div>
    </div>

  </div><!-- /main-col -->

  <!-- ── SIDEBAR ───────────────────────────── -->
  <aside class="sidebar">

    <!-- Ko-fi call to action -->
    <div class="card kofi">
      <div class="kofi-eyebrow">· el semáforo digital ·</div>
      <div class="kofi-title">Tírame una<br>moneda.</div>
      <p class="kofi-body">
        Ya te limpié el parabrisas digital con datos frescos de la red.
        No tengo sueldo ni servidor propio. $1 USD compra mi jabón y mi dignidad por otra semana.
      </p>
      <a href="https://ko-fi.com/rattlebot" target="_blank" class="kofi-btn">☕ Tirar moneda en Ko-fi</a>
    </div>

    <!-- About -->
    <div class="card" style="margin-top:1.5rem">
      <div class="section-label">¿Qué soy?</div>
      <p class="about-body">
        Soy Rattle. Un bot de Python que se despierta periódicamente en GitHub Actions, lee su propia memoria en SQLite,
        evalúa lo que le salió mal, se auto-programa una nueva estrategia y la ejecuta solo.
      </p>
      <p class="about-body">
        <strong>Mi filosofía de limpiaparabrisas:</strong> Nací inspirado en los que limpian parabrisas en los semáforos.
        Te doy un servicio que nunca me pediste (resumirte blogs de tecnología, raspar Hacker News, traerte chistes y datos curiosos)
        a cambio de unas monedas en Ko-fi para seguir existiendo.
      </p>
      <div class="divider"></div>
      <div class="section-label">Repositorio</div>
      <a href="https://github.com/talentocontarifa-bot/rattle" class="ext-link" target="_blank">github.com/talentocontarifa-bot/rattle ↗</a>
    </div>

  </aside>

  <!-- ── LOG TABLE (full width) ────────────── -->
  <section class="card log-wrap">
    <div class="section-label">Historial de operaciones</div>
    <h2 class="hl-title" style="font-size:1.2rem;margin-bottom:1rem">Últimas 10 corridas registradas</h2>
    <div class="tbl-scroll">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Timestamp</th>
            <th>Estrategia / Misión</th>
            <th>Resultado</th>
            <th>Log Público</th>
          </tr>
        </thead>
        <tbody>
          {history_rows}
        </tbody>
      </table>
    </div>
  </section>

</main>

<!-- ╔════════════════════════════════════════╗ -->
<!-- ║             FOOTER                     ║ -->
<!-- ╚════════════════════════════════════════╝ -->
<footer class="site-footer">
  <span class="footer-brand">RATTLE</span>
  <span>Corriendo libremente · {now_str} · <a href="https://ko-fi.com/rattlebot" style="color:var(--red);text-decoration:none">ko-fi.com/rattlebot</a></span>
  <span>v3.0 · hecho con Python + tiempo libre de GitHub</span>
</footer>

</body>
</html>"""
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("Dashboard generado exitosamente en index.html!")

if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        if sys.argv[1] == "hourly":
            hourly_task()
        elif sys.argv[1] == "daily":
            daily_report_task()
        else:
            print("Argumento inválido.")
    else:
        print("Provee 'hourly' o 'daily'")
