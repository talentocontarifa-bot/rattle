/**
 * instagram_publisher.js
 * Publicador automatizado para Instagram Reels utilizando la API oficial de Meta Graph (v21.0).
 * 
 * Utiliza el protocolo de "Resumable Upload" (subida directa de binario) de Meta:
 *  1. Inicializa el contenedor del Reel (upload_type=resumable, media_type=REELS).
 *  2. Sube el archivo de video binario (.mp4) directamente a los servidores de Meta.
 *  3. Monitorea y sondea el procesamiento del Reel hasta que el estado sea FINISHED.
 *  4. Publica el Reel en el feed y en la pestaña de Reels.
 * 
 * No requiere hosting público externo ni almacenamiento en S3.
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

const GRAPH_API_VERSION = 'v21.0';

/**
 * Obtiene el ID de la cuenta de Instagram Business conectada a la página de Facebook si no está en .env
 */
async function getInstagramAccountId(pageId, accessToken) {
  if (process.env.INSTAGRAM_ACCOUNT_ID) {
    return process.env.INSTAGRAM_ACCOUNT_ID;
  }

  if (!pageId || !accessToken) return null;

  try {
    const url = `https://graph.facebook.com/${GRAPH_API_VERSION}/${pageId}?fields=instagram_business_account&access_token=${accessToken}`;
    const res = await axios.get(url);
    if (res.data?.instagram_business_account?.id) {
      return res.data.instagram_business_account.id;
    }
  } catch (err) {
    console.warn('⚠️ No se pudo obtener automáticamente el instagram_business_account_id:', err.response?.data?.error?.message || err.message);
  }
  return null;
}

/**
 * Publica un video como Reel en Instagram
 * 
 * @param {string} videoFilePath - Ruta local al archivo .mp4
 * @param {object} options - Opciones adicionales (caption, share_to_feed, cover_url)
 */
async function publishReelToInstagram(videoFilePath, options = {}) {
  const pageId = process.env.META_PAGE_ID;
  const accessToken = process.env.META_PAGE_ACCESS_TOKEN || process.env.META_USER_ACCESS_TOKEN;

  if (!accessToken) {
    throw new Error('Falta META_PAGE_ACCESS_TOKEN en las variables de entorno.');
  }

  const igAccountId = await getInstagramAccountId(pageId, accessToken);
  if (!igAccountId) {
    throw new Error('No se encontró INSTAGRAM_ACCOUNT_ID ni se pudo deducir de META_PAGE_ID. Asegúrate de vincular tu cuenta de Instagram profesional a tu página de Facebook.');
  }

  if (!fs.existsSync(videoFilePath)) {
    throw new Error(`El archivo de video no existe: ${videoFilePath}`);
  }

  const stat = fs.statSync(videoFilePath);
  const videoSize = stat.size;
  const videoSizeMB = (videoSize / (1024 * 1024)).toFixed(2);
  const caption = options.caption || '🤖 Video creado automáticamente con Inteligencia Artificial #Reels #IA';
  const shareToFeed = options.share_to_feed !== false; // por defecto true

  console.log(`\n📸 Iniciando publicación de Instagram Reel en cuenta ID: ${igAccountId} (${videoSizeMB} MB)...`);

  // PASO 1: Crear la sesión de subida resumible
  console.log('📡 Creando contenedor de Reel (Resumable Upload)...');
  const initUrl = `https://graph.facebook.com/${GRAPH_API_VERSION}/${igAccountId}/media`;
  
  const postData = new URLSearchParams();
  postData.append('media_type', 'REELS');
  postData.append('upload_type', 'resumable');
  postData.append('caption', caption);
  postData.append('share_to_feed', shareToFeed.toString());
  postData.append('access_token', accessToken);

  let initRes;
  try {
    initRes = await axios.post(initUrl, postData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    });
  } catch (err) {
    const errorDetails = err.response?.data ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ Error detallado de Meta al crear contenedor de IG:', errorDetails);
    throw new Error(`Error de Meta al crear contenedor de IG: ${errorDetails}`);
  }

  if (!initRes.data?.id || !initRes.data?.uri) {
    throw new Error(`Respuesta inválida al crear contenedor de IG: ${JSON.stringify(initRes.data)}`);
  }

  const containerId = initRes.data.id;
  const uploadUri = initRes.data.uri;
  console.log(`✅ Contenedor de Reel creado con éxito. ID: ${containerId}`);

  // PASO 2: Subir el archivo de video binario a Meta CDN
  console.log('📤 Subiendo video a los servidores de Instagram...');
  const videoBuffer = fs.readFileSync(videoFilePath);

  await axios.post(uploadUri, videoBuffer, {
    headers: {
      'Authorization': `OAuth ${accessToken}`,
      'Offset': '0',
      'offset': '0',
      'file_size': videoSize.toString(),
      'Content-Type': 'application/octet-stream',
      'Content-Length': videoSize.toString()
    },
    maxBodyLength: Infinity,
    maxContentLength: Infinity
  });

  console.log('✅ Video cargado con éxito en Meta CDN. Esperando codificación...');

  // PASO 3: Sondear hasta que el estado sea FINISHED
  await pollInstagramContainerStatus(containerId, accessToken);

  // PASO 4: Publicar el Reel
  console.log('🚀 Publicando Reel en Instagram...');
  const publishUrl = `https://graph.facebook.com/${GRAPH_API_VERSION}/${igAccountId}/media_publish`;
  
  const publishData = new URLSearchParams();
  publishData.append('creation_id', containerId);
  publishData.append('access_token', accessToken);

  let pubRes;
  try {
    pubRes = await axios.post(publishUrl, publishData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    });
  } catch (err) {
    const errorDetails = err.response?.data ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ Error detallado al publicar Reel:', errorDetails);
    throw new Error(`Error de Meta en media_publish: ${errorDetails}`);
  }

  const mediaId = pubRes.data?.id;
  if (!mediaId) {
    throw new Error(`Error en media_publish de Instagram: ${JSON.stringify(pubRes.data)}`);
  }

  console.log(`\n🎉 ¡REEL PUBLICADO EXITOSAMENTE EN INSTAGRAM!`);
  console.log(`   Media ID: ${mediaId}`);
  console.log(`   Revisa tu perfil en Instagram.`);

  return {
    success: true,
    platform: 'instagram',
    mediaId: mediaId,
    containerId: containerId
  };
}

/**
 * Espera y verifica que Instagram termine de procesar el video antes de publicar
 */
async function pollInstagramContainerStatus(containerId, accessToken, maxAttempts = 30, delayMs = 5000) {
  const statusUrl = `https://graph.facebook.com/${GRAPH_API_VERSION}/${containerId}`;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await new Promise(r => setTimeout(r, delayMs));

    try {
      const res = await axios.get(statusUrl, {
        params: {
          fields: 'status_code,status',
          access_token: accessToken
        }
      });

      const statusCode = res.data?.status_code;
      console.log(`⏳ [Intento ${attempt}/${maxAttempts}] Estado en Instagram: ${statusCode || 'VERIFICANDO'}`);

      if (statusCode === 'FINISHED') {
        console.log('✨ Video procesado por Instagram y listo para publicar.');
        return true;
      }

      if (statusCode === 'ERROR') {
        throw new Error(`Instagram reportó un error al procesar el video: ${JSON.stringify(res.data)}`);
      }

      if (statusCode === 'EXPIRED') {
        throw new Error('La sesión de carga del Reel ha expirado.');
      }
    } catch (err) {
      if (err.message.includes('Instagram reportó') || err.message.includes('expirado')) {
        throw err;
      }
      console.warn(`⚠️ Error al verificar estado de IG: ${err.response?.data?.error?.message || err.message}`);
    }
  }

  throw new Error('Tiempo de espera agotado: Instagram tardó demasiado en procesar el Reel.');
}

// Ejecución directa de prueba CLI: `node instagram_publisher.js [ruta_al_video]`
if (require.main === module) {
  (async () => {
    try {
      const defaultVideo = path.join(__dirname, 'video_tct', 'out', 'video_final_tct.mp4');
      const videoToTest = process.argv[2] || defaultVideo;

      console.log('🚀 Probando instagram_publisher.js...');
      const res = await publishReelToInstagram(videoToTest, {
        caption: '🤖 Prueba de Reel automatizado con IA. #TalentoConTarifa #InteligenciaArtificial #MarketingDigital'
      });
      console.log('\nResultado:', res);
    } catch (e) {
      console.error('\n❌ Error:', e.message);
    }
  })();
}

module.exports = {
  publishReelToInstagram,
  getInstagramAccountId
};
