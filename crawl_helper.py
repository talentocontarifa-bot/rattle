import asyncio
import os
import sys
import threading
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
import obscura_manager

class CrawlResult:
    def __init__(self, success, markdown="", raw_html="", links=None, media=None, error=None):
        self.success = success
        self.markdown = markdown or ""
        self.raw_html = raw_html or ""
        self.links = links or []
        self.media = media or []
        self.error = error or ""

    def __repr__(self):
        status = "Success" if self.success else f"Failed ({self.error})"
        return f"<CrawlResult {status} length={len(self.markdown)}>"

def _run_async(coro):
    """Ejecuta una corrutina en un hilo aislado para evitar choques con loops de asyncio existentes."""
    result = None
    exception = None

    def target():
        nonlocal result, exception
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            try:
                loop.close()
            except Exception:
                pass

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=60)

    if exception:
        raise exception
    return result

def crawl_url(url, use_obscura=True, cdp_port=9222, css_selector=None, timeout=30):
    """
    Superpoder Crawl4AI: Extrae datos limpios y formateados en Markdown sin selectores CSS frágiles.
    
    Si use_obscura=True, conecta Crawl4AI directamente a la flota de navegadores ultraligeros
    de Obscura en Rust (~30 MB de RAM) vía CDP.
    """
    cdp_url = None
    if use_obscura:
        started = obscura_manager.start_obscura_cdp(port=cdp_port, stealth=True)
        if started:
            cdp_url = f"http://127.0.0.1:{cdp_port}"

    async def _async_crawl():
        if cdp_url:
            browser_cfg = BrowserConfig(cdp_url=cdp_url, verbose=False)
        else:
            browser_cfg = BrowserConfig(headless=True, verbose=False)

        run_cfg = CrawlerRunConfig(
            css_selector=css_selector,
            word_count_threshold=5,
            page_timeout=timeout * 1000,
            verbose=False
        )

        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                res = await crawler.arun(url=url, config=run_cfg)
                
                # Extraer enlaces limpios
                extracted_links = []
                if hasattr(res, 'links') and res.links:
                    if isinstance(res.links, dict):
                        for group, links in res.links.items():
                            for l in links:
                                extracted_links.append(l.get("href") or str(l))
                    elif isinstance(res.links, list):
                        for l in res.links:
                            extracted_links.append(l.get("href") if isinstance(l, dict) else str(l))

                return CrawlResult(
                    success=res.success,
                    markdown=res.markdown or "",
                    raw_html=res.html or "",
                    links=extracted_links,
                    error=res.error_message if not res.success else None
                )
        except Exception as e:
            # Fallback sin CDP si falló la conexión con Obscura
            if cdp_url:
                print(f"[WARN] Fallo al conectar con Obscura CDP ({e}). Intentando con motor estandar...")
                fallback_cfg = BrowserConfig(headless=True)
                async with AsyncWebCrawler(config=fallback_cfg) as crawler:
                    res = await crawler.arun(url=url, config=run_cfg)
                    return CrawlResult(
                        success=res.success,
                        markdown=res.markdown or "",
                        raw_html=res.html or "",
                        links=[],
                        error=res.error_message if not res.success else None
                    )
            raise e

    try:
        return _run_async(_async_crawl())
    except Exception as e:
        return CrawlResult(success=False, error=str(e))
