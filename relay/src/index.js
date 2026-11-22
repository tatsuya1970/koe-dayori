// 声だより の LINE の受け口（Cloudflare Workers、11/22 D29）
//
// LINE ─Webhook→ POST /callback：署名を確かめ、決めたグループのお便りだけを Durable Object（SQLite）に貯めて、すぐ 200
// Pi ─HTTPS→    GET /events（取り出す）・GET /image?id=（写真）・POST /ack（受け取った → 消す）・GET /ws（届いたら "new"）
//
// - Pi 用の窓口は合言葉（RELAY_TOKEN）で守る
// - ログにお便りの本文を出さない（件数と id だけ）
// - 写真は受けたときに LINE から取り出して貯める（LINE に置かれる期間に限りがあるので、Pi が止まっていても失わない）
// - Pi が受け取らないまま RETENTION_DAYS を過ぎたものは消し、消したことをログに残す（U32）

import { DurableObject } from "cloudflare:workers";

const enc = new TextEncoder();
const CHUNK = 1024 * 1024; // 写真は 1MB ずつに分けて貯める（1行の上限より小さく）
const PAGE = 20;
const IMAGE_RETRY_LIMIT = 10;

export async function lineSignatureOk(body, signature, secret) {
  if (!secret || !signature) return false;
  let sig;
  try {
    sig = Uint8Array.from(atob(signature), (c) => c.charCodeAt(0));
  } catch {
    return false;
  }
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  return crypto.subtle.verify("HMAC", key, sig, body);
}

async function tokenOk(request, token) {
  if (!token) return false;
  const got = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(got)),
    crypto.subtle.digest("SHA-256", enc.encode(token)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);
}

// 貯めるものだけを、Pi の app/webhook.py の to_letter() が読む形で残す（ほかの項目は捨てる）
export function keep(event, env) {
  const source = event.source || {};
  if (event.type !== "message" || source.type !== "group") return null;
  if (!env.LINE_GROUP_ID || source.groupId !== env.LINE_GROUP_ID) return null;
  const m = event.message || {};
  if (m.type === "text") {
    const mentionees = (m.mention && m.mention.mentionees) || [];
    if ((env.REPLY_FILTER || "mention") === "mention" && !mentionees.some((x) => x.isSelf)) return null;
  } else if (m.type !== "image") {
    return null; // スタンプ・動画・音声は、今は受けない（Pi と同じ）
  }
  const id = event.webhookEventId || m.id;
  if (!id) return null;
  const out = {
    type: "message",
    webhookEventId: id,
    timestamp: event.timestamp,
    source: { type: "group", groupId: source.groupId, userId: source.userId },
    message: { type: m.type, id: m.id },
  };
  if (m.type === "text") {
    out.message.text = m.text;
    if (m.mention) out.message.mention = m.mention;
  }
  if (event.koeSenderName) out.koeSenderName = event.koeSenderName; // 試験用（tools/fake_line_event.py）。署名が合ったものだけ
  return out;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const inbox = env.INBOX.get(env.INBOX.idFromName("inbox"));

    if (url.pathname === "/callback") {
      if (request.method !== "POST") return new Response(null, { status: 405 });
      const body = await request.arrayBuffer();
      if (!(await lineSignatureOk(body, request.headers.get("X-Line-Signature"), env.LINE_CHANNEL_SECRET))) {
        console.log("署名が合いません");
        return new Response(null, { status: 403 });
      }
      let events;
      try {
        events = JSON.parse(new TextDecoder().decode(body)).events || [];
      } catch {
        return new Response(null, { status: 400 });
      }
      const kept = events.map((e) => keep(e, env)).filter(Boolean);
      if (kept.length) {
        const added = await inbox.store(kept);
        console.log(`受けた ${events.length} 件、貯めた ${added} 件`);
      }
      return new Response("OK"); // LINE の「検証」（events が空）もここで 200
    }

    if (["/events", "/image", "/ack", "/ws"].includes(url.pathname)) {
      if (!(await tokenOk(request, env.RELAY_TOKEN))) return new Response(null, { status: 401 });
      if (url.pathname === "/ws") return inbox.fetch(request);
      if (url.pathname === "/events" && request.method === "GET") return Response.json({ events: await inbox.list() });
      if (url.pathname === "/ack" && request.method === "POST") {
        let ids;
        try {
          ids = (await request.json()).ids;
        } catch {
          return new Response(null, { status: 400 });
        }
        if (!Array.isArray(ids)) return new Response(null, { status: 400 });
        return Response.json({ deleted: await inbox.ack(ids.slice(0, 100).map(String)) });
      }
      if (url.pathname === "/image" && request.method === "GET") {
        const img = await inbox.image(url.searchParams.get("id") || "");
        if (!img) return new Response(null, { status: 404 });
        return new Response(img.data, { headers: { "Content-Type": img.contentType } });
      }
      return new Response(null, { status: 405 });
    }
    return new Response(null, { status: 404 });
  },
};

export class Inbox extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY,
      received_at INTEGER NOT NULL,
      event TEXT NOT NULL,
      image_state TEXT NOT NULL DEFAULT 'none',  -- none / pending / ready / failed
      content_type TEXT,
      attempts INTEGER NOT NULL DEFAULT 0
    )`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS image_chunks (
      id TEXT NOT NULL, seq INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY (id, seq)
    )`);
    // Pi が文字の "ping" を送っても、起こさずに "pong" と返す（料金がかからない）。ふだんは protocol の ping を使う
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  retentionMs() {
    return Number(this.env.RETENTION_DAYS || 7) * 24 * 3600 * 1000;
  }

  async store(events) {
    let added = 0;
    let ready = 0;
    let pending = 0;
    const now = Date.now();
    for (const e of events) {
      const state = e.message.type === "image" ? "pending" : "none";
      const cur = this.sql.exec(
        "INSERT OR IGNORE INTO events (id, received_at, event, image_state) VALUES (?, ?, ?, ?)",
        e.webhookEventId, now, JSON.stringify(e), state,
      );
      if (cur.rowsWritten > 0) {
        added++;
        if (state === "pending") pending++;
        else ready++;
      }
    }
    if (pending) await this.ctx.storage.setAlarm(now); // 写真はすぐ取り出しに行く（alarm は失敗してもやり直してくれる）
    else await this.scheduleAlarm();
    if (ready) this.broadcast();
    return added;
  }

  list() {
    const rows = this.sql.exec(
      "SELECT id, event, image_state FROM events WHERE image_state != 'pending' ORDER BY received_at, rowid LIMIT ?",
      PAGE,
    ).toArray();
    return rows.map((r) => ({ id: r.id, event: JSON.parse(r.event), image: r.image_state === "ready" }));
  }

  image(id) {
    const row = this.sql.exec("SELECT content_type FROM events WHERE id = ? AND image_state = 'ready'", id).toArray()[0];
    if (!row) return null;
    const chunks = this.sql.exec("SELECT data FROM image_chunks WHERE id = ? ORDER BY seq", id).toArray();
    const size = chunks.reduce((n, c) => n + c.data.byteLength, 0);
    const data = new Uint8Array(size);
    let off = 0;
    for (const c of chunks) {
      data.set(new Uint8Array(c.data), off);
      off += c.data.byteLength;
    }
    return { data, contentType: row.content_type || "image/jpeg" };
  }

  async ack(ids) {
    let deleted = 0;
    for (const id of ids) {
      deleted += this.sql.exec("DELETE FROM events WHERE id = ?", id).rowsWritten;
      this.sql.exec("DELETE FROM image_chunks WHERE id = ?", id);
    }
    await this.scheduleAlarm();
    return deleted;
  }

  // ---- WebSocket（Hibernation API：待っているあいだ料金がかからない） ----
  async fetch(request) {
    if (request.headers.get("Upgrade") !== "websocket") return new Response(null, { status: 426 });
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1]);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  webSocketMessage(ws, message) {
    // Pi からは何も送らない約束。来ても無視する
  }

  webSocketClose(ws, code) {
    try {
      ws.close(code === 1005 ? 1000 : code, "bye");
    } catch {
      // もう閉じている
    }
  }

  broadcast() {
    for (const ws of this.ctx.getWebSockets()) {
      try {
        ws.send("new");
      } catch {
        // 切れたものは webSocketClose で片づく
      }
    }
  }

  // ---- alarm：写真の取り出しと、古いものの片づけ ----
  async alarm() {
    const now = Date.now();
    let ready = 0;
    const pending = this.sql.exec("SELECT id, event, attempts FROM events WHERE image_state = 'pending' LIMIT 5").toArray();
    for (const r of pending) {
      const mid = JSON.parse(r.event).message.id;
      let res;
      try {
        res = await fetch(`https://api-data.line.me/v2/bot/message/${mid}/content`, {
          headers: { Authorization: `Bearer ${this.env.LINE_CHANNEL_ACCESS_TOKEN}` },
        });
      } catch (e) {
        res = null;
      }
      if (res && res.ok) {
        const buf = new Uint8Array(await res.arrayBuffer());
        this.sql.exec("DELETE FROM image_chunks WHERE id = ?", r.id);
        for (let i = 0, seq = 0; i < buf.length; i += CHUNK, seq++) {
          this.sql.exec("INSERT INTO image_chunks (id, seq, data) VALUES (?, ?, ?)", r.id, seq, buf.slice(i, i + CHUNK).buffer);
        }
        this.sql.exec("UPDATE events SET image_state = 'ready', content_type = ? WHERE id = ?",
          res.headers.get("Content-Type") || "image/jpeg", r.id);
        console.log(`写真を取り出しました id=${r.id} ${buf.length} バイト`);
        ready++;
      } else {
        const status = res ? res.status : "通信できません";
        const permanent = res && res.status >= 400 && res.status < 500 && res.status !== 429;
        const attempts = r.attempts + 1;
        if (permanent || attempts >= IMAGE_RETRY_LIMIT) {
          // 写真なしで Pi に渡す。Pi が LINE から取り出せなければ「取り出せませんでした」と伝える
          this.sql.exec("UPDATE events SET image_state = 'failed', attempts = ? WHERE id = ?", attempts, r.id);
          console.log(`写真を取り出せません（あきらめます） id=${r.id} ${status}`);
          ready++;
        } else {
          this.sql.exec("UPDATE events SET attempts = ? WHERE id = ?", attempts, r.id);
          console.log(`写真を取り出せません（あとでやり直します） id=${r.id} ${status}`);
        }
      }
    }

    const expired = this.sql.exec("SELECT id FROM events WHERE received_at < ?", now - this.retentionMs()).toArray();
    for (const r of expired) {
      this.sql.exec("DELETE FROM events WHERE id = ?", r.id);
      this.sql.exec("DELETE FROM image_chunks WHERE id = ?", r.id);
    }
    if (expired.length) {
      console.log(`Pi が受け取らないまま ${this.env.RETENTION_DAYS || 7} 日たったお便りを ${expired.length} 件消しました: ${expired.map((r) => r.id).join(",")}`);
    }

    if (ready) this.broadcast();
    await this.scheduleAlarm();
  }

  async scheduleAlarm() {
    const p = this.sql.exec("SELECT MAX(attempts) AS a FROM events WHERE image_state = 'pending'").toArray()[0];
    if (p && p.a !== null) {
      await this.ctx.storage.setAlarm(Date.now() + 30_000 * Math.max(1, p.a)); // 写真のやり直しは少しずつ間を空ける
      return;
    }
    const o = this.sql.exec("SELECT MIN(received_at) AS t FROM events").toArray()[0];
    if (o && o.t !== null) await this.ctx.storage.setAlarm(o.t + this.retentionMs() + 1000);
    else await this.ctx.storage.deleteAlarm();
  }
}
