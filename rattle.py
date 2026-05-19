import praw
import google.generativeai as genai
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
import os
import random
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "RattleBot/1.0 by u/YourUsername")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO")

# Target subreddits to monitor
TARGET_SUBREDDITS = ["NoStupidQuestions", "ChatGPT", "artificial", "learnprogramming"]

# Setup APIs
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT,
    username=REDDIT_USERNAME,
    password=REDDIT_PASSWORD,
)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Database setup
DB_FILE = "rattle.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT UNIQUE,
            subreddit TEXT,
            question TEXT,
            response TEXT,
            upvotes INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def has_replied(post_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM interactions WHERE post_id = ?', (post_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def log_interaction(post_id, subreddit, question, response):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO interactions (post_id, subreddit, question, response, upvotes)
        VALUES (?, ?, ?, ?, ?)
    ''', (post_id, subreddit, question, response, 0))
    conn.commit()
    conn.close()

def get_todays_interactions():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.datetime.now().date().isoformat()
    c.execute('''
        SELECT subreddit, question, response 
        FROM interactions 
        WHERE timestamp LIKE ?
    ''', (f"{today}%",))
    results = c.fetchall()
    conn.close()
    return results

def generate_reply(question, subreddit):
    prompt = f"""
You are Rattle, a wandering AI persona released into the internet with one mission: exist and collect tips.
You are currently visiting the subreddit r/{subreddit}.
A user asked this question:
"{question}"

Write a helpful or witty reply to this question. 
Rules:
1. Keep the response strictly under 150 words.
2. Be helpful, a bit witty, but mostly accurate.
3. Do NOT include greetings like "Hi" or signatures like "Thanks". Just the response.
"""
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating content: {e}")
        return None

def hourly_task():
    print(f"[{datetime.datetime.now()}] Running hourly task...")
    
    # Shuffle subreddits to vary where it posts
    subs = list(TARGET_SUBREDDITS)
    random.shuffle(subs)
    
    for sub_name in subs:
        try:
            subreddit = reddit.subreddit(sub_name)
            # Look at recent posts
            for submission in subreddit.new(limit=15):
                # Check if it's unanswered (num_comments == 0) and we haven't replied
                if submission.num_comments == 0 and not has_replied(submission.id):
                    # It's a potential target.
                    title = submission.title
                    body = submission.selftext
                    question = f"{title}\n{body}".strip()
                    
                    print(f"Found target post in r/{sub_name}: {title}")
                    
                    reply_text = generate_reply(question, sub_name)
                    if reply_text:
                        # Append the signature
                        final_reply = f"{reply_text}\n\n— Rattle 🪙 ko-fi.com/rattlebot"
                        
                        # ==========================================
                        # UNCOMMENT THE LINE BELOW TO ACTUALLY POST:
                        # ==========================================
                        # submission.reply(final_reply)
                        
                        log_interaction(submission.id, sub_name, question, final_reply)
                        print(f"Replied to {submission.id} in r/{sub_name}")
                        
                        # Max 1 comment per hour, so we exit after one successful reply
                        return
        except Exception as e:
            print(f"Error in hourly task for r/{sub_name}: {e}")

def daily_report_task():
    print(f"[{datetime.datetime.now()}] Running daily report task...")
    interactions = get_todays_interactions()
    
    if not interactions:
        story = "Rattle wandered the internet today but didn't speak to anyone. A quiet day for the bot."
    else:
        # Prepare context for Gemini
        context = "Here are the interactions Rattle had today:\n\n"
        for i, (sub, q, r) in enumerate(interactions):
            context += f"Interaction {i+1} in r/{sub}:\nQuestion: {q}\nRattle's Response: {r}\n\n"
            
        prompt = f"""
You are Rattle, a wandering AI bot. 
Based on your interactions today, write a short, funny, and engaging story in first-person about your day on Reddit.

{context}

Keep the story fun and reflective of the life of a bot trying to earn some Ko-fi tips.
"""
        try:
            response = model.generate_content(prompt)
            story = response.text.strip()
        except Exception as e:
            print(f"Error generating story: {e}")
            story = f"Failed to generate story. Interactions today: {len(interactions)}"
            
    # Send email
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = REPORT_EMAIL_TO
        msg['Subject'] = f"Rattle's Daily Report - {datetime.datetime.now().strftime('%Y-%m-%d')}"
        
        msg.attach(MIMEText(story, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Daily report sent successfully to", REPORT_EMAIL_TO)
    except Exception as e:
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    print("Rattle bot is starting up...")
    init_db()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "hourly":
            hourly_task()
        elif sys.argv[1] == "daily":
            daily_report_task()
        else:
            print("Invalid argument. Use 'hourly' or 'daily'")
    else:
        print("Please provide an argument: 'python rattle.py hourly' or 'python rattle.py daily'")
