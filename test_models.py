import google.generativeai as genai
import sys

def test_models():
    # Usar una llave de prueba falsa (API_KEY_INVALIDA) para ver cómo responde Google.
    # Si el modelo EXISTE, Google dice: 400 API key not valid.
    # Si el modelo NO EXISTE, Google dice: 404 Not Found.
    genai.configure(api_key="AIzaSy_FAKE_API_KEY_JUST_FOR_TESTING")
    
    print("--- PROBANDO gemini-1.5-flash (El que dejé puesto) ---")
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        model.generate_content("Hola")
    except Exception as e:
        print(f"Resultado: {str(e)}")

    print("\n--- PROBANDO gemini-2.5-flash (El que el bot de Facebook tenía) ---")
    try:
        model2 = genai.GenerativeModel('gemini-2.5-flash')
        model2.generate_content("Hola")
    except Exception as e:
        print(f"Resultado: {str(e)}")

if __name__ == "__main__":
    test_models()
