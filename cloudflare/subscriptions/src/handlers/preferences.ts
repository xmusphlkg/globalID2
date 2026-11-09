import { escapeHtml, normalizeEmail } from "../lib/input.ts";
import { HttpError, publicBaseUrl } from "../lib/http.ts";
import { buildManagementEmail } from "../lib/email.ts";
import { secureTextEqual, sha256Hex } from "../lib/security.ts";
import type { Env } from "../types.ts";

type SmtpConfig = {
  host: string; port: number; username: string; password: string; fromEmail: string;
  fromName: string; useTls: boolean;
};

type Dependencies = {
  createSignedToken(env: Env, purpose: "manage", subscriptionId: string, ttlSeconds: number): Promise<string>;
  verifySignedToken(env: Env, token: string, purpose: "manage"): Promise<{ subscriptionId: string; version?: string } | null>;
  smtpConfig(env: Env): SmtpConfig | null;
  sendSmtpEmail(config: SmtpConfig, message: {
    to: string; subject: string; text: string; html: string; messageId?: string;
  }, purpose?: "content" | "transactional"): Promise<void | { providerMessageId: string | null }>;
};

type Subscription = {
  id: string; status: string; frequency: string; contact_status: string; subscriber_status: string;
  list_code: string; list_name: string; list_name_zh: string | null; locale: string;
  preference_revision: string | null;
  management_generation: string;
};

const FILTERS: Record<string, Array<{ type: string; en: string; zh: string }>> = {
  reports: [{ type: "country", en: "Countries", zh: "国家和地区" }, { type: "disease", en: "Diseases", zh: "疾病" }],
  weekly_digest: [{ type: "country", en: "Countries", zh: "国家和地区" }, { type: "disease", en: "Diseases", zh: "疾病" }],
  alerts: [{ type: "country", en: "Countries", zh: "国家和地区" }, { type: "disease", en: "Diseases", zh: "疾病" }],
  research_digest: [
    { type: "country", en: "Countries", zh: "国家和地区" }, { type: "disease", en: "Diseases", zh: "疾病" },
    { type: "research_topic", en: "Research topics", zh: "研究主题" },
    { type: "study_type", en: "Study types", zh: "研究类型" },
    { type: "peer_review_status", en: "Publication status", zh: "出版状态" },
  ],
};

// Match the frequencies accepted by new subscription requests and consumed by
// the automated dispatchers. Historical values stay unchanged without consent.
const SUPPORTED_FREQUENCIES: Record<string, string> = {
  reports: "weekly", weekly_digest: "weekly", research_digest: "weekly", alerts: "instant",
};
const FREQUENCY_LABELS: Record<string, { en: string; zh: string }> = {
  instant: { en: "Instant", zh: "即时" }, daily: { en: "Daily", zh: "每日" },
  weekly: { en: "Weekly", zh: "每周" }, monthly: { en: "Monthly", zh: "每月" },
};

function frequencyLabel(value: string, zh: boolean): string {
  return FREQUENCY_LABELS[value]?.[zh ? "zh" : "en"] || value;
}

function page(title: string, content: string, status = 200, lang = "en"): Response {
  return new Response(`<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)} | GIDS</title><style>body{font:16px/1.5 system-ui,sans-serif;background:#f7faf9;color:#172a2a;margin:0}main{max-width:720px;margin:3rem auto;padding:1.5rem;background:white;border:1px solid #d7e4e1}label{display:block;margin:1.2rem 0 .3rem;font-weight:600}input,select,button{font:inherit;padding:.7rem;box-sizing:border-box;max-width:100%}select{width:100%}select[multiple]{min-height:9rem}button{background:#087b70;color:white;border:0;cursor:pointer;margin:.6rem .5rem .6rem 0}a{color:#087b70}small{color:#526462}</style></head><body><main><h1>${escapeHtml(title)}</h1>${content}</main></body></html>`, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    },
  });
}

function requestPage(message = ""): Response {
  return page("Manage your subscription / 管理订阅", `${message ? `<p>${escapeHtml(message)}</p>` : ""}<p>Enter your email to receive a short-lived management link. / 输入邮箱获取短时有效的管理链接。</p><form method="post" action="/api/subscriptions/manage"><label for="email">Email / 邮箱</label><input id="email" name="email" type="email" autocomplete="email" required maxlength="254"><button type="submit">Send link / 发送链接</button></form><p><a href="https://globalinfectiousdisease.com/subscribe/">Subscription center / 订阅中心</a></p>`);
}

async function consumeLimit(env: Env, key: string, max: number): Promise<boolean> {
  const window = Math.floor(Date.now() / 600_000);
  const bucket = await sha256Hex(`${env.TOKEN_SIGNING_SECRET}:manage-limit:${key}:${window}`);
  const expires = new Date((window + 2) * 600_000).toISOString();
  const result = await env.DB.prepare(
    `INSERT INTO preference_link_limits (bucket, attempts, expires_at) VALUES (?, 1, ?)
     ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1 WHERE attempts < ? RETURNING attempts`,
  ).bind(bucket, expires, max).first<{ attempts: number }>();
  return Boolean(result);
}

async function readForm(request: Request): Promise<FormData> {
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/x-www-form-urlencoded")) {
    throw new HttpError(415, "form_content_type_required");
  }
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, "form_required");
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.byteLength;
    if (size > 65536) {
      await reader.cancel();
      throw new HttpError(413, "form_too_large");
    }
    chunks.push(part.value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const form = new FormData();
  for (const [key, value] of new URLSearchParams(new TextDecoder().decode(bytes))) form.append(key, value);
  return form;
}

export function createPreferenceHandlers(deps: Dependencies) {
  async function findSubscription(env: Env, token: string): Promise<Subscription> {
    const parsed = await deps.verifySignedToken(env, token, "manage");
    if (!parsed) throw new HttpError(400, "invalid_or_expired_management_link");
    const row = await env.DB.prepare(
      `SELECT s.id, s.status, s.frequency, s.preference_revision, s.management_generation, c.status AS contact_status, sub.status AS subscriber_status,
       l.code AS list_code, l.name AS list_name, l.name_zh AS list_name_zh, sub.locale
       FROM subscriptions s JOIN subscriber_contacts c ON c.id=s.contact_id
       JOIN subscribers sub ON sub.id=s.subscriber_id JOIN subscription_lists l ON l.id=s.list_id
       WHERE s.id=?`,
    ).bind(parsed.subscriptionId).first<Subscription>();
    if (!row || !parsed.version || !(await secureTextEqual(parsed.version, row.management_generation)) || !["active", "paused"].includes(row.status) || row.contact_status !== "active" || row.subscriber_status !== "active") {
      throw new HttpError(400, "management_link_no_longer_available");
    }
    return row;
  }

  async function render(request: Request, env: Env, token: string, notice = ""): Promise<Response> {
    if (!token) return requestPage(notice);
    const row = await findSubscription(env, token);
    const zh = row.locale === "zh";
    const options = await env.DB.prepare(
      `SELECT filter_type, filter_value, label_en, label_zh FROM subscription_filter_options
       WHERE is_public=1 ORDER BY filter_type, sort_order, label_en`,
    ).all<{ filter_type: string; filter_value: string; label_en: string; label_zh: string | null }>();
    const filters = await env.DB.prepare("SELECT filter_type, filter_value FROM subscription_filters WHERE subscription_id=?")
      .bind(row.id).all<{ filter_type: string; filter_value: string }>();
    const selected = new Set((filters.results || []).map(item => `${item.filter_type}:${item.filter_value}`));
    const controls = (FILTERS[row.list_code] || []).map(filter => {
      const choices = (options.results || []).filter(item => item.filter_type === filter.type).map(item =>
        `<option value="${escapeHtml(item.filter_value)}"${selected.has(`${filter.type}:${item.filter_value}`) ? " selected" : ""}>${escapeHtml(zh ? item.label_zh || item.label_en : item.label_en)}</option>`).join("");
      return `<label for="${filter.type}">${escapeHtml(zh ? filter.zh : filter.en)}</label><small>${zh ? "不选表示全部。按住 Ctrl 或 Command 可多选。" : "Leave empty for all. Hold Ctrl or Command to select multiple."}</small><select id="${filter.type}" name="${filter.type}" multiple>${choices}</select>`;
    }).join("");
    const action = row.status === "paused" ? "resume" : "pause";
    const title = zh ? "管理订阅偏好" : "Manage subscription preferences";
    const revision = escapeHtml(row.preference_revision || "");
    const supportedFrequency = SUPPORTED_FREQUENCIES[row.list_code];
    const frequencyNotice = supportedFrequency && row.frequency !== supportedFrequency
      ? (zh ? "当前频率暂不支持自动投递。请选择可用频率并点击保存偏好；在此之前，原选择保持不变。" : "Automatic delivery does not support your current frequency. Select an available frequency and save preferences; your existing choice stays unchanged until then.")
      : (zh ? "选择新频率后点击保存偏好；暂停或恢复不会更改频率。" : "Select a new frequency and save preferences; pausing or resuming does not change it.");
    const frequencyControl = `<label for="frequency">${zh ? "投递频率" : "Delivery frequency"}</label><p>${zh ? "当前频率" : "Current frequency"}: ${escapeHtml(frequencyLabel(row.frequency, zh))}</p><small id="frequency-help">${escapeHtml(frequencyNotice)}</small><select id="frequency" name="frequency" aria-describedby="frequency-help"><option value="">${zh ? "保留当前频率" : "Keep current frequency"}</option>${supportedFrequency && row.frequency !== supportedFrequency ? `<option value="${escapeHtml(supportedFrequency)}">${escapeHtml(frequencyLabel(supportedFrequency, zh))}</option>` : ""}</select>`;
    return page(title, `${notice ? `<p role="status">${escapeHtml(notice)}</p>` : ""}<p>${escapeHtml(zh ? row.list_name_zh || row.list_name : row.list_name)} · ${escapeHtml(row.status)}</p><form method="post" action="/api/subscriptions/manage"><input type="hidden" name="token" value="${escapeHtml(token)}"><input type="hidden" name="revision" value="${revision}">${frequencyControl}${controls}<button name="action" value="save">${zh ? "保存偏好" : "Save preferences"}</button><button name="action" value="${action}">${action === "pause" ? (zh ? "暂停订阅" : "Pause subscription") : (zh ? "恢复订阅" : "Resume subscription")}</button></form><form method="post" action="/api/subscriptions/manage"><input type="hidden" name="token" value="${escapeHtml(token)}"><input type="hidden" name="revision" value="${revision}"><button name="action" value="unsubscribe_all">${zh ? "退订此邮箱的全部更新" : "Unsubscribe all updates for this email"}</button></form><p><a href="/api/subscriptions/manage">${zh ? "重新获取管理链接" : "Request another management link"}</a></p>`, 200, zh ? "zh" : "en");
  }

  async function requestLink(request: Request, env: Env, form: FormData, ctx?: ExecutionContext): Promise<Response> {
    const email = normalizeEmail(String(form.get("email") || ""));
    const message = "If this address has an eligible subscription, a management link will be sent. / 如果该邮箱有可管理的订阅，我们会发送管理链接。";
    try {
      // Persist before returning: waitUntil alone can be interrupted and is not a queue.
      await enqueueManagementLink(request, env, email);
      const work = drainManagementOutbox(env).catch(() => {
        console.error(JSON.stringify({ message: "subscription_management_drain_failed" }));
      });
      if (ctx) ctx.waitUntil(work);
      else await work;
    } catch {
      // Never reveal whether an address exists, including on storage errors.
      console.error(JSON.stringify({ message: "subscription_management_enqueue_failed" }));
    }
    return requestPage(message);
  }

  async function enqueueManagementLink(request: Request, env: Env, email: string): Promise<void> {
    if (!email || !env.TOKEN_SIGNING_SECRET) return;
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!await consumeLimit(env, `ip:${ip}`, 20) || !await consumeLimit(env, `email:${email}`, 2)) return;
    const addressHash = await sha256Hex(`${env.TOKEN_SIGNING_SECRET}:email:${email}`);
    const contact = await env.DB.prepare(`SELECT c.id, c.subscriber_id FROM subscriber_contacts c
      JOIN subscribers sub ON sub.id=c.subscriber_id
      WHERE c.channel='email' AND c.address_hash=? AND c.status='active' AND sub.status='active'
        AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.contact_id=c.id AND s.status IN ('active','paused'))`)
      .bind(addressHash).first<{ id: string; subscriber_id: string }>();
    if (!contact) return;
    const now = new Date().toISOString();
    const expires = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    await env.DB.prepare(`INSERT INTO management_email_outbox
      (id, subscriber_id, contact_id, status, attempts, next_attempt_at, created_at, expires_at, updated_at)
      VALUES (?, ?, ?, 'queued', 0, ?, ?, ?, ?)`)
      .bind(crypto.randomUUID(), contact.subscriber_id, contact.id, now, now, expires, now).run();
  }

  async function drainManagementOutbox(env: Env): Promise<void> {
    const now = new Date().toISOString();
    // The provider may have accepted a message before its worker died.
    await env.DB.prepare(`UPDATE management_email_outbox SET status='unknown', last_error='send_outcome_unknown',
      claim_token=NULL, claim_expires_at=NULL, updated_at=? WHERE status='sending' AND claim_expires_at<=?`)
      .bind(now, now).run();
    await env.DB.prepare(`UPDATE management_email_outbox SET status='cancelled', last_error='request_expired', updated_at=?
      WHERE status IN ('queued','retry') AND expires_at<=?`).bind(now, now).run();
    const candidate = await env.DB.prepare(`SELECT id FROM management_email_outbox
      WHERE status IN ('queued','retry') AND next_attempt_at<=? AND expires_at>? ORDER BY created_at LIMIT 1`)
      .bind(now, now).first<{ id: string }>();
    if (!candidate) return;
    const claim = crypto.randomUUID();
    const lease = new Date(Date.now() + 120_000).toISOString();
    const job = await env.DB.prepare(`UPDATE management_email_outbox SET status='sending', attempts=attempts+1,
      claim_token=?, claim_expires_at=?, updated_at=? WHERE id=? AND status IN ('queued','retry')
      AND next_attempt_at<=? AND expires_at>? RETURNING id,subscriber_id,contact_id,attempts`)
      .bind(claim, lease, now, candidate.id, now, now)
      .first<{ id: string; subscriber_id: string; contact_id: string; attempts: number }>();
    if (!job) return;
    const update = async (status: string, reason: string | null, next: string | null = null) => {
      await env.DB.prepare(`UPDATE management_email_outbox SET status=?, last_error=?, next_attempt_at=?,
        claim_token=NULL, claim_expires_at=NULL, updated_at=? WHERE id=? AND status='sending' AND claim_token=?`)
        .bind(status, reason, next, new Date().toISOString(), job.id, claim).run();
    };
    const retry = async (reason: string) => {
      if (job.attempts >= 12) await update('failed', reason);
      else await update('retry', reason, new Date(Date.now() + 600_000).toISOString());
    };
    let smtpStarted = false;
    try {
      const contact = await env.DB.prepare(`SELECT c.address, sub.locale FROM subscriber_contacts c
        JOIN subscribers sub ON sub.id=c.subscriber_id WHERE c.id=? AND c.subscriber_id=?
        AND c.status='active' AND sub.status='active'`)
        .bind(job.contact_id, job.subscriber_id).first<{ address: string; locale: string }>();
      if (!contact) { await update('cancelled', 'contact_inactive'); return; }
      const rows = await env.DB.prepare(`SELECT s.id, l.name, l.name_zh FROM subscriptions s
        JOIN subscription_lists l ON l.id=s.list_id WHERE s.contact_id=? AND s.status IN ('active','paused')
        ORDER BY l.sort_order`).bind(job.contact_id).all<{ id: string; name: string; name_zh: string | null }>();
      if (!rows.results?.length) { await update('cancelled', 'subscription_inactive'); return; }
      const config = deps.smtpConfig(env);
      if (!config || !env.PUBLIC_BASE_URL || !env.TOKEN_SIGNING_SECRET) { await retry('delivery_not_configured'); return; }
      const base = publicBaseUrl(new Request(env.PUBLIC_BASE_URL), env);
      const links = await Promise.all(rows.results.map(async row => ({
        name: contact.locale === 'zh' ? row.name_zh || row.name : row.name,
        url: `${base}/api/subscriptions/manage?token=${await deps.createSignedToken(env, 'manage', row.id, 1800)}`,
      })));
      const content = buildManagementEmail(contact.locale, links);
      const deliveryId = crypto.randomUUID();
      const timestamp = new Date().toISOString();
      const written = await env.DB.batch([
        env.DB.prepare(`INSERT INTO transactional_email_deliveries
          (id,subscriber_id,contact_id,delivery_type,channel,recipient,subject,provider,status,attempts,request_source,created_at,updated_at)
          SELECT ?,subscriber_id,contact_id,'subscription_management','email',?,?,'smtp','queued',1,'preferences',?,?
          FROM management_email_outbox WHERE id=? AND status='sending' AND claim_token=?`)
          .bind(deliveryId, contact.address, content.subject, timestamp, timestamp, job.id, claim),
        env.DB.prepare(`UPDATE management_email_outbox SET transaction_delivery_id=?, updated_at=?
          WHERE id=? AND status='sending' AND claim_token=?`)
          .bind(deliveryId, timestamp, job.id, claim),
      ]);
      if (!written[0]?.meta.changes || !written[1]?.meta.changes) return;
      smtpStarted = true;
      let receipt: void | { providerMessageId: string | null };
      try {
        receipt = await deps.sendSmtpEmail(config, { to: contact.address, ...content,
          messageId: `${deliveryId}@globalinfectiousdisease.com` }, 'transactional');
      } catch (error) {
        const reason = error instanceof Error ? error.message : '';
        const temporary = /_rejected:4\d\d$/.test(reason);
        const permanent = /_rejected:5\d\d$/.test(reason);
        await env.DB.prepare(`UPDATE transactional_email_deliveries SET status=?, error_code=?, updated_at=?
          WHERE id=? AND status='queued'`)
          .bind(temporary || permanent ? 'failed' : 'unknown', temporary ? 'smtp_temporary_rejection' : permanent ? 'smtp_permanent_rejection' : 'smtp_result_unknown', new Date().toISOString(), deliveryId).run();
        if (temporary) await retry('smtp_temporary_rejection');
        else if (permanent) await update('failed', 'smtp_permanent_rejection');
        else await update('unknown', 'smtp_result_unknown');
        return;
      }
      await env.DB.prepare(`UPDATE transactional_email_deliveries SET status=CASE WHEN status='queued' THEN 'sent' ELSE status END,
        sent_at=?, provider_message_id=?, updated_at=? WHERE id=?`)
        .bind(new Date().toISOString(), receipt?.providerMessageId || `${deliveryId}@globalinfectiousdisease.com`, new Date().toISOString(), deliveryId).run();
      await update('sent', null);
    } catch {
      // Before SMTP starts we know no mail was sent; afterward the outcome is uncertain.
      if (smtpStarted) await update('unknown', 'send_outcome_unknown');
      else await retry('management_preparation_failed');
      console.error(JSON.stringify({ message: 'subscription_management_outbox_failed', outbox_id: job.id }));
    }
  }

  async function update(request: Request, env: Env, token: string, form: FormData): Promise<Response> {
    const row = await findSubscription(env, token);
    const action = String(form.get("action") || "");
    if (!["save", "pause", "resume", "unsubscribe_all"].includes(action)) throw new HttpError(400, "invalid_management_action");
    const suppliedRevision = form.get("revision");
    if (typeof suppliedRevision !== "string" || suppliedRevision.length > 100) throw new HttpError(400, "preference_revision_required");
    const now = new Date().toISOString();
    const revision = crypto.randomUUID();
    const newStatus = action === "pause" ? "paused" : action === "resume" ? "active" : action === "unsubscribe_all" ? "unsubscribed" : row.status;
    const requestedFrequencies = form.getAll("frequency");
    if (action === "save" && (requestedFrequencies.length > 1 || requestedFrequencies.some(value => typeof value !== "string"))) {
      throw new HttpError(400, "invalid_frequency");
    }
    const requestedFrequency = action === "save" ? String(requestedFrequencies[0] || "") : "";
    if (requestedFrequency && requestedFrequency !== SUPPORTED_FREQUENCIES[row.list_code]) throw new HttpError(400, "invalid_frequency");
    const newFrequency = requestedFrequency || row.frequency;
    const statements = [env.DB.prepare(
      `UPDATE subscriptions SET status=?, frequency=?, updated_at=?, preference_revision=? WHERE id=? AND status=?
       AND COALESCE(preference_revision, '') = ?
       AND management_generation = ?
       AND EXISTS (SELECT 1 FROM subscriber_contacts c JOIN subscribers sub ON sub.id=c.subscriber_id
       WHERE c.id=subscriptions.contact_id AND c.status='active' AND sub.status='active')`,
    ).bind(newStatus, newFrequency, now, revision, row.id, row.status, suppliedRevision, row.management_generation)];
    if (action === "unsubscribe_all") {
      const guard = "EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)";
      statements.push(env.DB.prepare(
        `UPDATE subscriptions SET status='unsubscribed', updated_at=?, preference_revision=?, management_generation=? WHERE contact_id=(SELECT contact_id FROM subscriptions WHERE id=?) AND ${guard}`,
      ).bind(now, revision, crypto.randomUUID(), row.id, row.id, revision));
      statements.push(env.DB.prepare(
        `UPDATE subscription_change_requests SET status='invalidated' WHERE contact_id=(SELECT contact_id FROM subscriptions WHERE id=?) AND status='pending' AND ${guard}`,
      ).bind(row.id, row.id, revision));
      statements.push(env.DB.prepare(
        `UPDATE message_deliveries SET status='skipped', last_error='subscription_unsubscribed' WHERE contact_id=(SELECT contact_id FROM subscriptions WHERE id=?) AND status IN ('queued','retry') AND ${guard}`,
      ).bind(row.id, row.id, revision));
      statements.push(env.DB.prepare(
        `UPDATE situation_alert_deliveries SET status='skipped', last_error='subscription_unsubscribed', skipped_at=?, updated_at=?, claim_token=NULL, claim_expires_at=NULL WHERE contact_id=(SELECT contact_id FROM subscriptions WHERE id=?) AND status IN ('queued','retry') AND ${guard}`,
      ).bind(now, now, row.id, row.id, revision));
    } else if (action === "pause") {
      statements.push(env.DB.prepare(
        `UPDATE message_deliveries SET status='skipped', last_error='subscription_paused' WHERE subscription_id=? AND status IN ('queued','retry')
         AND EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)`,
      ).bind(row.id, row.id, revision));
      statements.push(env.DB.prepare(
        `UPDATE situation_alert_deliveries SET status='skipped', last_error='subscription_paused', skipped_at=?, updated_at=?, claim_token=NULL, claim_expires_at=NULL
         WHERE subscription_id=? AND status IN ('queued','retry') AND EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)`,
      ).bind(now, now, row.id, row.id, revision));
    } else if (action === "save") {
      if (requestedFrequency && requestedFrequency !== row.frequency) {
        // A previously requested confirmation must not restore the old
        // frequency after this authenticated preference change.
        statements.push(env.DB.prepare(`UPDATE subscription_change_requests SET status='invalidated'
          WHERE subscription_id=? AND status='pending'
          AND EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)`)
          .bind(row.id, row.id, revision));
      }
      const allowedTypes = FILTERS[row.list_code] || [];
      const options = await env.DB.prepare("SELECT filter_type, filter_value FROM subscription_filter_options WHERE is_public=1")
        .all<{ filter_type: string; filter_value: string }>();
      const allowed = new Set((options.results || []).map(item => `${item.filter_type}:${item.filter_value}`));
      const entries = allowedTypes.flatMap(({ type }) => form.getAll(type).map(value => ({ type, value: String(value) })));
      if (entries.length > 300 || entries.some(item => !allowed.has(`${item.type}:${item.value}`))) throw new HttpError(400, "invalid_preferences");
      for (const { type } of allowedTypes) {
        statements.push(env.DB.prepare("DELETE FROM subscription_filters WHERE subscription_id=? AND filter_type=? AND EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)")
          .bind(row.id, type, row.id, revision));
      }
      for (const entry of entries) {
        statements.push(env.DB.prepare(
          `INSERT INTO subscription_filters (id,subscription_id,filter_type,filter_value,created_at)
           SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM subscriptions WHERE id=? AND preference_revision=?)
           ON CONFLICT(subscription_id,filter_type,filter_value) DO NOTHING`,
        ).bind(crypto.randomUUID(), row.id, entry.type, entry.value, now, row.id, revision));
      }
    }
    statements.push(env.DB.prepare(
      `INSERT INTO subscription_events (id, subscriber_id, subscription_id, event_type, actor_type, metadata_json, created_at)
       SELECT ?, subscriber_id, id, ?, 'subscriber', ?, ? FROM subscriptions WHERE id=? AND preference_revision=?`,
    ).bind(crypto.randomUUID(), `preferences_${action}`, JSON.stringify({ action, ...(requestedFrequency && requestedFrequency !== row.frequency ? { frequency_from: row.frequency, frequency_to: requestedFrequency } : {}) }), now, row.id, revision));
    const result = await env.DB.batch(statements);
    if (!result[0]?.meta?.changes) throw new HttpError(409, "subscription_changed_retry");
    if (action === "unsubscribe_all") {
      return page(row.locale === "zh" ? "已退订" : "Unsubscribed", row.locale === "zh" ? "<p>此邮箱的全部更新已退订。</p>" : "<p>All updates for this email have been unsubscribed.</p>", 200, row.locale === "zh" ? "zh" : "en");
    }
    return render(request, env, token, row.locale === "zh" ? "订阅偏好已更新。" : "Your subscription preferences were updated.");
  }

  return {
    drainManagementOutbox,
    async get(request: Request, env: Env): Promise<Response> {
      try {
        return await render(request, env, new URL(request.url).searchParams.get("token") || "");
      } catch (error) {
        if (error instanceof HttpError && error.status === 400) {
          return requestPage("This link is invalid or expired. Request a new link below. / 链接无效或已过期，请重新申请。");
        }
        throw error;
      }
    },
    async post(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
      const form = await readForm(request);
      const token = String(form.get("token") || "");
      return token ? update(request, env, token, form) : requestLink(request, env, form, ctx);
    },
  };
}
