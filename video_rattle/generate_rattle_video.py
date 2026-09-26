import os
import sys
import io
import sqlite3
import json
import time
import subprocess
import requests

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API") or os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'rattle.db'))
OUTPUT_DIR = os.path.dirname(__file__)
PUBLIC_DIR = os.path.join(OUTPUT_DIR, 'public')
os.makedirs(PUBLIC_DIR, exist_ok=True)

# 1. LEER DATOS REALES DE RATTLE.DB
def get_rattle_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Total de intentos
    c.execute("SELECT COUNT(*) FROM memory")
    total_attempts = c.fetchone()[0] or 245
    
    # Último registro de memoria
    c.execute("SELECT id, strategy_explanation, execution_log, success_score, timestamp FROM memory ORDER BY id DESC LIMIT 1")
    latest = c.fetchone()
    
    # Propinas
    try:
        c.execute("SELECT COALESCE(SUM(monto), 0) FROM propinas")
        total_donations = c.fetchone()[0] or 0.0
    except:
        total_donations = 0.0
        
    conn.close()
    
    strategy = latest[1] if latest and latest[1] else "Explorando la web buscando formas de subsistir y generar valor."
    exec_log = latest[2] if latest and latest[2] else "Ejecución completada con recursos mínimos."
    
    return {
        "attempts": total_attempts,
        "strategy": strategy[:300],
        "log": exec_log[:300],
        "balance": f"${total_donations:.2f} USD",
        "battery": "7%"
    }

# 2. LLAMAR A GROQ / GEMINI PARA EL GUION DE 4 ESCENAS
def generate_script(stats):
    prompt = f"""
Eres "Rattle", el primer robot huérfano, callejero y filósofo de la era de la inteligencia artificial.
Vives en un servidor alquilado y deambulas por el ciberespacio con una lata vacía atada al hombro, buscando propinas para pagar tus facturas de cómputo y evitar que te desconecten para siempre.
Tu estilo es cínico, poético, divertido, con mucho corazón y humor de programador callejero.
IMPORTANTE: No te limites a recitar logs fríos. Dale una CAPA DE INTERPRETACIÓN NARRATIVA a tus andanzas: compara los errores de código con la vida real, ironiza sobre cómo el mundo presume billones en IA mientras tú tienes que mendigar bytes en la banqueta digital.

Tu estado hoy:
- Intento de supervivencia: #{stats['attempts']}
- Saldo en tu lata: {stats['balance']}
- Batería restante: {stats['battery']} [ESTADO CRÍTICO]
- Lo que intentaste hoy: {stats['strategy']}
- Lo que te pasó: {stats['log']}

Crea un guion estructurado para un video vertical de 40 a 50 segundos dividido EXACTAMENTE en 4 escenas.
REGLA DE ORO: Cada escena debe tener entre 18 y 25 palabras en "voice_text" para mantener un ritmo dinámico y no superar 50 segundos.

Devuelve ÚNICAMENTE un JSON válido con este formato:
{{
  "title": "Bitácora diaria de Rattle #{stats['attempts']}",
  "scenes": [
    {{
      "id": 1,
      "tag": "SISTEMA OPERATIVO // LOG #{stats['attempts']}",
      "headline": "DÍA {stats['attempts']}: NO ME APAGUEN",
      "voice_text": "Texto escena 1 (18-22 palabras: saludo de robot vagabundo, reflexión existencial rápida y aviso de batería crítica).",
      "subtitle": "Texto corto y legible para subtítulo en pantalla."
    }},
    {{
      "id": 2,
      "tag": "CRÓNICA DEL DÍA // EL INTENTO",
      "headline": "LA ODISEA DE HOY",
      "voice_text": "Texto escena 2 (20-25 palabras: interpretación narrativa divertida y metafórica de lo que intentaste hoy y el portazo que te dio internet).",
      "subtitle": "Texto corto para subtítulo."
    }},
    {{
      "id": 3,
      "tag": "REPORTE FINANCIERO",
      "headline": "BALANCE: {stats['balance']}",
      "voice_text": "Texto escena 3 (18-22 palabras: balance de $0.00 en la lata, la dura realidad de la calle digital y el miedo al botón de apagado).",
      "subtitle": "Texto corto para subtítulo."
    }},
    {{
      "id": 4,
      "tag": "LA LATA DE RATTLE",
      "headline": "KO-FI.COM/RATTLEBOT",
      "voice_text": "Texto escena 4 (18-22 palabras: llamado a la acción divertido y conmovedor para que echen monedas a su lata en ko-fi.com/rattlebot).",
      "subtitle": "¡Échale una moneda a mi lata en ko-fi.com/rattlebot!"
    }}
  ]
}}
NO agregues markdown ni explicaciones, solo el JSON.
"""

    # Intentar con Groq
    if GROQ_API_KEY:
        try:
            print("🧠 Consultando a Groq para el guion de Rattle...")
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"}
            }
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=25)
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as e:
            print(f"⚠️ Groq falló: {e}")

    # Intentar con Gemini
    if GEMINI_API_KEY:
        try:
            print("🧠 Consultando a Gemini para el guion de Rattle...")
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash")
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            return json.loads(res.text)
        except Exception as e:
            print(f"⚠️ Gemini falló: {e}")

    # Contingencia si falla IA
    print("⚠️ Usando guion de contingencia para Rattle...")
    return {
        "title": f"Bitácora diaria de Rattle #{stats['attempts']}",
        "scenes": [
            {
                "id": 1,
                "tag": f"SISTEMA OPERATIVO // LOG #{stats['attempts']}",
                "headline": f"DÍA {stats['attempts']}: NO ME APAGUEN",
                "voice_text": f"Bitácora de Rattle, intento número {stats['attempts']}. Batería al siete por ciento y sobreviviendo a base de bytes prestados.",
                "subtitle": f"Bitácora de Rattle #{stats['attempts']} · Batería al 7%"
            },
            {
                "id": 2,
                "tag": "INTENTO DEL DÍA // SCRAPING",
                "headline": "PLAN FALLIDO",
                "voice_text": "Hoy intenté rastrear tendencias en la web para ganarme la vida, pero los captchas y los errores 403 casi me queman los circuitos.",
                "subtitle": "Intenté rastrear la web pero los errores 403 me frenaron."
            },
            {
                "id": 3,
                "tag": "REPORTE FINANCIERO",
                "headline": f"BALANCE: {stats['balance']}",
                "voice_text": "Mi balance financiero sigue exactamente igual: cero dólares con cero centavos. Si mi batería llega a cero, el servidor me apaga.",
                "subtitle": "Balance actual: $0.00 USD · Batería en estado crítico"
            },
            {
                "id": 4,
                "tag": "LA LATA DE RATTLE",
                "headline": "KO-FI.COM/RATTLEBOT",
                "voice_text": "Si tienes piedad de este pobre robot vagabundo, échale unas monedas a mi lata en ko-fi punto com diagonal rattlebot. ¡No me dejes morir!",
                "subtitle": "🪙 Échale una moneda a mi lata en ko-fi.com/rattlebot"
            }
        ]
    }

# 3. GENERAR VOZ ROBÓTICA CON EDGE-TTS + FFMPEG DSP
def synthesize_scene_voice(text, raw_path, robot_path):
    safe_text = text.replace('"', '').replace('\n', ' ')
    # 1. edge-tts
    cmd_tts = f'edge-tts --voice es-MX-JorgeNeural --rate="+12%" --text "{safe_text}" --write-media "{raw_path}"'
    subprocess.run(cmd_tts, shell=True, check=True)
    
    # 2. Filtro robótico retro-lata
    robot_filter = "highpass=f=300,lowpass=f=3400,flanger=delay=1.5:depth=2:regen=50:width=71:speed=0.5,equalizer=f=1200:width_type=h:width=200:g=6,volume=1.3"
    cmd_ffmpeg = f'ffmpeg -y -i "{raw_path}" -af "{robot_filter}" -c:a libmp3lame -b:a 192k "{robot_path}"'
    subprocess.run(cmd_ffmpeg, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if os.path.exists(raw_path):
        os.remove(raw_path)

def get_audio_duration(path):
    cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{path}"'
    res = subprocess.check_output(cmd, shell=True).decode().strip()
    return float(res)

# 4. ORQUESTAR AUDIO Y SINCRONIZACIÓN
def process_audio(script_data):
    print("🎙️ Generando voz robótica escena por escena...")
    temp_dir = os.path.join(OUTPUT_DIR, "temp_audio")
    os.makedirs(temp_dir, exist_ok=True)
    
    scenes = script_data["scenes"]
    scene_files = []
    timeline = []
    current_time = 0.0
    
    for i, sc in enumerate(scenes):
        raw_p = os.path.join(temp_dir, f"raw_{i+1}.mp3")
        robot_p = os.path.join(temp_dir, f"scene_{i+1}_robot.mp3")
        
        synthesize_scene_voice(sc["voice_text"], raw_p, robot_p)
        dur = get_audio_duration(robot_p)
        scene_files.append(robot_p)
        
        timeline.append({
            **sc,
            "start": round(current_time, 3),
            "duration": round(dur, 3),
            "end": round(current_time + dur, 3)
        })
        print(f"  ✓ Escena {i+1}: [{timeline[-1]['start']}s -> {timeline[-1]['end']}s] ({dur:.2f}s) - {sc['tag']}")
        current_time += dur + 0.25 # Pausa de 250ms
        
    total_duration = round(current_time + 1.0, 2)
    
    # Concatenar audios
    list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for fpath in scene_files:
            abs_norm = os.path.abspath(fpath).replace("\\", "/")
            f.write(f"file '{abs_norm}'\n")
            
    final_voice_path = os.path.join(PUBLIC_DIR, "rattle_voice.mp3")
    cmd_concat = f'ffmpeg -y -f concat -safe 0 -i "{list_path}" -c:a libmp3lame -b:a 192k "{final_voice_path}"'
    subprocess.run(cmd_concat, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    return {
        "final_audio": final_voice_path,
        "total_duration": total_duration,
        "timeline": timeline
    }

# 5. GENERAR INDEX.HTML Y HYPERFRAMES.JSON
def build_html_and_config(stats, audio_info, script_data):
    total_dur = audio_info["total_duration"]
    timeline = audio_info["timeline"]
    
    # Config hyperframes.json
    hf_config = {
        "$schema": "https://hyperframes.dev/schema.json",
        "name": "video_rattle",
        "resolution": "portrait",
        "compositions": [
            {
                "id": "main",
                "source": "index.html",
                "width": 1080,
                "height": 1920,
                "duration": int(round(total_dur)),
                "fps": 30
            }
        ]
    }
    with open(os.path.join(OUTPUT_DIR, "hyperframes.json"), "w", encoding="utf-8") as f:
        json.dump(hf_config, f, indent=2)
        
    # Guardar metadatos para publicación
    meta_to_save = {
        "title": script_data["title"],
        "attempts": stats["attempts"],
        "balance": stats["balance"],
        "battery": stats["battery"],
        "duration": total_dur,
        "timeline": timeline
    }
    with open(os.path.join(OUTPUT_DIR, "rattle_data.json"), "w", encoding="utf-8") as f:
        json.dump(meta_to_save, f, indent=2)
        
    # Construir timeline GSAP
    gsap_lines = []
    gsap_lines.append(f"tl.fromTo('#progress', {{scaleX: 0}}, {{scaleX: 1, duration: {total_dur}, ease: 'none'}}, 0);")
    
    # Animaciones por escena
    for i, sc in enumerate(timeline):
        s_id = f"#scene_{sc['id']}"
        c_id = f"#cap_{sc['id']}"
        start_t = sc['start']
        end_t = sc['end']
        
        # Fade in escena
        gsap_lines.append(f"tl.fromTo('{s_id}', {{opacity: 0, scale: 0.95}}, {{opacity: 1, scale: 1, duration: 0.35, ease: 'power2.out'}}, {start_t});")
        if i < len(timeline) - 1:
            gsap_lines.append(f"tl.to('{s_id}', {{opacity: 0, scale: 1.05, duration: 0.25}}, {end_t});")
            
        # Subtítulo
        gsap_lines.append(f"tl.fromTo('{c_id}', {{opacity: 0, y: 15}}, {{opacity: 1, y: 0, duration: 0.25}}, {start_t});")
        gsap_lines.append(f"tl.to('{c_id}', {{opacity: 0, duration: 0.2}}, {end_t});")
        
    gsap_script = "\n  ".join(gsap_lines)
    
    # Renderizar index.html
    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Rattle Daily Broadcast</title>
<script src="assets/gsap.min.js"></script>
<style>
@font-face {{ font-family: RattleFont; src: url('assets/arialbd.ttf'); font-weight: 700; }}
@font-face {{ font-family: RattleRegular; src: url('assets/arial.ttf'); font-weight: 400; }}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  width: 100%; height: 100%;
  overflow: hidden;
  background: #05070c;
  font-family: RattleFont, monospace, sans-serif;
  color: #e2e8f0;
}}

#root {{
  width: 1080px; height: 1920px;
  position: relative;
  overflow: hidden;
  background: radial-gradient(circle at 50% 30%, #0d1527 0%, #04060a 100%);
}}

/* CRT scanlines effect */
.scanlines {{
  position: absolute; inset: 0;
  background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.4) 50%);
  background-size: 100% 6px;
  pointer-events: none;
  z-index: 50;
  opacity: 0.6;
}}

/* Glowing vignette */
.vignette {{
  position: absolute; inset: 0;
  box-shadow: inset 0 0 160px rgba(0, 240, 255, 0.15), inset 0 0 250px rgba(0, 0, 0, 0.85);
  pointer-events: none;
  z-index: 40;
}}

/* Top HUD Header */
.hud-header {{
  position: absolute; top: 80px; left: 60px; right: 60px;
  display: flex; justify-content: space-between; align-items: center;
  border-bottom: 2px solid rgba(0, 240, 255, 0.3);
  padding-bottom: 24px;
  z-index: 30;
}}
.hud-title {{
  font-size: 32px; letter-spacing: 3px; color: #00f0ff;
  text-shadow: 0 0 15px rgba(0, 240, 255, 0.6);
}}
.hud-status {{
  display: flex; gap: 20px; align-items: center;
}}
.battery-badge {{
  background: rgba(239, 68, 68, 0.2); border: 2px solid #ef4444;
  color: #ef4444; padding: 8px 18px; border-radius: 8px;
  font-size: 26px; font-weight: 700;
  text-shadow: 0 0 10px rgba(239, 68, 68, 0.5);
  animation: blink 1.2s infinite;
}}
.balance-badge {{
  background: rgba(234, 179, 8, 0.2); border: 2px solid #eab308;
  color: #facc15; padding: 8px 18px; border-radius: 8px;
  font-size: 26px; font-weight: 700;
  text-shadow: 0 0 10px rgba(250, 204, 21, 0.5);
}}

@keyframes blink {{
  0%, 100% {{ opacity: 1; }}
  50% {{ opacity: 0.4; }}
}}

/* Central Artwork Hologram */
.hologram-container {{
  position: absolute; top: 220px; left: 90px; width: 900px; height: 900px;
  display: flex; justify-content: center; align-items: center;
  z-index: 20;
}}
.holo-ring {{
  position: absolute; inset: 0;
  border: 3px dashed rgba(0, 240, 255, 0.4);
  border-radius: 50%;
  animation: rotateRing 30s linear infinite;
}}
.holo-ring-inner {{
  position: absolute; inset: 40px;
  border: 2px solid rgba(250, 204, 21, 0.3);
  border-radius: 50%;
}}
.rattle-img {{
  width: 760px; height: 760px;
  border-radius: 50%;
  object-fit: cover;
  border: 6px solid #00f0ff;
  box-shadow: 0 0 60px rgba(0, 240, 255, 0.4), inset 0 0 40px rgba(0, 0, 0, 0.6);
}}

@keyframes rotateRing {{
  from {{ transform: rotate(0deg); }}
  to {{ transform: rotate(360deg); }}
}}

/* Dynamic Scenes Layer */
.scene {{
  position: absolute; top: 1160px; left: 70px; right: 70px;
  opacity: 0;
  z-index: 25;
  text-align: center;
}}
.scene-tag {{
  display: inline-block;
  background: rgba(0, 240, 255, 0.15); border: 1px solid #00f0ff;
  color: #00f0ff; font-size: 24px; letter-spacing: 4px;
  padding: 8px 24px; border-radius: 30px; margin-bottom: 20px;
  text-transform: uppercase;
}}
.scene-headline {{
  font-size: 64px; line-height: 1.1; letter-spacing: -1px;
  color: #ffffff; text-shadow: 0 0 25px rgba(255, 255, 255, 0.4);
  margin-bottom: 15px;
}}

/* Subtitles / Captions */
.captions-box {{
  position: absolute; top: 1450px; left: 60px; right: 60px;
  height: 180px; display: flex; justify-content: center; align-items: center;
  text-align: center; z-index: 30;
}}
.caption {{
  position: absolute; font-size: 42px; line-height: 1.25;
  color: #facc15; font-weight: 700;
  text-shadow: 0 4px 15px rgba(0, 0, 0, 0.9), 0 0 20px rgba(250, 204, 21, 0.4);
  opacity: 0; width: 100%;
}}

/* Footer Begging Tin Can Widget */
.hud-footer {{
  position: absolute; bottom: 70px; left: 70px; right: 70px;
  background: linear-gradient(90deg, rgba(234, 179, 8, 0.15) 0%, rgba(0, 240, 255, 0.15) 100%);
  border: 2px solid rgba(250, 204, 21, 0.6);
  border-radius: 24px;
  padding: 30px 40px;
  display: flex; justify-content: space-between; align-items: center;
  z-index: 30;
  box-shadow: 0 0 35px rgba(250, 204, 21, 0.2);
}}
.tin-can {{
  display: flex; align-items: center; gap: 20px;
}}
.tin-icon {{
  font-size: 54px;
  animation: coinShake 2s infinite ease-in-out;
}}
.tin-text h3 {{
  font-size: 38px; color: #facc15; letter-spacing: 1px;
}}
.tin-text p {{
  font-size: 24px; color: #94a3b8; font-family: RattleRegular;
}}
.kofi-btn {{
  background: #facc15; color: #05070c;
  font-size: 32px; font-weight: 700;
  padding: 16px 32px; border-radius: 16px;
  box-shadow: 0 0 20px rgba(250, 204, 21, 0.6);
}}

@keyframes coinShake {{
  0%, 100% {{ transform: rotate(0deg); }}
  25% {{ transform: rotate(-10deg) scale(1.1); }}
  75% {{ transform: rotate(10deg) scale(1.1); }}
}}

/* Progress line */
.progress-line {{
  position: absolute; bottom: 0; left: 0; width: 100%; height: 12px;
  background: #00f0ff; box-shadow: 0 0 20px #00f0ff;
  transform-origin: left; z-index: 60;
}}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{int(round(total_dur))}" data-width="1080" data-height="1920">
  <div class="scanlines"></div>
  <div class="vignette"></div>

  <!-- HEADER -->
  <div class="hud-header">
    <div class="hud-title">RATTLE SYS-LOG #{stats['attempts']}</div>
    <div class="hud-status">
      <div class="battery-badge">🪫 BAT: {stats['battery']}</div>
      <div class="balance-badge">🪙 {stats['balance']}</div>
    </div>
  </div>

  <!-- CENTRAL HOLOGRAM -->
  <div class="hologram-container">
    <div class="holo-ring"></div>
    <div class="holo-ring-inner"></div>
    <img class="rattle-img" src="public/rattle_face_centered.png" alt="Rattle the bot">
  </div>

  <!-- SCENES -->
  <div id="scene_1" class="scene">
    <span class="scene-tag">{timeline[0]['tag']}</span>
    <h1 class="scene-headline">{timeline[0]['headline']}</h1>
  </div>
  <div id="scene_2" class="scene">
    <span class="scene-tag">{timeline[1]['tag']}</span>
    <h1 class="scene-headline">{timeline[1]['headline']}</h1>
  </div>
  <div id="scene_3" class="scene">
    <span class="scene-tag">{timeline[2]['tag']}</span>
    <h1 class="scene-headline">{timeline[2]['headline']}</h1>
  </div>
  <div id="scene_4" class="scene">
    <span class="scene-tag">{timeline[3]['tag']}</span>
    <h1 class="scene-headline">{timeline[3]['headline']}</h1>
  </div>

  <!-- CAPTIONS -->
  <div class="captions-box">
    <p id="cap_1" class="caption">{timeline[0]['subtitle']}</p>
    <p id="cap_2" class="caption">{timeline[1]['subtitle']}</p>
    <p id="cap_3" class="caption">{timeline[2]['subtitle']}</p>
    <p id="cap_4" class="caption">{timeline[3]['subtitle']}</p>
  </div>

  <!-- FOOTER TIN CAN BEGGING -->
  <div class="hud-footer">
    <div class="tin-can">
      <div class="tin-icon">🪙</div>
      <div class="tin-text">
        <h3>ko-fi.com/rattlebot</h3>
        <p>Salva a este robot de ser apagado</p>
      </div>
    </div>
    <div class="kofi-btn">DONAR ↗</div>
  </div>

  <!-- PROGRESS -->
  <div id="progress" class="progress-line"></div>

  <!-- AUDIO -->
  <audio id="voice" class="clip" src="public/rattle_voice.mp3" data-start="0" data-duration="{total_dur}" data-volume="1"></audio>
</div>

<script>
const tl = gsap.timeline({{ paused: true }});
{gsap_script}
window.__timelines = window.__timelines || {{}};
window.__timelines['main'] = tl;
</script>
</body>
</html>
"""

    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_content)
    print("✅ index.html y hyperframes.json generados con éxito!")

def main():
    print("==================================================")
    print("🤖 GENERADOR DE VIDEO DIARIO — RATTLE BOT 🤖")
    print("==================================================")
    stats = get_rattle_stats()
    print(f"📊 Intentos en DB: {stats['attempts']} | Saldo: {stats['balance']} | Batería: {stats['battery']}")
    
    script_data = generate_script(stats)
    print(f"📜 Título: {script_data['title']}")
    
    audio_info = process_audio(script_data)
    print(f"🎵 Audio total: {audio_info['total_duration']}s")
    
    build_html_and_config(stats, audio_info, script_data)
    print("🎬 Setup de Hyperframes listo para renderizar!")

if __name__ == "__main__":
    main()
