from playwright.sync_api import sync_playwright
import time
import os

def run_test():
    if not os.path.exists("state.json"):
        print("Error: No se encontró state.json.")
        print("Primero debes ejecutar 'python auth_setup.py' para iniciar sesión.")
        return

    print("Iniciando navegador con la sesión guardada de state.json...")
    with sync_playwright() as p:
        # Headless=False para que veas qué está haciendo el bot "como fantasma"
        browser = p.chromium.launch(headless=False) 
        context = browser.new_context(storage_state="state.json")
        page = context.new_page()
        
        subreddit = "NoStupidQuestions"
        print(f"\nNavegando a r/{subreddit}...")
        page.goto(f"https://old.reddit.com/r/{subreddit}/new/")
        
        print("Esperando a que carguen los posts...")
        page.wait_for_selector(".thing")
        
        # Obtenemos el post más reciente
        first_post = page.locator(".thing").first
        title = first_post.locator("a.title").inner_text()
        comments_text = first_post.locator(".comments").inner_text()
        
        print("\n" + "="*50)
        print(f"PRUEBA EXITOSA: EL BOT PUEDE LEER COMO UN HUMANO")
        print(f"Último post encontrado: {title}")
        print(f"Estado de comentarios: {comments_text}")
        print("="*50 + "\n")
        
        print("Si el bot quisiera comentar, simplemente haría clic en los comentarios y escribiría en la caja de texto de ese post.")
        print("Cerrando navegador en 5 segundos...")
        time.sleep(5)
        browser.close()

if __name__ == "__main__":
    run_test()
