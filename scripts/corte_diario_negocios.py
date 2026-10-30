#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Corte Diario Ejecutivo de Negocios (6:00 PM America/Cancun) -> @Rattle_diario_bot (Telegram)
Recopila métricas en tiempo real de:
  1. Stripe Live (Ventas del día, Balance, Sesiones de Checkout / Prospectos)
  2. DreamCrafters Web & Firestore (Radar Centinela, Tráfico hoy, Invitaciones, Socios, SPEI leads)
  3. Bots Publicadores & Redes Sociales (FB/IG DreamCrafters, TCT, PET, YouTube Shorts, TikTok)
  4. Consultoría Trello (Tablero MS Proyecto LCM)
"""

import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CANCUN_TZ = timezone(timedelta(hours=-5))


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15):
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": "DreamCraftersDailyReport/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def parse_fs_val(v):
    if not isinstance(v, dict):
        return v
    for k, val in v.items():
        if k in ("stringValue", "timestampValue", "booleanValue"):
            return val
        if k in ("integerValue", "doubleValue"):
            return float(val) if "." in str(val) else int(val)
        if k == "mapValue":
            return {mk: parse_fs_val(mv) for mk, mv in (val.get("fields") or {}).items()}
        if k == "arrayValue":
            return [parse_fs_val(av) for av in (val.get("values") or [])]
        if k == "nullValue":
            return None
    return str(v)


def get_fs_collection(project_id: str, col_name: str, page_size: int = 40) -> list:
    url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/{col_name}?pageSize={page_size}"
    data = http_get_json(url)
    if not isinstance(data, dict):
        return []
    docs = data.get("documents") or []
    parsed = []
    for d in docs:
        fields = {k: parse_fs_val(v) for k, v in (d.get("fields") or {}).items()}
        fields["_id"] = d.get("name", "").split("/")[-1]
        fields["_createTime"] = d.get("createTime")
        parsed.append(fields)
    return parsed


def build_daily_report(env: dict) -> str:
    now = datetime.now(CANCUN_TZ)
    today_str = now.strftime("%Y-%m-%d")
    start_today_ts = int(
        now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    start_24h_ts = int((now - timedelta(hours=24)).timestamp())

    # 1. STRIPE LIVE
    stripe_key = env.get("STRIPE_SECRET_KEY", "")
    sh = {"Authorization": f"Bearer {stripe_key}"}
    bal = http_get_json("https://api.stripe.com/v1/balance", sh)
    charges = http_get_json("https://api.stripe.com/v1/charges?limit=25", sh)
    sessions = http_get_json("https://api.stripe.com/v1/checkout/sessions?limit=20", sh)

    avail_mxn = 0.0
    pend_mxn = 0.0
    if isinstance(bal, dict):
        for b in bal.get("available") or []:
            if b.get("currency") == "mxn":
                avail_mxn += b.get("amount", 0) / 100.0
        for b in bal.get("pending") or []:
            if b.get("currency") == "mxn":
                pend_mxn += b.get("amount", 0) / 100.0

    paid_today_mxn = 0.0
    paid_today_count = 0
    if isinstance(charges, dict):
        for c in charges.get("data") or []:
            if c.get("paid") and c.get("created", 0) >= start_today_ts:
                paid_today_mxn += c.get("amount", 0) / 100.0
                paid_today_count += 1

    recent_prospects = []
    if isinstance(sessions, dict):
        for s in sessions.get("data") or []:
            if s.get("created", 0) >= start_24h_ts:
                meta = s.get("metadata") or {}
                prod = meta.get("product") or "invitacion"
                celeb = (
                    meta.get("celebrant_name")
                    or (s.get("customer_details") or {}).get("name")
                    or "Cliente"
                )
                amt = (s.get("amount_total") or 0) / 100.0
                pstat = s.get("payment_status", "unpaid")
                recent_prospects.append(f"{prod.upper()} (${amt:.0f} MXN - {celeb}: {pstat})")

    # 2. DREAMCRAFTERS RADAR & FIRESTORE
    tel = http_get_json("https://www.dreamcrafters.lat/api/telemetry")
    patrol = (tel.get("patrol") if isinstance(tel, dict) else None) or {}
    patrol_status = patrol.get("status", "ok").upper()
    metrics = patrol.get("metrics") or {}
    events = (tel.get("events") if isinstance(tel, dict) else None) or []
    events_24h = [e for e in events if str(e.get("createdAt", "")).startswith(today_str)]

    pv_today = sum(1 for e in events_24h if e.get("eventType") == "pageview")
    ic_today = sum(1 for e in events_24h if e.get("eventType") == "intent_click")

    project_id = env.get("FIREBASE_PROJECT_ID", "dreamcrafter-2946b")
    invs = get_fs_collection(project_id, "invitations", 30)
    invs_today = [
        d for d in invs if str(d.get("createdAt") or d.get("_createTime") or "").startswith(today_str)
    ]
    leads = get_fs_collection(project_id, "checkout_leads", 20)
    leads_today = [
        d for d in leads if str(d.get("createdAt") or d.get("_createTime") or "").startswith(today_str)
    ]
    affs = get_fs_collection(project_id, "affiliates", 30)

    # 3. REDES SOCIALES (IG, FB, YouTube, TikTok)
    dc_ig = http_get_json(
        f"https://graph.facebook.com/v21.0/{env.get('DREAMCRAFTERS_IG_ACCOUNT_ID')}/media?fields=id,timestamp&limit=10&access_token={env.get('DREAMCRAFTERS_META_PAGE_ACCESS_TOKEN')}"
    )
    dc_ig_today = sum(
        1
        for m in ((dc_ig.get("data") if isinstance(dc_ig, dict) else None) or [])
        if str(m.get("timestamp", "")).startswith(today_str)
    )

    tct_ig = http_get_json(
        f"https://graph.facebook.com/v21.0/{env.get('TCT_INSTAGRAM_ACCOUNT_ID')}/media?fields=id,timestamp&limit=10&access_token={env.get('TCT_META_PAGE_ACCESS_TOKEN')}"
    )
    tct_ig_today = sum(
        1
        for m in ((tct_ig.get("data") if isinstance(tct_ig, dict) else None) or [])
        if str(m.get("timestamp", "")).startswith(today_str)
    )

    # YouTube Shorts
    yt_total_views = 0
    yt_today_count = 0
    yt_top_today = ""
    try:
        tok_req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode(
                {
                    "client_id": env["YOUTUBE_CLIENT_ID"],
                    "client_secret": env["YOUTUBE_CLIENT_SECRET"],
                    "refresh_token": env["YOUTUBE_REFRESH_TOKEN"],
                    "grant_type": "refresh_token",
                }
            ).encode(),
        )
        yt_tok = json.loads(urllib.request.urlopen(tok_req, timeout=10).read().decode())[
            "access_token"
        ]
        yh = {"Authorization": f"Bearer {yt_tok}"}
        ch = http_get_json(
            "https://www.googleapis.com/youtube/v3/channels?part=statistics,contentDetails&mine=true",
            yh,
        )
        ch_item = (ch.get("items") or [{}])[0]
        yt_total_views = int((ch_item.get("statistics") or {}).get("viewCount", 0))
        uploads_id = (
            ((ch_item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        )
        if uploads_id:
            pl = http_get_json(
                f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&playlistId={uploads_id}&maxResults=10",
                yh,
            )
            vids = [x["contentDetails"]["videoId"] for x in (pl.get("items") or [])]
            if vids:
                vs = http_get_json(
                    f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={','.join(vids)}",
                    yh,
                )
                best_v = -1
                for v in vs.get("items") or []:
                    pub = str((v.get("snippet") or {}).get("publishedAt", ""))
                    v_views = int((v.get("statistics") or {}).get("viewCount", 0))
                    if pub.startswith(today_str):
                        yt_today_count += 1
                        if v_views > best_v:
                            best_v = v_views
                            yt_top_today = f"{(v.get('snippet') or {}).get('title', '')[:42]}... ({v_views} vistas)"
    except Exception:
        pass

    tk_today_str = env.get("TIKTOK_TODAY_COUNT", "").strip()
    tk_line = (
        f"  • <b>TikTok Studio (Puente Local):</b> {html.escape(tk_today_str)} videos publicados hoy\n"
        if tk_today_str
        else ""
    )

    # 4. TRELLO MS PROYECTO LCM
    trello_summary = "Sin cambios hoy"
    t_key = env.get("TRELLO_API_KEY")
    t_tok = env.get("TRELLO_TOKEN")
    b_id = env.get("TRELLO_LCM_BOARD_ID")
    if t_key and t_tok and b_id:
        lists = http_get_json(
            f"https://api.trello.com/1/boards/{b_id}/lists?cards=open&card_fields=name,dateLastActivity&key={t_key}&token={t_tok}"
        )
        if isinstance(lists, list):
            counts = []
            for lst in lists:
                lname = lst.get("name", "")
                c_len = len(lst.get("cards") or [])
                if "Por Hacer" in lname:
                    counts.append(f"Por hacer: {c_len}")
                elif "En Proceso" in lname:
                    counts.append(f"En proceso: {c_len}")
                elif "Completado" in lname:
                    counts.append(f"Completadas: {c_len}")
            if counts:
                trello_summary = " | ".join(counts)

    prospects_line = (
        ", ".join(recent_prospects[:3])
        if recent_prospects
        else "Sin sesiones nuevas en las últimas 24h"
    )
    yt_extra = f"\n  • Destacado hoy: <i>{html.escape(yt_top_today)}</i>" if yt_top_today else ""

    msg = (
        f"📊 <b>CORTE DIARIO DE NEGOCIOS — {now.strftime('%d/%m/%Y %I:%M %p')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>1. VENTAS Y STRIPE LIVE</b>\n"
        f"  • <b>Cobros cerrados hoy:</b> ${paid_today_mxn:,.2f} MXN ({paid_today_count} ventas)\n"
        f"  • <b>Balance Stripe:</b> ${avail_mxn:,.2f} MXN disp. / ${pend_mxn:,.2f} MXN pend.\n"
        f"  • <b>Checkouts (24h):</b> {html.escape(prospects_line)}\n"
        f"  • <b>Invitaciones creadas hoy:</b> {len(invs_today)} (Total: {len(invs)}) | <b>SPEI leads:</b> {len(leads_today)}\n"
        f"  • <b>Socios activos:</b> {len(affs)}\n\n"
        f"📡 <b>2. TRÁFICO Y RADAR DREAMCRAFTERS</b>\n"
        f"  • <b>Estado del Sistema:</b> {html.escape(patrol_status)}\n"
        f"  • <b>Hoy:</b> {pv_today} visitas y {ic_today} clics de intención\n"
        f"  • <b>Ventana reciente:</b> {metrics.get('pageviews', 0)} vistas / {metrics.get('intentClicks', 0)} intentos de compra\n\n"
        f"🤖 <b>3. REDES SOCIALES Y BOTS PUBLICADORES</b>\n"
        f"  • <b>DreamCrafters (FB/IG):</b> {dc_ig_today} Reels publicados hoy\n"
        f"  • <b>Talento con Tarifa + PET:</b> {tct_ig_today} Reels publicados hoy\n"
        f"{tk_line}"
        f"  • <b>YouTube Shorts:</b> {yt_today_count} subidos hoy ({yt_total_views:,} vistas totales en el canal){yt_extra}\n\n"
        f"📋 <b>4. CONSULTORÍA (TRELLO MS PROYECTO LCM)</b>\n"
        f"  • {html.escape(trello_summary)}\n\n"
        f"🔗 <a href=\"https://www.dreamcrafters.lat/admin-pedidos\">Abrir Panel Admin DreamCrafters</a>"
    )
    return msg


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    env = dict(os.environ)
    report_html = build_daily_report(env)
    print(report_html)

    bot_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = env.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("Error: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": report_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        chat_info = (res_data.get("result") or {}).get("chat") or {}
        from_info = (res_data.get("result") or {}).get("from") or {}
        print(
            f"\n[OK] Corte Diario enviado por Telegram a {chat_info.get('first_name') or chat_info.get('username')} "
            f"vía @{from_info.get('username')} (message_id={(res_data.get('result') or {}).get('message_id')})"
        )


if __name__ == "__main__":
    main()
