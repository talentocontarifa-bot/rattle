/**
 * check_tokens.js — Verifica las credenciales de TikTok, Instagram y YouTube SIN publicar nada.
 *
 * Uso local:   node check_tokens.js
 * En Actions:  workflow "Verificar credenciales" (manual)
 * Sale con código 1 si alguna credencial está rota.
 */
const path = require('path');
let axios;
try { axios = require('axios'); } catch (e) { axios = require(path.join(__dirname, 'video_rattle', 'node_modules', 'axios')); }
require('dotenv').config({ path: path.join(__dirname, '.env') });

const { describeAxiosError } = require('./http_errors');
const { getValidGoogleAccessToken } = require('./youtube_publisher');
const { getValidAccessToken, getCreatorInfo } = require('./tiktok_publisher');

async function checkYouTube() {
  const token = await getValidGoogleAccessToken();
  const res = await axios.get('https://www.googleapis.com/youtube/v3/channels', {
    params: { part: 'snippet', mine: true },
    headers: { Authorization: `Bearer ${token}` },
    timeout: 20000
  });
  const ch = res.data?.items?.[0];
  return ch ? `canal "${ch.snippet.title}"` : 'token válido (sin canal asociado?)';
}

async function checkTikTok() {
  const token = await getValidAccessToken();
  const info = await getCreatorInfo(token);
  if (!info) throw new Error('creator_info no respondió');
  const privacy = (info.privacy_level_options || []).join('/');
  return `@${info.creator_username} (privacidad permitida: ${privacy || 'n/d'})`;
}

async function checkInstagram() {
  const token = process.env.META_PAGE_ACCESS_TOKEN || process.env.META_USER_ACCESS_TOKEN;
  const igId = process.env.INSTAGRAM_ACCOUNT_ID;
  if (!token || !igId) throw new Error('Faltan META_PAGE_ACCESS_TOKEN o INSTAGRAM_ACCOUNT_ID');
  try {
    const res = await axios.get(`https://graph.facebook.com/v21.0/${igId}`, {
      params: { fields: 'username', access_token: token },
      timeout: 20000
    });
    let quota = '';
    try {
      const q = await axios.get(`https://graph.facebook.com/v21.0/${igId}/content_publishing_limit`, {
        params: { fields: 'quota_usage,config', access_token: token }, timeout: 20000
      });
      const d = q.data?.data?.[0];
      if (d) quota = ` · cuota 24h: ${d.quota_usage}/${d.config?.quota_total}`;
    } catch (_) { /* opcional */ }
    return `@${res.data.username}${quota}`;
  } catch (err) {
    throw new Error(describeAxiosError(err));
  }
}

(async () => {
  const checks = [['▶️  YouTube', checkYouTube], ['🎵 TikTok', checkTikTok], ['📸 Instagram', checkInstagram]];
  let failed = 0;
  for (const [name, fn] of checks) {
    try {
      console.log(`${name}: ✅ ${await fn()}`);
    } catch (err) {
      failed++;
      console.log(`${name}: ❌ ${err.message}`);
    }
  }
  process.exit(failed ? 1 : 0);
})();
