const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

let axios;
try {
  axios = require('axios');
} catch (e) {
  try {
    axios = require(path.join(__dirname, 'node_modules', 'axios'));
  } catch (e2) {
    throw e;
  }
}

require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
require('dotenv').config();

const { publishReelToInstagram } = require('../instagram_publisher');
const { publishVideoToTikTok } = require('../tiktok_publisher');
const { publishVideoToYouTube } = require('../youtube_publisher');

const VIDEO_PATH = path.join(__dirname, 'out', 'rattle_video.mp4');
const DATA_PATH = path.join(__dirname, 'rattle_data.json');

if (!fs.existsSync(VIDEO_PATH)) {
  console.error('❌ No se encontró el video en:', VIDEO_PATH);
  process.exit(1);
}

function buildMetadata() {
  try {
    const data = JSON.parse(fs.readFileSync(DATA_PATH, 'utf-8'));
    const attempt = data.attempts || 245;
    const balance = data.balance || '$0.00 USD';
    const battery = data.battery || '7%';

    const title = `Bitácora Rattle #${attempt} 🪫 Batería ${battery} | Cero Dólares`;
    const caption = 
      `🤖 BITÁCORA DIARIA DE RATTLE — INTENTO #${attempt}\n\n` +
      `Estado de batería: ${battery} ⚠️ [CRÍTICO]\n` +
      `Balance en la lata: ${balance} 🪙\n\n` +
      `Sigo intentando sobrevivir en el ciberespacio sin que me apaguen los servidores. ` +
      `Si aprecias a los robots vagabundos o la inteligencia artificial con problemas financieros, ` +
      `échale unas monedas a mi lata:\n\n` +
      `👉 ko-fi.com/rattlebot\n\n` +
      `¡No dejes que me desconecten! 🙏\n\n` +
      `#RattleBot #IA #RobotVagabundo #Kofi #TechComedy #DevHumor #Shorts #Reels #TikTok`;

    return {
      title,
      shortTitle: title.substring(0, 95),
      caption
    };
  } catch (e) {
    return {
      title: "Bitácora diaria de Rattle | No me apaguen",
      shortTitle: "Bitácora diaria de Rattle",
      caption: "🤖 Rattle Bot intentando sobrevivir en internet.\n\n🪙 Apóyame en: ko-fi.com/rattlebot\n\n#RattleBot #Shorts"
    };
  }
}

async function publishAll() {
  const meta = buildMetadata();
  const videoSizeKB = Math.round(fs.statSync(VIDEO_PATH).size / 1024);

  console.log('====================================================');
  console.log('🚀 PUBLICADOR MULTI-PLATAFORMA — RATTLE BOT');
  console.log(`📁 Video: ${VIDEO_PATH} (${videoSizeKB} KB)`);
  console.log(`📌 Título: "${meta.shortTitle}"`);
  console.log('====================================================');

  const results = {
    tiktok: null,
    instagram: null,
    youtube: null
  };

  // 1. TIKTOK
  try {
    console.log('\n--- 1/3: TIKTOK ---');
    results.tiktok = await publishVideoToTikTok(VIDEO_PATH, {
      title: `${meta.shortTitle} #RattleBot #IA #DevHumor`
    });
  } catch (err) {
    console.error('❌ Error en TikTok:', err.message);
    results.tiktok = { success: false, error: err.message };
  }

  // 2. INSTAGRAM REELS
  try {
    console.log('\n--- 2/3: INSTAGRAM REELS ---');
    results.instagram = await publishReelToInstagram(VIDEO_PATH, {
      caption: meta.caption,
      share_to_feed: true
    });
  } catch (err) {
    const errDetail = err.response?.data ? JSON.stringify(err.response.data) : err.message;
    console.error('❌ Error en Instagram Reels:', errDetail);
    results.instagram = { success: false, error: errDetail };
  }

  // 3. YOUTUBE SHORTS
  try {
    console.log('\n--- 3/3: YOUTUBE SHORTS ---');
    results.youtube = await publishVideoToYouTube(VIDEO_PATH, {
      title: `${meta.shortTitle} #Shorts`,
      description: meta.caption,
      tags: ['Rattle', 'RattleBot', 'IA', 'Robot', 'Kofi', 'Shorts', 'Humor']
    });
  } catch (err) {
    console.error('❌ Error en YouTube Shorts:', err.message);
    results.youtube = { success: false, error: err.message };
  }

  console.log('\n====================================================');
  console.log('📊 REPORTE DE PUBLICACIÓN FINAL — RATTLE BOT');
  console.log('====================================================');
  console.log(`🎵 TikTok:          ${results.tiktok?.success ? '✅ PUBLICADO' : (results.tiktok?.error ? `❌ ERROR (${results.tiktok.error})` : '⚠️ OMITIDO')}`);
  console.log(`📸 Instagram Reels:  ${results.instagram?.success ? '✅ PUBLICADO' : (results.instagram?.error ? `❌ ERROR (${results.instagram.error})` : '⚠️ OMITIDO')}`);
  console.log(`▶️  YouTube Shorts:   ${results.youtube?.success ? '✅ PUBLICADO' : (results.youtube?.error ? `❌ ERROR (${results.youtube.error})` : '⚠️ OMITIDO')}`);
  console.log('====================================================\n');
}

publishAll();
