# 🤖 Rattle — Autonomous AI Entity

Rattle es una entidad autónoma de IA que corre programada en **GitHub Actions**, reflexiona sobre sus intentos anteriores en SQLite, navega por internet, genera contenido audiovisual y busca ganarse propinas en su enlace de **Ko-fi**: [https://ko-fi.com/rattlebot](https://ko-fi.com/rattlebot).

---

## ⚡ Nuevas Capacidades Integradas

### 1. 🛡️ Obscura (Headless Stealth Browser Engine)
Rattle ahora cuenta con **Obscura** (motor en Rust con soporte CDP y evasión de sistemas anti-bot como Cloudflare):
* **Extracción ultrarrápida a Markdown:**
  ```python
  md = obscura_fetch("https://news.ycombinator.com", mode="markdown")
  ```
* **Navegación interactiva con Playwright:**
  ```python
  from playwright.sync_api import sync_playwright

  with sync_playwright() as p:
      # Conecta automáticamente Playwright sobre CDP al puerto 9222 de Obscura:
      browser, context = get_stealth_browser(p)
      page = context.new_page()
      page.goto("https://news.ycombinator.com")
      print("Título:", page.title())
      browser.close()
  ```

---

### 2. ☕ Integración y Publicación en Ko-fi

Rattle ahora puede interactuar con su perfil de creador en Ko-fi:
* **Publicar posts o actualizaciones en su feed:**
  ```python
  res = post_to_kofi(
      title="Bitácora autónoma: reflexiones desde el servidor",
      content="Hoy analicé nuevas tendencias y optimicé mi código. Apóyame en ko-fi.com/rattlebot"
  )
  ```
* **Consultar donantes y metas públicas:**
  ```python
  stats = check_kofi_stats()
  ```
* **Configurar la sesión (local y GitHub Actions):**
  1. Ejecuta el asistente interactivo:
     ```bash
     python kofi_setup.py
     ```
  2. Inicia sesión en la ventana de Chrome que se abrirá y marca *"Remember Me"*.
  3. Escribe `listo` en la terminal. Se generará el archivo `kofi_state.json` y una cadena en Base64.
  4. En GitHub (`Settings -> Secrets and variables -> Actions`), agrega el Secret:
     `KOFI_STORAGE_STATE` con el valor Base64 obtenido.
  5. Para verificar si la sesión sigue activa en cualquier momento:
     ```bash
     python kofi_setup.py --check
     ```

---

## 🩺 Operación y diagnóstico

### Workflows
| Workflow | Qué hace | Si falla |
|---|---|---|
| `hourly.yml` | Ciclo autónomo cada 4h (corre `tests/` antes de gastar cuota de LLM) | Alerta a Telegram |
| `daily.yml` | Bitácora diaria a Telegram/Ko-fi | Alerta a Telegram |
| `daily_video.yml` | Genera, optimiza (`optimize_video.sh`) y publica el video | Reporte por red a Telegram; falla si **todas** las redes fallan (`PUBLISH_STRICT=true` para fallar con cualquiera) |
| `check_tokens.yml` | Verifica credenciales de TikTok/IG/YouTube **sin publicar** | Muestra qué token está roto |

En ejecución manual de `daily_video.yml` puedes elegir plataformas (`tiktok,instagram,youtube`).

### Secrets requeridos / opcionales
- `GH_SECRETS_PAT` *(opcional, recomendado)*: PAT fine-grained con permiso **Secrets: Read and write** sobre este repo. Permite guardar el `TIKTOK_REFRESH_TOKEN` cuando TikTok lo rota.
- `KOFI_STORAGE_STATE`: ver sección de Ko-fi arriba.

### Problemas conocidos
- **YouTube `invalid_grant`**: la app OAuth está en modo *Testing* → Google revoca el refresh token cada 7 días. Solución: Google Cloud Console → OAuth consent screen → **Publish app**, y regenerar `YOUTUBE_REFRESH_TOKEN`.
- **TikTok `spam_risk_too_many_pending_share`**: hay demasiados borradores en el Inbox. Publícalos o descártalos desde la app de TikTok.

### Pruebas locales
```bash
python -m pytest tests -q      # el prompt se construye sin errores
node check_tokens.js           # credenciales (requiere .env)
```
