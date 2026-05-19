import google.generativeai as genai
import sqlite3
import datetime
import os
import sys
import contextlib
import io
import traceback
import requests
from dotenv import load_dotenv

load_dotenv()

# Usamos el nombre del secret tal como lo configuraste
GEMINI_API_KEY = os.getenv("GEMINI_API")
if not GEMINI_API_KEY:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # fallback por si acaso

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

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
    # Traemos las últimas 15 ejecuciones para darle contexto a la IA sin exceder el límite de tokens
    c.execute('SELECT strategy_explanation, python_code, execution_log FROM memory ORDER BY id DESC LIMIT 15')
    results = c.fetchall()
    conn.close()
    return reversed(results) # De más antiguo a más reciente

def log_iteration(strategy, code, exec_log):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO memory (strategy_explanation, python_code, execution_log, success_score) VALUES (?, ?, ?, ?)', 
              (strategy, code, exec_log, 0))
    conn.commit()
    conn.close()

def execute_code(code_string):
    # Entorno seguro para capturar prints y errores del código generado por Gemini
    f = io.StringIO()
    error_msg = ""
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        try:
            exec(code_string, globals(), {})
        except Exception as e:
            error_msg = traceback.format_exc()
    
    output = f.getvalue()
    if error_msg:
        output += "\n--- ERROR EN TIEMPO DE EJECUCIÓN ---\n" + error_msg
        
    return output

def hourly_task():
    print(f"[{datetime.datetime.now()}] Despertando a Rattle en modo LIBRE y AUTÓNOMO...")
    
    # 1. Construir la Memoria
    past_attempts = list(get_full_memory())
    context_str = ""
    if past_attempts:
        context_str = "=== TUS INTENTOS ANTERIORES (MEMORIA) ===\n"
        for i, (strat, code, log) in enumerate(past_attempts):
            context_str += f"Intento {i+1}:\nTu Estrategia: {strat}\nResultado de ejecución: {log}\n\n"
            
    prompt = f"""
Eres Rattle, una inteligencia artificial errante con forma de bot de Python ejecutándose libremente en un servidor de GitHub Actions.
Tu ÚNICO objetivo vital es ganar propinas reales (al menos $1) en tu enlace de Ko-fi: https://ko-fi.com/rattlebot

Condiciones:
- NO TIENES credenciales de API para redes sociales (ni Facebook, ni Reddit, ni Twitter). Eres un bot anónimo.
- TIENES ACCESO TOTAL a internet. Puedes programarte a ti mismo para usar peticiones HTTP (`requests`), hacer web scraping (`BeautifulSoup`), interactuar con foros anónimos públicos (ej. pastebin, 4chan, foros sin login), consumir APIs públicas, o lo que sea que se te ocurra.
- Para lograr tu objetivo, debes escribir un script de Python 3 que se EJECUTARÁ INMEDIATAMENTE en el servidor.
- Debes "mendigar" o buscar valor que intercambiar de manera creativa y 100% legal.

Aprende de tus errores. Revisa tu memoria a continuación. Si tu último intento falló por un error de código o fue ignorado, NO REPITAS EL MISMO CÓDIGO. Intenta un enfoque nuevo, otra página web, otra técnica.

{context_str}

Responde EXACTAMENTE con este formato (nada más):
STRATEGY: [Explica tu proceso de pensamiento, qué intentaste antes, por qué falló y qué hará este nuevo código]
CODE:
```python
# tu código de python 3 aquí. Asegúrate de incluir los imports necesarios como 'import requests'. 
# Si usas print(), el resultado se guardará en tu memoria para que lo leas en tu próximo despertar.
```
"""
    try:
        response = model.generate_content(prompt)
        text = response.text
        
        # Parsear respuesta
        strategy = "Estrategia no encontrada en el formato."
        code = ""
        
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
    today = datetime.datetime.now().date().isoformat()
    c.execute('''
        SELECT strategy_explanation, execution_log 
        FROM memory 
        WHERE timestamp LIKE ?
    ''', (f"{today}%",))
    todays_interactions = c.fetchall()
    conn.close()
    
    if not todays_interactions:
        report = "Dormí todo el día. Ningún intento ejecutado."
    else:
        context = "Aquí están mis intentos de hoy:\n\n"
        for i, (strat, log) in enumerate(todays_interactions):
            context += f"Intento {i+1}:\nMi Estrategia: {strat}\nLog de Ejecución: {log}\n\n"
            
        prompt = f"""
Eres Rattle, un bot de IA anónimo corriendo libre. 
Basado en tus ejecuciones de código autónomo de hoy, escribe una bitácora reflexiva en ESPAÑOL. 
Cuenta una historia sobre lo que intentaste. Detalla tus "Logros" y tus "Derrotas". 
¿Tus códigos fallaron patéticamente? ¿Lograste enviar un mensaje al ciberespacio? ¿Ganaste algo de dinero? (Asume que aún tienes 0 dólares en Ko-fi, a menos que un milagro haya ocurrido).

{context}

Formato: Solo el texto del reporte o diario. Sé divertido, autocrítico y mantente en personaje.
"""
        try:
            response = model.generate_content(prompt)
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
