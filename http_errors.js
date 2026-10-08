/**
 * http_errors.js
 * Utilidades compartidas por los publicadores (TikTok, Instagram, YouTube):
 *  - describeAxiosError: extrae el cuerpo real del error de la API (antes se perdía y solo
 *    veíamos "Request failed with status code 400").
 *  - withRetry: reintentos con backoff exponencial para errores transitorios.
 */

function describeAxiosError(err) {
  if (!err) return 'Error desconocido';
  const status = err.response?.status;
  const data = err.response?.data;
  let body = '';
  if (data !== undefined) {
    try {
      body = typeof data === 'string' ? data : JSON.stringify(data);
    } catch (_) {
      body = String(data);
    }
  }
  if (body.length > 800) body = body.slice(0, 800) + '…';
  if (status) return `HTTP ${status}${body ? ` → ${body}` : ''}`;
  return err.message || String(err);
}

function isTransient(err) {
  const status = err.response?.status;
  if (!status) return true; // red / timeout / ECONNRESET
  return status === 429 || status >= 500;
}

async function withRetry(fn, { retries = 3, baseDelayMs = 4000, label = 'operación', shouldRetry = isTransient } = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastErr = err;
      if (attempt === retries || !shouldRetry(err)) break;
      const wait = baseDelayMs * Math.pow(2, attempt - 1);
      console.warn(`⚠️ ${label} falló (intento ${attempt}/${retries}): ${describeAxiosError(err)}. Reintentando en ${wait / 1000}s...`);
      await new Promise(r => setTimeout(r, wait));
    }
  }
  throw lastErr;
}

module.exports = { describeAxiosError, withRetry, isTransient };
