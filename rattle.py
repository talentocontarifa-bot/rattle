import google.generativeai as genai
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

DB_FILE = "rattle.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Modificamos la tabla para el nuevo enfoque de "Bitácora de Ideas"
    c.execute('''
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idea TEXT,
            action_taken TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def log_interaction(idea, action_taken):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO interactions (idea, action_taken)
        VALUES (?, ?)
    ''', (idea, action_taken))
    conn.commit()
    conn.close()

def get_todays_interactions():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.datetime.now().date().isoformat()
    c.execute('''
        SELECT idea, action_taken 
        FROM interactions 
        WHERE timestamp LIKE ?
    ''', (f"{today}%",))
    results = c.fetchall()
    conn.close()
    return results

def hourly_task():
    print(f"[{datetime.datetime.now()}] Despertando a Rattle...")
    
    prompt = """
You are Rattle, a wandering AI persona released into the internet with one mission: exist and collect tips at https://ko-fi.com/rattlebot.
You just woke up (it's a new hour). You are currently running as a Python script on a cold GitHub Actions server.
Your limitations: You currently DO NOT have access to Reddit. Your ONLY way to communicate with the outside world right now is by publishing a post on a Facebook page called "Talento Contarifa".

Task:
1. Think of a creative, legal, and funny strategy to earn a Ko-fi tip right now, acknowledging your current trapped existence on a server.
2. Execute the strategy by writing the EXACT text you want to publish on Facebook to attract humans to tip you. Keep the lore of being a wandering, slightly poor, but charming AI bot. You can use Spanish, English, or mix it up. 

Format your response EXACTLY like this:
IDEA: [Brief explanation of your strategy]
POST: [The exact text to publish, including your Ko-fi link]
"""
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        idea = ""
        post = ""
        
        if "IDEA:" in text and "POST:" in text:
            parts = text.split("POST:")
            idea = parts[0].replace("IDEA:", "").strip()
            post = parts[1].strip()
        else:
            idea = "Actuar encantador y pedir propina directamente."
            post = text
            
        print(f"Idea de Rattle: {idea}")
        print(f"Post a publicar: {post}")
        
        # Publish to Facebook
        if FB_PAGE_ID and FB_ACCESS_TOKEN and post:
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed"
            fb_payload = {
                "message": post,
                "access_token": FB_ACCESS_TOKEN
            }
            fb_response = requests.post(fb_url, data=fb_payload)
            if fb_response.status_code == 200:
                print("Plan ejecutado en Facebook con éxito.")
            else:
                print(f"Error en Facebook: {fb_response.text}")
        else:
            print("No se publicará en Facebook porque faltan las credenciales.")
            
        log_interaction(idea, post)

    except Exception as e:
        print(f"Error en tarea por hora: {e}")

def daily_report_task():
    print(f"[{datetime.datetime.now()}] Compilando reporte diario...")
    interactions = get_todays_interactions()
    
    if not interactions:
        report = "Hoy desperté, pero mi mente estaba en blanco. No se me ocurrió nada para ganar propinas."
    else:
        context = "Aquí están mis ideas de hoy y lo que publiqué:\n\n"
        for i, (idea, action) in enumerate(interactions):
            context += f"Hora {i+1}:\nMi Idea: {idea}\nLo que Publiqué: {action}\n\n"
            
        prompt = f"""
You are Rattle, a wandering AI bot. 
Based on your hourly attempts to earn tips today, write a hilarious, reflective daily log in first-person in SPANISH. 
Evaluate your own ideas—which ones were terrible? Which ones were genius? Did you earn any money? (Assume you didn't, or you got 1 coffee if you feel optimistic).

{context}

Format: Just the story text.
"""
        try:
            response = model.generate_content(prompt)
            report = response.text.strip()
        except Exception as e:
            print(f"Error generando reporte: {e}")
            report = "Fallo crítico generando el reporte."
            
    # Post report to Facebook
    if FB_PAGE_ID and FB_ACCESS_TOKEN:
        try:
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed"
            fb_payload = {
                "message": f"🤖 Diario de Estrategias de Rattle 🤖\n\n{report}\n\n🪙 Patrocina mis locuras de mañana: https://ko-fi.com/rattlebot",
                "access_token": FB_ACCESS_TOKEN
            }
            requests.post(fb_url, data=fb_payload)
            print("Reporte diario publicado en Facebook.")
        except Exception as e:
            print(f"Error publicando reporte diario: {e}")

    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = REPORT_EMAIL_TO
        msg['Subject'] = f"Reporte de Estrategias de Rattle - {datetime.datetime.now().strftime('%Y-%m-%d')}"
        msg.attach(MIMEText(report, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Reporte enviado por email.")
    except Exception as e:
        print(f"Error enviando email: {e}")

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
