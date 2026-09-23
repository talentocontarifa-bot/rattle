import os
import sys
import platform
import subprocess
import tarfile
import zipfile
import urllib.request
import time
import socket
import logging

logger = logging.getLogger("obscura_manager")

OBSCURA_VERSION = "v0.2.3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")

DOWNLOAD_URLS = {
    "Windows": {
        "x86_64": f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/obscura-x86_64-windows.zip",
        "AMD64": f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/obscura-x86_64-windows.zip"
    },
    "Linux": {
        "x86_64": f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/obscura-x86_64-linux.tar.gz"
    },
    "Darwin": {
        "arm64": f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/obscura-aarch64-macos.tar.gz",
        "x86_64": f"https://github.com/h4ckf0r0day/obscura/releases/download/{OBSCURA_VERSION}/obscura-x86_64-macos.tar.gz"
    }
}

_cdp_process = None

def get_obscura_binary():
    """Devuelve la ruta al binario ejecutable de Obscura, descargándolo si no existe."""
    os.makedirs(BIN_DIR, exist_ok=True)
    system = platform.system()
    binary_name = "obscura.exe" if system == "Windows" else "obscura"
    binary_path = os.path.join(BIN_DIR, binary_name)

    # También verificar si está en PATH del sistema
    system_which = shutil_which(binary_name)
    if system_which and os.path.exists(system_which):
        return system_which

    if os.path.exists(binary_path):
        return binary_path

    # Descargar binario
    arch = platform.machine()
    urls_for_sys = DOWNLOAD_URLS.get(system, {})
    download_url = urls_for_sys.get(arch) or urls_for_sys.get("x86_64")

    if not download_url:
        print(f"[!] No hay binario pre-compilado de Obscura para {system} ({arch}).")
        return None

    print(f"[*] Descargando Obscura ({OBSCURA_VERSION}) para {system} [{arch}]...")
    archive_name = download_url.split("/")[-1]
    archive_path = os.path.join(BIN_DIR, archive_name)

    try:
        urllib.request.urlretrieve(download_url, archive_path)
        if archive_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(BIN_DIR)
        elif archive_name.endswith(".tar.gz") or archive_name.endswith(".tgz"):
            with tarfile.open(archive_path, "r:gz") as tar_ref:
                tar_ref.extractall(BIN_DIR)
        
        # Eliminar archivo comprimido temporal
        if os.path.exists(archive_path):
            os.remove(archive_path)

        # Permisos de ejecución en Linux / macOS
        if system != "Windows" and os.path.exists(binary_path):
            os.chmod(binary_path, 0o755)
            worker_path = os.path.join(BIN_DIR, "obscura-worker")
            if os.path.exists(worker_path):
                os.chmod(worker_path, 0o755)

        print(f"[OK] Obscura instalado exitosamente en: {binary_path}")
        return binary_path
    except Exception as e:
        print(f"[ERROR] Error descargando o extrayendo Obscura: {e}")
        return None

def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)

def is_port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

def start_obscura_cdp(port=9222, stealth=True):
    """Inicia el servidor CDP de Obscura en segundo plano si no está ya activo."""
    global _cdp_process
    if is_port_in_use(port):
        print(f"[INFO] Servidor CDP ya esta corriendo en el puerto {port}.")
        return True

    bin_path = get_obscura_binary()
    if not bin_path:
        print("[WARN] No se pudo iniciar CDP: binario de Obscura no disponible.")
        return False

    cmd = [bin_path, "serve", "--port", str(port)]
    if stealth:
        cmd.append("--stealth")

    try:
        # En Windows creationflags detached / en Linux start_new_session
        if platform.system() == "Windows":
            _cdp_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            _cdp_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

        # Esperar a que el puerto responda
        for _ in range(20):
            if is_port_in_use(port):
                print(f"[OK] Obscura CDP Server activo en http://127.0.0.1:{port}")
                return True
            time.sleep(0.2)

        print("[WARN] Obscura inicio pero el puerto no respondio a tiempo.")
        return False
    except Exception as e:
        print(f"[ERROR] Error iniciando Obscura CDP: {e}")
        return False

def stop_obscura_cdp():
    """Detiene el proceso del servidor CDP de Obscura si fue iniciado por este módulo."""
    global _cdp_process
    if _cdp_process and _cdp_process.poll() is None:
        try:
            _cdp_process.terminate()
            _cdp_process.wait(timeout=2)
            print("[INFO] Obscura CDP Server detenido.")
        except Exception:
            _cdp_process.kill()
        _cdp_process = None

def obscura_fetch(url, eval_js=None, stealth=True, timeout=30):
    """Realiza un fetch ultrarrápido (~80ms, ~30MB RAM) directamente con el CLI en Rust de Obscura."""
    bin_path = get_obscura_binary()
    if not bin_path:
        raise RuntimeError("Binario de Obscura no disponible.")

    cmd = [bin_path, "fetch", url]
    if eval_js:
        cmd.extend(["--eval", eval_js])
    if stealth:
        cmd.append("--stealth")

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"Obscura fetch falló: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()

def obscura_scrape(urls, eval_js=None, concurrency=10, timeout=60):
    """Scrapea múltiples URLs concurrentemente con Obscura en formato JSON."""
    bin_path = get_obscura_binary()
    if not bin_path:
        raise RuntimeError("Binario de Obscura no disponible.")

    if isinstance(urls, str):
        urls = [urls]

    cmd = [bin_path, "scrape"] + urls + ["--concurrency", str(concurrency), "--format", "json"]
    if eval_js:
        cmd.extend(["--eval", eval_js])

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"Obscura scrape falló: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()
