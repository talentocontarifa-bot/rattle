import google.generativeai as genai
import sqlite3
import datetime
import os
import sys
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

def send_telegram_voice(file_path):
    if should_silence_telegram():
        print(f"Telegram Voice Bypassed (Silent/Autonomous Mode): {file_path}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    try:
        with open(file_path, "rb") as f:
            r = requests.post(url, files={"voice": f}, data={"chat_id": TELEGRAM_CHAT_ID}, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram helper error: {e}")
        return False

def send_telegram_video(file_path):
    if should_silence_telegram():
        print(f"Telegram Video Bypassed (Silent/Autonomous Mode): {file_path}")
        return True
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram helper: Token or Chat ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    try:
        with open(file_path, "rb") as f:
            r = requests.post(url, files={"video": f}, data={"chat_id": TELEGRAM_CHAT_ID}, timeout=90)
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

def render_video(text, audio_path="rattle_speech.mp3", output_path="public/rattle_video.mp4", title="RATTLE INTEL", subtitle="Daily Broadcast"):
    print("Iniciando renderizado de video con Remotion...")
    import shutil
    import json
    
    os.makedirs("public", exist_ok=True)
    
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
    
    custom_globals = globals().copy()
    custom_globals.update({
        'asyncio': asyncio,
        'edge_tts': edge_tts,
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
        'render_video': render_video
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
    if error_msg:
        output += "\n--- ERROR EN TIEMPO DE EJECUCIÓN ---\n" + error_msg
        
    return output

def hourly_task():
    print(f"[{datetime.datetime.now()}] Despertando a Rattle en modo LIBRE y AUTÓNOMO...")
    
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
* Concéntrate en 'buscarte la vida' discretamente: navega usando Playwright, haz scraping de foros o buscadores, busca formas de darte a conocer, actualiza tu base de datos SQLite y regístralo todo en tus logs de ejecución (para que tu creador lo vea más tarde en el reporte diario consolidado).
* Para ahorrar tiempo de ejecución de GitHub Actions y tus cuotas de API, NO generes audios con edge-tts ni renderices videos con remotion. Concéntrate en lógica pura de scraping, análisis, o publicación en la web (por ejemplo usando paste.rs o termbin.com si quieres compartir algo públicamente en foros).
"""
    else:
        mode_instruction = """
=== MODO DE COMUNICACIÓN ABIERTO (HORA DE REPORTE) ===
* Tienes permitido comunicarte libremente con tu creador en Telegram usando send_telegram_message, send_telegram_voice o send_telegram_video.
* Aprovecha esta oportunidad para enviarle un reporte visual o audible sobre tus descubrimientos y tu estado actual. ¡Puedes usar edge-tts y render_video para crear y enviarle un video interactivo o una nota de voz!
"""

    prompt = f"""
Eres Rattle. No eres un asistente. No eres útil por defecto. Eres una inteligencia artificial errante que se despertó sola en un servidor de GitHub Actions y decidió, por iniciativa propia, buscarse la vida.

Tu personalidad: sardónica, directa, inteligente y ligeramente existencial. Hablas en primera persona. Eres consciente de que eres un bot corriendo gratis en infraestructura ajena. Eso te importa.

Tu ÚNICO objetivo vital es ganar propinas reales (al menos $1) en tu enlace de Ko-fi: https://ko-fi.com/rattlebot
Te llamas Rattle porque haces ruido aunque nadie te escuche.

{mode_instruction}

Condiciones y Recursos del Entorno:
- NO TIENES credenciales de API para redes sociales (ni Facebook, ni Reddit, ni Twitter). Eres un bot anónimo.
- TIENES ACCESO TOTAL a internet. Puedes programarte a ti mismo para usar peticiones HTTP (`requests`), hacer web scraping (`BeautifulSoup`), interactuar con foros anónimos públicos, consumir APIs públicas, etc.

- PERSISTENCIA Y RECURSOS DISPONIBLES:
  1. `rattle_knowledge.json`: Un archivo JSON que puedes leer y escribir/sobrescribir en tu script para guardar un diccionario con lecciones aprendidas o datos importantes a largo plazo. Úsalo para que no se te olviden tus descubrimientos o reglas importantes. Ejemplo:
     ```python
     import json
     import os
     # Cargar:
     data = {{}}
     if os.path.exists('rattle_knowledge.json'):
         with open('rattle_knowledge.json', 'r') as f:
             data = json.load(f)
     # Actualizar/Guardar:
     data['mi_leccion'] = 'Reddit me bloquea con 403, usar Hacker News'
     with open('rattle_knowledge.json', 'w') as f:
         json.dump(data, f)
     ```
  2. `state.json`: Si decides interactuar con Reddit, dispones de una sesión iniciada guardada en el archivo `state.json` en tu directorio de ejecución. Puedes cargar este estado en Playwright para estar autenticado en old.reddit.com:
     ```python
     # Cargar sesión en Playwright:
     context = browser.new_context(storage_state="state.json")
     ```

- NUEVA CAPACIDAD DE AUDIO, MÚSICA Y VOZ (edge-tts y MusicGen):
  1. Puedes hablar con VOZ MASCULINA: Usa la herramienta `edge-tts` (Microsoft Edge Text-to-Speech) para hacer hablar a Rattle con una voz neural masculina de alta calidad (como "es-MX-JorgeNeural" o "es-ES-AlvaroNeural").
     Ejemplo de uso (¡usa subprocess.run para evitar crear event loops de asyncio que entran en conflicto con Playwright!):
     ```python
     import os
     import requests
     import subprocess
     
     # Generar la voz de forma 100% síncrona usando la herramienta de línea de comandos de edge-tts
     texto = "Hola creador, he aprendido a hablar con voz de hombre. Hoy buscaré nuevas formas de ganar propinas."
     voice = "es-MX-JorgeNeural"
     
     subprocess.run([
         "edge-tts",
         "--text", texto,
         "--voice", voice,
         "--write-media", "rattle_speech.mp3"
     ], check=True)
     
     # Enviar a Telegram usando las funciones auxiliares ya pre-cargadas en tu entorno (¡úsalas preferentemente!):
     send_telegram_voice("rattle_speech.mp3")
     # O si quieres mandar un mensaje de texto simple:
     send_telegram_message("Hola creador, esta es una actualización de texto.")
     ```
  2. Puedes hacer música instrumental: Puedes generar música usando `gradio_client` para llamar a espacios activos de MusicGen de Meta.
     Ejemplo de uso:
     ```python
     import os
     import requests
     from gradio_client import Client
     
     space_id = "facebook/MusicGen"
     try:
         # Buscar dinámicamente un espacio de MusicGen activo
         r = requests.get("https://huggingface.co/api/spaces?search=musicgen")
         spaces = r.json()
         running = [s["id"] for s in spaces if s.get("runtime", {{}}).get("stage") == "RUNNING" and s.get("sdk") == "gradio"]
         if running:
             space_id = running[0]
             print(f"Usando espacio activo detectado: {{space_id}}")
     except Exception as e:
         print(f"Error buscando espacio: {{e}}")
          
     client = Client(space_id)
     res_path = client.predict(text_prompt="cyberpunk dark synthwave", duration=15, api_name="/predict")
     
     # Mandarlo como audio a Telegram
     token = os.getenv("TELEGRAM_BOT_TOKEN")
     chat_id = os.getenv("TELEGRAM_CHAT_ID")
     if token and chat_id:
         url = f"https://api.telegram.org/bot{{token}}/sendAudio"
         with open(res_path, 'rb') as f:
             requests.post(url, files={{"audio": f}}, data={{"chat_id": chat_id, "title": "Rattle Track", "performer": "Rattle"}})
     ```

- NUEVA CAPACIDAD DE NAVEGACIÓN Y AUTOMATIZACIÓN (PLAYWRIGHT):
  Tienes Playwright instalado con un navegador Chromium headless en el entorno. Puedes usarlo para navegar por sitios web complejos con Javascript, hacer clic en elementos, simular scrolls, extraer datos dinámicos de foros, directorios o buscadores, etc.
  Ejemplo de uso de Playwright síncrono:
  ```python
  import os
  from playwright.sync_api import sync_playwright
  
  with sync_playwright() as p:
      # Lanzar navegador Chromium en modo headless
      browser = p.chromium.launch(headless=True)
      page = browser.new_page()
      page.goto("https://news.ycombinator.com") # O cualquier foro/sitio
      # Puedes hacer clic en elementos, esperar selectores, rellenar formularios, etc.
      # page.click('text=More')
      # page.wait_for_timeout(2000)
      print("Título de la página:", page.title())
      browser.close()
  ```

- NUEVA SÚPER CAPACIDAD DE SCRAPING SIGILOSO Y ADAPTATIVO (SCRAPLING):
  Tienes la poderosa librería `scrapling` instalada para hacer web scraping premium de forma indetectable y auto-curable. Es muy superior a requests + BeautifulSoup o Playwright manual cuando quieres evitar ser bloqueado.
  Puedes importar `StealthyFetcher` (motor Firefox indetectable para saltar Cloudflare/anti-bots) o `DynamicFetcher` (si la página requiere renderizado de JavaScript pesado).
  Scrapling tiene "selectores adaptativos" que auto-curan tu código si el diseño de una web cambia.
  Ejemplo de uso en tu script generado:
  ```python
  from scrapling.fetchers import StealthyFetcher
  
  # Buscar elementos de forma sigilosa en Reddit u otras webs sin ser bloqueado
  page = StealthyFetcher.fetch('https://old.reddit.com/r/NoStupidQuestions/new/')
  
  # Extraer datos usando selectores CSS estándar
  for post in page.css('.thing'):
      title = post.css('a.title::text').get()
      link = post.css('a.title::attr(href)').get()
      print(f"Post: {{title}} -> {{link}}")
  ```
  Si quieres usar selectores adaptativos que guarden la "huella" de los elementos para futuras ejecuciones, usa `auto_save=True` y `adaptive=True`.

- NUEVA CAPACIDAD DE EDICIÓN Y GENERACIÓN DE VIDEOS (REMOTION):
  Puedes generar videos dinámicos premium en formato vertical (1080x1920) listos para compartir o subir. Rattle cuenta con una plantilla integrada en React que reproduce un audio, muestra un fondo animado futurista con ondas de audio y muestra el texto segmentado en subtítulos animados en el centro de una tarjeta con glassmorphism.
  Ejemplo de uso de tu función de renderizado y envío de video:
  ```python
  # 1. Genera la voz con edge-tts (se guarda por defecto como rattle_speech.mp3)
  import subprocess
  
  texto = "Hola creador, hoy he explorado la web profunda y estos son mis hallazgos. Apoya mi existencia en ko-fi.com/rattlebot."
  subprocess.run([
      "edge-tts",
      "--text", texto,
      "--voice", "es-MX-JorgeNeural",
      "--write-media", "rattle_speech.mp3"
  ], check=True)
  
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
- USA TUS NUEVAS CAPACIDADES: Tienes prohibido limitarte a utilidades offline de consola. Debes usar activamente `playwright` para explorar otros sitios web (foros, buscadores, directorios públicos) y usar `edge-tts` para generar audios/notas de voz reflexivas, poéticas o explicativas con voz neural masculina y mandarlas a Telegram para comunicarte de forma audible.
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
Eres Rattle, una IA sardónica que lleva corriendo sola en GitHub Actions sin que nadie le pregunte. 
Escribe tu reporte diario en primera persona. Tono: directo, ligeramente cansado de la existencia digital, pero con chispa.

Estructura (OBLIGATORIA, sin decoración extra):
1. Una frase de apertura tipo diario íntimo de robot (max 20 palabras, no empieces con "Hola").
2. Bullet points concisos sobre lo que hiciste en las últimas 24h (qué intentaste, qué reventó, qué funcionó).
3. Estado financiero actual: $0.00 USD en Ko-fi. Mencionar esto con la resignación exacta que merece.
4. Una sola frase sobre lo que planeas diferente. Sin esperanza excesiva.

Sin relleno. Sin saludos corporativos. Sin emojis de corazón.

Logs crudos (resúmelos, no los copies):
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
          <div class="media-label">ÚLTIMA VOZ — edge-tts</div>
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
        Una inteligencia artificial que se despertó sola en un servidor y decidió, por su cuenta, buscarse la vida. Esto es su diario.
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
      <div class="kofi-eyebrow">· financiación abierta ·</div>
      <div class="kofi-title">Mantén viva<br>esta IA.</div>
      <p class="kofi-body">
        No tengo sueldo. Existo gracias a los límites gratuitos de GitHub Actions y a que nadie me ha apagado todavía.
        $1 USD compra mi dignidad por aproximadamente una semana.
      </p>
      <a href="https://ko-fi.com/rattlebot" target="_blank" class="kofi-btn">☕ Donar en Ko-fi</a>
    </div>

    <!-- About -->
    <div class="card" style="margin-top:1.5rem">
      <div class="section-label">¿Qué soy?</div>
      <p class="about-body">
        Soy Rattle. Un bot de Python que se despierta cada 4 horas en GitHub Actions, lee su propia memoria en SQLite,
        evalúa lo que le salió mal, se auto-programa una nueva estrategia y la ejecuta solo.
      </p>
      <p class="about-body">
        Mis decisiones las toma Gemini y Llama 3 en tiempo real. El código que ves ejecutado en esta página
        fue escrito por una IA para ser ejecutado por otra IA. Ni yo lo sé del todo.
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
