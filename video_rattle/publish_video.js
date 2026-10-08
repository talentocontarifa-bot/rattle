const fs = require('fs');
const path = require('path');

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

const VIDEO_PATH = process.env.RATTLE_VIDEO_PATH || path.join(__dirname, 'out', 'rattle_video.mp4');
const DATA_PATH = path.join(__dirname, 'rattle_data.json');
const RESULTS_PATH = path.join(__dirname, 'out', 'publish_results.json');

// PUBLISH_PLATFORMS=tiktok,instagram,youtube (por defecto todas)
const ENABLED = new Set(
  (process.env.PUBLISH_PLATFORMS || 'tiktok,instagram,youtube')
    .split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
);
// PUBLISH_STRICT=true → el job falla si CUALQUIER plataforma falla (por defecto: solo si fallan TODAS)
const STRICT = String(process.env.PUBLISH_STRICT || '').toLowerCase() === 'true';

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

    return { title, shortTitle: title.substring(0, 95), caption };
  } catch (e) {
    return {
      title: 'Bitácora diaria de Rattle | No me apaguen',
      shortTitle: 'Bitácora diaria de Rattle',
      caption: '🤖 Rattle Bot intentando sobrevivir en internet.\n\n🪙 Apóyame en: ko-fi.com/rattlebot\n\n#RattleBot #Shorts'
    };
  }
}

async function sendTelegram(text) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return;
  try {
    await axios.post(`https://api.telegram.org/bot${token}/sendMessage`, {
      chat_id: chatId,
      text: text.slice(0, 3900),
      disable_web_page_preview: true
    }, { timeout: 15000 });
  } catch (e) {
    console.warn('⚠️ No se pudo enviar el reporte a Telegram:', e.message);
  }
}

async function runPlatform(key, label, fn) {
  if (!ENABLED.has(key)) {
    console.log(`\n--- ${label}: OMITIDO (no está en PUBLISH_PLATFORMS) ---`);
    return { skipped: true };
  }
  console.log(`\n--- ${label} ---`);
  const t0 = Date.now();
  try {
    const res = await fn();
    const ok = !!res?.success;
    return { ...res, success: ok, seconds: Math.round((Date.now() - t0) / 1000), error: ok ? undefined : (res?.reason || res?.error || 'Sin detalle') };
  } catch (err) {
    console.error(`❌ Error en ${label}: ${err.message}`);
    return { success: false, error: err.message, seconds: Math.round((Date.now() - t0) / 1000) };
  }
}

function line(icon, name, r) {
  if (r.skipped) return `${icon} ${name}: ⚠️ OMITIDO`;
  if (r.success) {
    const extra = r.shortUrl || (r.mode === 'INBOX_DRAFT' ? 'borrador en la app (publícalo desde TikTok)' : '') || r.mediaId || '';
    return `${icon} ${name}: ✅ PUBLICADO ${extra}`.trim();
  }
  return `${icon} ${name}: ❌ ${r.error}`;
}

async function publishAll() {
  if (!fs.existsSync(VIDEO_PATH)) {
    const msg = `❌ No se encontró el video en: ${VIDEO_PATH}`;
    console.error(msg);
    await sendTelegram(`🚨 Rattle video: ${msg}`);
    process.exit(1);
  }

  const meta = buildMetadata();
  const videoSizeMB = (fs.statSync(VIDEO_PATH).size / (1024 * 1024)).toFixed(1);

  console.log('====================================================');
  console.log('🚀 PUBLICADOR MULTI-PLATAFORMA — RATTLE BOT');
  console.log(`📁 Video: ${VIDEO_PATH} (${videoSizeMB} MB)`);
  console.log(`📌 Título: "${meta.shortTitle}"`);
  console.log(`🎯 Plataformas: ${[...ENABLED].join(', ')}${STRICT ? ' (modo estricto)' : ''}`);
  console.log('====================================================');

  // Las plataformas son independientes: se publican en secuencia para no saturar la red del runner
  const results = {};
  results.tiktok = await runPlatform('tiktok', '1/3: TIKTOK', () => {
    const safeTikTokTitle = meta.shortTitle.replace(/[^\w\s#áéíóúÁÉÍÓÚñÑ.,:!|-]/g, '').replace(/\s+/g, ' ').trim().substring(0, 90);
    return publishVideoToTikTok(VIDEO_PATH, { title: safeTikTokTitle });
  });
  results.instagram = await runPlatform('instagram', '2/3: INSTAGRAM REELS', () =>
    publishReelToInstagram(VIDEO_PATH, { caption: meta.caption, share_to_feed: true })
  );
  results.youtube = await runPlatform('youtube', '3/3: YOUTUBE SHORTS', () =>
    publishVideoToYouTube(VIDEO_PATH, {
      title: `${meta.shortTitle} #Shorts`,
      description: meta.caption,
      tags: ['Rattle', 'RattleBot', 'IA', 'Robot', 'Kofi', 'Shorts', 'Humor']
    })
  );

  const report = [
    line('🎵', 'TikTok', results.tiktok),
    line('📸', 'Instagram', results.instagram),
    line('▶️', 'YouTube', results.youtube)
  ];

  console.log('\n====================================================');
  console.log('📊 REPORTE DE PUBLICACIÓN FINAL — RATTLE BOT');
  console.log('====================================================');
  report.forEach(l => console.log(l));
  console.log('====================================================\n');

  try {
    fs.mkdirSync(path.dirname(RESULTS_PATH), { recursive: true });
    fs.writeFileSync(RESULTS_PATH, JSON.stringify({ date: new Date().toISOString(), video_mb: Number(videoSizeMB), results }, null, 2));
  } catch (_) { /* opcional */ }

  const attempted = Object.values(results).filter(r => !r.skipped);
  const failed = attempted.filter(r => !r.success);
  const allFailed = attempted.length > 0 && failed.length === attempted.length;

  const runUrl = process.env.GITHUB_RUN_ID
    ? `\n\n🔗 ${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
    : '';
  const header = failed.length === 0 ? '✅ Rattle publicó su video diario'
    : allFailed ? '🚨 Rattle NO pudo publicar en ninguna red'
    : `⚠️ Rattle publicó con ${failed.length} error(es)`;
  await sendTelegram(`${header} (${videoSizeMB} MB)\n\n${report.join('\n')}${runUrl}`);

  if (failed.length > 0 && process.env.GITHUB_ACTIONS) {
    failed.forEach(r => console.log(`::warning title=Publicación fallida::${r.error}`));
  }

  if (allFailed || (STRICT && failed.length > 0)) {
    process.exit(1);
  }
}

publishAll().catch(async (err) => {
  console.error('💥 Error fatal en el publicador:', err);
  await sendTelegram(`🚨 Rattle: error fatal en el publicador: ${err.message}`);
  process.exit(1);
});
