/**
 * tiktok_publisher.js
 * Publicador automatizado para TikTok utilizando la API oficial de TikTok (v2).
 * 
 * Soporta:
 *  1. Auto-renovación de Access Token usando Refresh Token si está expirado o cerca de expirar.
 *  2. Consulta de permisos y privacidad del creador (/v2/post/publish/creator_info/query/).
 *  3. Publicación directa en el feed (/v2/post/publish/video/init/).
 *  4. Fallback inteligente a Bandeja de Creador / Borrador (/v2/post/publish/inbox/video/init/)
 *     si la app en Sandbox requiere cuenta privada o aprobación para feed público.
 *  5. Subida de archivo binario al CDN de TikTok.
 *  6. Monitoreo y sondeo del estado de publicación (/v2/post/publish/status/fetch/).
 */

const fs = require('fs');
const path = require('path');
let axios;
try {
  axios = require('axios');
} catch (e) {
  try {
    axios = require(path.join(__dirname, 'video_rattle', 'node_modules', 'axios'));
  } catch (e2) {
    try {
      axios = require(path.join(__dirname, 'video_pet', 'node_modules', 'axios'));
    } catch (e3) {
      try {
        axios = require(path.join(__dirname, 'video_tct', 'node_modules', 'axios'));
      } catch (e4) {
        throw e;
      }
    }
  }
}
require('dotenv').config({ path: path.join(__dirname, '.env') });
const { execFileSync } = require('child_process');
const { describeAxiosError, withRetry } = require('./http_errors');

const TOKEN_FILE = path.join(__dirname, 'tiktok_tokens.json');
const ENV_FILE = path.join(__dirname, '.env');

// Límites oficiales de TikTok para FILE_UPLOAD
const MIN_CHUNK = 5 * 1024 * 1024;
const MAX_CHUNK = 64 * 1024 * 1024;

// Mensajes accionables para los errores más comunes de la API
const TIKTOK_ERROR_HINTS = {
  spam_risk_too_many_pending_share: 'Hay demasiados borradores pendientes en el Inbox de TikTok. Abre la app de TikTok y publica o descarta los borradores de Rattle.',
  spam_risk_too_many_posts: 'TikTok alcanzó el límite diario de publicaciones para esta cuenta.',
  spam_risk_user_banned_from_posting: 'La cuenta tiene bloqueada la publicación vía API.',
  scope_not_authorized: 'El token no tiene el scope necesario (video.upload / video.publish). Re-autoriza la app.',
  access_token_invalid: 'Access token inválido; revisa TIKTOK_REFRESH_TOKEN.',
  unaudited_client_can_only_post_to_private_accounts: 'La app no está auditada: solo puede publicar en cuentas privadas (se usa Inbox como fallback).',
  invalid_params: 'Parámetros inválidos (revisa tamaño/chunks del video).'
};

function tiktokErrorSummary(err) {
  const code = err.response?.data?.error?.code;
  const hint = code && TIKTOK_ERROR_HINTS[code] ? ` 💡 ${TIKTOK_ERROR_HINTS[code]}` : '';
  return `${describeAxiosError(err)}${hint}`;
}

/**
 * En GitHub Actions el runner es efímero: si TikTok rota el refresh_token, hay que
 * guardarlo de vuelta en los Secrets o la siguiente corrida usará uno viejo.
 * Requiere el secret GH_SECRETS_PAT (PAT con permiso "Secrets: read/write" del repo).
 */
function persistGithubSecret(name, value) {
  const pat = process.env.GH_SECRETS_PAT;
  const repo = process.env.GITHUB_REPOSITORY;
  if (!pat || !repo || !value) return false;
  try {
    execFileSync('gh', ['secret', 'set', name, '--repo', repo], {
      input: value,
      env: { ...process.env, GH_TOKEN: pat },
      stdio: ['pipe', 'ignore', 'pipe']
    });
    console.log(`🔐 Secret ${name} actualizado en GitHub.`);
    return true;
  } catch (e) {
    console.warn(`⚠️ No se pudo actualizar el secret ${name}: ${e.message}`);
    return false;
  }
}

/**
 * Carga las credenciales de TikTok.
 */
function getTokens() {
  if (fs.existsSync(TOKEN_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf-8'));
    } catch (e) {
      console.warn('⚠️ Error al leer tiktok_tokens.json, usando variables de entorno.');
    }
  }

  return {
    access_token: process.env.TIKTOK_ACCESS_TOKEN,
    refresh_token: process.env.TIKTOK_REFRESH_TOKEN,
    open_id: process.env.TIKTOK_OPEN_ID
  };
}

/**
 * Guarda los tokens tanto en tiktok_tokens.json como en el archivo .env
 */
function saveTokens(tokenData) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(tokenData, null, 2), 'utf-8');

  if (fs.existsSync(ENV_FILE)) {
    let envContent = fs.readFileSync(ENV_FILE, 'utf-8');
    const updates = {
      TIKTOK_ACCESS_TOKEN: tokenData.access_token,
      TIKTOK_REFRESH_TOKEN: tokenData.refresh_token,
      TIKTOK_OPEN_ID: tokenData.open_id
    };

    for (const [key, val] of Object.entries(updates)) {
      if (!val) continue;
      const regex = new RegExp(`^${key}=.*$`, 'm');
      if (regex.test(envContent)) {
        envContent = envContent.replace(regex, `${key}=${val}`);
      } else {
        envContent += `\n${key}=${val}`;
      }
    }
    fs.writeFileSync(ENV_FILE, envContent.trim() + '\n', 'utf-8');
  }
}

/**
 * Renueva el access_token usando el refresh_token.
 */
async function refreshAccessToken() {
  const tokens = getTokens();
  const clientKey = process.env.TIKTOK_CLIENT_KEY;
  const clientSecret = process.env.TIKTOK_CLIENT_SECRET;

  if (!tokens.refresh_token || !clientKey || !clientSecret) {
    throw new Error('Faltan credenciales para refrescar el token de TikTok.');
  }

  console.log('🔄 Renovando access_token de TikTok con refresh_token...');
  const params = new URLSearchParams({
    client_key: clientKey,
    client_secret: clientSecret,
    grant_type: 'refresh_token',
    refresh_token: tokens.refresh_token
  });

  let response;
  try {
    response = await axios.post('https://open.tiktokapis.com/v2/oauth/token/', params.toString(), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      timeout: 30000
    });
  } catch (err) {
    throw new Error(`No se pudo renovar el token de TikTok: ${describeAxiosError(err)}`);
  }

  const data = response.data;
  if (!data.access_token && !data.data?.access_token) {
    throw new Error(`Error en respuesta de refresh: ${JSON.stringify(data)}`);
  }

  const newTokens = {
    access_token: data.access_token || data.data.access_token,
    refresh_token: data.refresh_token || data.data.refresh_token || tokens.refresh_token,
    open_id: data.open_id || data.data?.open_id || tokens.open_id,
    expires_in: data.expires_in || data.data?.expires_in,
    scope: data.scope || data.data?.scope,
    updated_at: new Date().toISOString()
  };

  saveTokens(newTokens);
  if (newTokens.refresh_token && newTokens.refresh_token !== tokens.refresh_token) {
    console.log('🔁 TikTok rotó el refresh_token.');
    if (!persistGithubSecret('TIKTOK_REFRESH_TOKEN', newTokens.refresh_token) && process.env.GITHUB_ACTIONS) {
      console.warn('⚠️ El nuevo refresh_token NO se guardó en GitHub Secrets (falta GH_SECRETS_PAT). La próxima corrida podría fallar.');
    }
  }
  console.log('✅ Token de TikTok renovado exitosamente.');
  return newTokens.access_token;
}

/**
 * Obtiene un access_token válido (lo renueva si está ausente o expirado).
 */
async function getValidAccessToken() {
  let tokens = getTokens();
  if (!tokens.access_token && tokens.refresh_token) {
    return await refreshAccessToken();
  }
  if (!tokens.access_token) {
    throw new Error('No se encontró TIKTOK_ACCESS_TOKEN en .env ni tiktok_tokens.json');
  }

  // Comprobar validez contra TikTok API
  try {
    const res = await axios.post('https://open.tiktokapis.com/v2/post/publish/creator_info/query/', {}, {
      headers: {
        'Authorization': `Bearer ${tokens.access_token}`,
        'Content-Type': 'application/json; charset=UTF-8'
      }
    });
    if (res.data?.data) {
      return tokens.access_token;
    }
  } catch (err) {
    const errCode = err.response?.data?.error?.code;
    if (err.response?.status === 401 || errCode === 'access_token_invalid') {
      console.log('🔄 Token expirado detectado en comprobación inicial. Renovando...');
      return await refreshAccessToken();
    }
  }

  return tokens.access_token;
}

/**
 * Consulta la información del creador para verificar límites y permisos.
 */
async function getCreatorInfo(accessToken) {
  try {
    const res = await axios.post('https://open.tiktokapis.com/v2/post/publish/creator_info/query/', {}, {
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'Content-Type': 'application/json; charset=UTF-8'
      }
    });
    return res.data?.data || null;
  } catch (err) {
    console.warn('⚠️ No se pudo consultar creator_info:', err.response?.data || err.message);
    return null;
  }
}

/**
 * Publica un video en TikTok (intenta Direct Post primero, y fallback a Inbox si la cuenta es pública en sandbox).
 * 
 * @param {string} videoFilePath - Ruta absoluta al archivo .mp4
 * @param {object} options - Opciones adicionales (title, privacy_level, etc.)
 */
async function publishVideoToTikTok(videoFilePath, options = {}) {
  if (!fs.existsSync(videoFilePath)) {
    throw new Error(`El archivo de video no existe: ${videoFilePath}`);
  }

  const stat = fs.statSync(videoFilePath);
  const videoSize = stat.size;
  const videoSizeMB = (videoSize / (1024 * 1024)).toFixed(2);

  console.log(`\n🎵 Iniciando publicación en TikTok (${videoSizeMB} MB)...`);
  let accessToken = await getValidAccessToken();

  // Verificar información del creador
  const creatorInfo = await getCreatorInfo(accessToken);
  if (creatorInfo) {
    console.log(`👤 Creador conectado: @${creatorInfo.creator_username} (${creatorInfo.creator_nickname})`);
  }

  const title = options.title || 'Talento con Tarifa #TalentoConTarifa #InteligenciaArtificial #Emprendedores';
  const privacyLevel = options.privacy_level || 'SELF_ONLY';

  // Calcular chunks según las reglas de TikTok:
  //  - Videos <= 64MB: un solo chunk del tamaño exacto.
  //  - Más grandes: chunks de 10MB; el último absorbe el resto (total = floor(size / chunk)).
  let chunkSize = videoSize;
  let totalChunks = 1;
  if (videoSize > MAX_CHUNK) {
    chunkSize = 10 * 1024 * 1024;
    totalChunks = Math.floor(videoSize / chunkSize);
  }
  const sourceInfo = {
    source: 'FILE_UPLOAD',
    video_size: videoSize,
    chunk_size: chunkSize,
    total_chunk_count: totalChunks
  };

  const authHeaders = () => ({
    'Authorization': `Bearer ${accessToken}`,
    'Content-Type': 'application/json; charset=UTF-8'
  });

  let mode = 'DIRECT_POST';
  let initUrl = 'https://open.tiktokapis.com/v2/post/publish/video/init/';
  let initPayload = {
    post_info: {
      title: title.substring(0, 2200),
      privacy_level: privacyLevel,
      disable_duet: options.disable_duet ?? false,
      disable_stitch: options.disable_stitch ?? false,
      disable_comment: options.disable_comment ?? false,
      video_cover_timestamp_ms: options.video_cover_timestamp_ms ?? 1000
    },
    source_info: sourceInfo
  };

  console.log(`📡 Solicitando URL de carga a TikTok API (Direct Post, ${totalChunks} chunk/s)...`);
  let initRes;
  try {
    initRes = await axios.post(initUrl, initPayload, { headers: authHeaders(), timeout: 60000 });
  } catch (err) {
    let errCode = err.response?.data?.error?.code;
    let status = err.response?.status;

    // Si el token expiró, renovar y reintentar
    if (status === 401 || errCode === 'access_token_invalid') {
      console.log('⚠️ Token inválido o expirado. Renovando token...');
      accessToken = await refreshAccessToken();
      try {
        initRes = await axios.post(initUrl, initPayload, { headers: authHeaders(), timeout: 60000 });
      } catch (retryErr) {
        err = retryErr;
        errCode = err.response?.data?.error?.code;
        status = err.response?.status;
      }
    }

    // Si la llamada directa falló por ser Sandbox / cuenta no auditada (403 o error_code)
    if (!initRes) {
      if (errCode === 'unaudited_client_can_only_post_to_private_accounts' || status === 403) {
        console.log(`ℹ️ Direct Post no permitido (${errCode || status}). Usando Creator Inbox (borrador en la app de TikTok)...`);
        mode = 'INBOX_DRAFT';
        initUrl = 'https://open.tiktokapis.com/v2/post/publish/inbox/video/init/';
        initPayload = { source_info: sourceInfo };

        try {
          initRes = await axios.post(initUrl, initPayload, { headers: authHeaders(), timeout: 60000 });
        } catch (inboxErr) {
          throw new Error(`TikTok Inbox init falló: ${tiktokErrorSummary(inboxErr)}`);
        }
      } else {
        throw new Error(`TikTok init falló: ${tiktokErrorSummary(err)}`);
      }
    }
  }

  const initData = initRes.data?.data;
  if (!initData || !initData.upload_url) {
    throw new Error(`Respuesta inesperada al inicializar: ${JSON.stringify(initRes.data)}`);
  }

  const publishId = initData.publish_id;
  const uploadUrl = initData.upload_url;
  console.log(`✅ Sesión de subida creada [${mode}]. Publish ID: ${publishId}`);

  // Subir el video (por chunks si hace falta), con reintentos por chunk
  console.log('📤 Subiendo video a los servidores de TikTok...');
  const fileBuffer = fs.readFileSync(videoFilePath);
  for (let i = 0; i < totalChunks; i++) {
    const start = i * chunkSize;
    const end = i === totalChunks - 1 ? videoSize - 1 : start + chunkSize - 1;
    const chunk = fileBuffer.subarray(start, end + 1);
    try {
      await withRetry(
        () => axios.put(uploadUrl, chunk, {
          headers: {
            'Content-Type': 'video/mp4',
            'Content-Length': chunk.length.toString(),
            'Content-Range': `bytes ${start}-${end}/${videoSize}`
          },
          maxBodyLength: Infinity,
          maxContentLength: Infinity,
          timeout: 5 * 60 * 1000
        }),
        { label: `Chunk ${i + 1}/${totalChunks} TikTok` }
      );
    } catch (err) {
      throw new Error(`Subida a TikTok falló en chunk ${i + 1}/${totalChunks}: ${tiktokErrorSummary(err)}`);
    }
    if (totalChunks > 1) console.log(`   ✓ Chunk ${i + 1}/${totalChunks}`);
  }

  console.log('✅ Archivo binario cargado con éxito en TikTok CDN. Verificando estado...');

  // Monitorear el estado de publicación
  return await pollPublishStatus(accessToken, publishId, mode);
}

/**
 * Consulta el estado de procesamiento del video.
 */
async function pollPublishStatus(accessToken, publishId, mode, maxAttempts = 15, delayMs = 3000) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await new Promise(r => setTimeout(r, delayMs));

    try {
      const res = await axios.post('https://open.tiktokapis.com/v2/post/publish/status/fetch/', {
        publish_id: publishId
      }, {
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Content-Type': 'application/json; charset=UTF-8'
        }
      });

      const statusData = res.data?.data;
      const status = statusData?.status;
      console.log(`⏳ [Intento ${attempt}/${maxAttempts}] Estado en TikTok: ${status}`);

      if (status === 'SUCCESS' || status === 'PUBLISH_COMPLETE') {
        console.log('\n🎉 ¡VIDEO PUBLICADO EXITOSAMENTE EN TIKTOK!');
        return { success: true, mode, status, publishId, statusData };
      }

      if (status === 'SEND_TO_USER_INBOX') {
        console.log('\n🎉 ¡VIDEO ENVIADO CON ÉXITO A LA BANDEJA DE TIKTOK!');
        console.log('📱 Ya está disponible en la app de TikTok de @' + (statusData?.creator_username || 'tu cuenta') + ' listo para publicar.');
        return { success: true, mode, status, publishId, statusData };
      }

      if (status === 'FAILED') {
        const reason = statusData?.fail_reason || 'Desconocido';
        console.error(`❌ Falló el procesamiento del video en TikTok: ${reason}`);
        return { success: false, mode, status, reason, publishId };
      }
    } catch (err) {
      console.warn(`⚠️ Error temporal al consultar estado: ${err.message}`);
    }
  }

  console.log('ℹ️ El video continúa procesándose en segundo plano en TikTok.');
  return { success: true, mode, status: 'PROCESSING', publishId };
}

// Ejecución directa por CLI: `node tiktok_publisher.js`
if (require.main === module) {
  (async () => {
    try {
      const defaultVideo = path.join(__dirname, 'video_tct', 'public', 'video_final_tct.mp4');
      const videoToTest = process.argv[2] || defaultVideo;

      console.log('🚀 Probando tiktok_publisher.js...');
      const result = await publishVideoToTikTok(videoToTest, {
        title: '🤖 Video automatizado con Inteligencia Artificial por Talento con Tarifa. #TalentoConTarifa #IA #Automatizacion',
        privacy_level: 'SELF_ONLY'
      });
      console.log('\nResultado final:', result);
    } catch (e) {
      console.error('Error fatal:', e.message);
    }
  })();
}

module.exports = {
  publishVideoToTikTok,
  getCreatorInfo,
  getValidAccessToken,
  refreshAccessToken
};
