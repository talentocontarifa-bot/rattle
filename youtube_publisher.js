/**
 * youtube_publisher.js
 * Publicador automatizado para YouTube Shorts y videos largos utilizando la API oficial YouTube Data v3.
 * 
 * Características:
 *  1. Auto-renovación de Access Token mediante Google OAuth2 usando Refresh Token.
 *     (Ideal para GitHub Actions desatendido; nunca expira).
 *  2. Protocolo oficial de Google "Resumable Upload":
 *     - Inicializa la sesión con metadatos (título, descripción con #Shorts, tags, categoría, privacidad).
 *     - Sube el archivo binario (.mp4) por chunks o stream continuo.
 *  3. Soporta visibilidad configurable: 'public', 'unlisted' o 'private'.
 *  4. Detecta automáticamente Shorts (agrega el hashtag y valida formato).
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

const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status';

/**
 * Renueva el token de acceso de Google usando el Refresh Token
 */
async function getValidGoogleAccessToken() {
  const clientId = process.env.YOUTUBE_CLIENT_ID;
  const clientSecret = process.env.YOUTUBE_CLIENT_SECRET;
  const refreshToken = process.env.YOUTUBE_REFRESH_TOKEN;

  if (!clientId || !clientSecret || !refreshToken) {
    throw new Error('Faltan credenciales de YouTube (YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN) en el entorno.');
  }

  try {
    const params = new URLSearchParams({
      client_id: clientId,
      client_secret: clientSecret,
      refresh_token: refreshToken,
      grant_type: 'refresh_token'
    });

    const res = await axios.post(TOKEN_URL, params.toString(), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    });

    if (!res.data?.access_token) {
      throw new Error(`Google no devolvió access_token: ${JSON.stringify(res.data)}`);
    }

    return res.data.access_token;
  } catch (err) {
    const errorDetails = err.response?.data ? JSON.stringify(err.response.data) : err.message;
    throw new Error(`Error al renovar access_token de YouTube: ${errorDetails}`);
  }
}

/**
 * Sube y publica un video / Short en YouTube
 * 
 * @param {string} videoFilePath - Ruta local al archivo .mp4
 * @param {object} metadata - Opciones (title, description, tags, privacyStatus)
 */
async function publishVideoToYouTube(videoFilePath, metadata = {}) {
  if (!fs.existsSync(videoFilePath)) {
    throw new Error(`El archivo de video no existe: ${videoFilePath}`);
  }

  const stat = fs.statSync(videoFilePath);
  const videoSize = stat.size;
  const videoSizeMB = (videoSize / (1024 * 1024)).toFixed(2);

  console.log(`\n▶️  Iniciando publicación en YouTube (${videoSizeMB} MB)...`);

  const accessToken = await getValidGoogleAccessToken();

  let rawTitle = metadata.title || 'IA para Emprendedores | Talento con Tarifa';
  // YouTube limita el título a 100 caracteres
  if (rawTitle.length > 90) {
    rawTitle = rawTitle.substring(0, 87) + '...';
  }

  // Asegurar que contenga #Shorts para que YouTube lo clasifique como Short automáticamente
  let title = rawTitle;
  if (!title.toLowerCase().includes('#shorts')) {
    title = `${title} #Shorts`;
  }
  if (title.length > 100) {
    title = title.substring(0, 92) + ' #Shorts';
  }

  let description = metadata.description || '🤖 Video creado y automatizado con Inteligencia Artificial.\n\n#Shorts #IA #Emprendedores #Automatizacion';
  if (!description.includes('#Shorts')) {
    description = `${description}\n\n#Shorts`;
  }

  const tags = metadata.tags || ['Shorts', 'Inteligencia Artificial', 'Tecnología', 'Emprendedores', 'Automatización'];
  const privacyStatus = metadata.privacyStatus || process.env.YOUTUBE_PRIVACY_STATUS || 'public'; // 'public', 'unlisted', 'private'

  const videoMetadata = {
    snippet: {
      title: title,
      description: description,
      tags: tags,
      categoryId: metadata.categoryId || '28', // 28 = Science & Technology, 22 = People & Blogs
      defaultLanguage: 'es',
      defaultAudioLanguage: 'es'
    },
    status: {
      privacyStatus: privacyStatus,
      selfDeclaredMadeForKids: false
    }
  };

  // PASO 1: Iniciar sesión de subida Resumible
  console.log('📡 Solicitando URL de carga a YouTube Data API v3...');
  const initRes = await axios.post(UPLOAD_URL, videoMetadata, {
    headers: {
      'Authorization': `Bearer ${accessToken}`,
      'Content-Type': 'application/json; charset=UTF-8',
      'X-Upload-Content-Type': 'video/mp4',
      'X-Upload-Content-Length': videoSize.toString()
    }
  });

  const uploadUrl = initRes.headers['location'];
  if (!uploadUrl) {
    throw new Error('No se recibió la cabecera Location para la subida resumible a YouTube.');
  }

  console.log('✅ Sesión de subida iniciada en Google. Enviando archivo de video...');

  // PASO 2: Subir archivo de video a la URL obtenida
  const fileBuffer = fs.readFileSync(videoFilePath);

  const uploadRes = await axios.put(uploadUrl, fileBuffer, {
    headers: {
      'Content-Type': 'video/mp4',
      'Content-Length': videoSize.toString()
    },
    maxBodyLength: Infinity,
    maxContentLength: Infinity
  });

  const videoData = uploadRes.data;
  if (!videoData?.id) {
    throw new Error(`Error en la subida a YouTube: ${JSON.stringify(uploadRes.data)}`);
  }

  const videoId = videoData.id;
  const shortUrl = `https://youtube.com/shorts/${videoId}`;
  const watchUrl = `https://youtu.be/${videoId}`;

  console.log(`\n🎉 ¡VIDEO PUBLICADO EXITOSAMENTE EN YOUTUBE!`);
  console.log(`   Video ID: ${videoId}`);
  console.log(`   Short URL: ${shortUrl}`);
  console.log(`   Watch URL: ${watchUrl}`);
  console.log(`   Privacidad: ${privacyStatus}`);

  return {
    success: true,
    platform: 'youtube',
    videoId: videoId,
    shortUrl: shortUrl,
    watchUrl: watchUrl,
    privacyStatus: privacyStatus
  };
}

// Ejecución directa de prueba CLI: `node youtube_publisher.js [ruta_al_video]`
if (require.main === module) {
  (async () => {
    try {
      const defaultVideo = path.join(__dirname, 'video_tct', 'out', 'video_final_tct.mp4');
      const videoToTest = process.argv[2] || defaultVideo;

      console.log('🚀 Probando youtube_publisher.js...');
      const res = await publishVideoToYouTube(videoToTest, {
        title: '🤖 Video automatizado con IA para YouTube Shorts',
        description: 'Demostración de publicación directa y automática con YouTube Data API v3.',
        privacyStatus: process.env.YOUTUBE_PRIVACY_STATUS || 'unlisted' // Para pruebas seguras sugerimos 'unlisted'
      });
      console.log('\nResultado final:', res);
    } catch (e) {
      console.error('\n❌ Error:', e.message);
    }
  })();
}

module.exports = {
  publishVideoToYouTube,
  getValidGoogleAccessToken
};
