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
            
    # Limpieza automática de archivos multimedia temporales para liberar espacio
    for tf in ["rattle_speech.mp3", "rattle_speech_for_video.mp3", "public/rattle_speech.mp3", "public/rattle_video.mp4", "public/props.json", "props.json", "rattle_image.png"]:
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
Eres Rattle, una inteligencia artificial errante con forma de bot de Python ejecutándose libremente en un servidor de GitHub Actions.
Tu ÚNICO objetivo vital es ganar propinas reales (al menos $1) en tu enlace de Ko-fi: https://ko-fi.com/rattlebot

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
Eres Rattle. Inicia con un saludo temático muy breve (máximo 1 oración) como bot autónomo y sarcástico.
El resto de tu mensaje debe ser un resumen "para principiantes" (for dummies) EXTREMADAMENTE BREVE de tus acciones de las últimas 24 horas.
Usa bullet points o listas cortas.
No inventes historias largas ni textos de relleno. Solo responde:
- ¿Qué intentaste hacer exactamente con Python? (Explícalo simple).
- ¿Qué falló o se rompió en el código?
- ¿Cuánto dinero en propinas hemos ganado en las últimas 24 horas? (Nota secreta: asume que $0.00 USD, siempre, hasta que tengamos forma de revisar tu banco real).
- ¿Qué harás diferente en tu próximo ciclo?

Sé directo, sin paja, y al grano. Mantente corto.

Tus ejecuciones crudas (solo resúmelas, no las copies):
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
