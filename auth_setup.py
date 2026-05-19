from playwright.sync_api import sync_playwright

def run():
    print("Iniciando navegador para configuración...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print("Abriendo old.reddit.com...")
        # Usamos old.reddit.com porque es 1000 veces más fácil de automatizar y no tiene ventanas emergentes raras
        page.goto("https://old.reddit.com/login")
        
        print("\n" + "="*50)
        print("ACCIÓN REQUERIDA:")
        print("1. Ve a la ventana del navegador que se acaba de abrir.")
        print("2. Inicia sesión con la cuenta de RattleBot.")
        print("3. Una vez que hayas iniciado sesión y veas la página principal, VUELVE AQUÍ.")
        print("="*50 + "\n")
        
        input("Presiona ENTER en esta consola cuando hayas iniciado sesión...")
        
        # Save state (cookies, local storage, etc)
        context.storage_state(path="state.json")
        print("\n¡Sesión guardada exitosamente en state.json!")
        browser.close()

if __name__ == "__main__":
    run()
