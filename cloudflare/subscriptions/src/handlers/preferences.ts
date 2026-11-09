import { escapeHtml, normalizeEmail } from "../lib/input.ts";
import { HttpError, publicBaseUrl } from "../lib/http.ts";
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
  id: string; status: string; contact_status: string; subscriber_status: string;
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

function page(title: string, content: string, status = 200, lang = "en"): Response {
  return new Response(`<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)} | GIDS</title><style>body{font:16px/1.5 system-ui,sans-serif;background:#f7faf9;color:#172a2a;margin:0}main{max-width:720px;margin:3rem auto;padding:1.5rem;background:white;border:1px solid #d7e4e1}label{display:block;margin:1.2rem 0 .3rem;font-weight:600}input,select,button{font:inherit;padding:.7rem;box-sizing:border-box;max-width:100%}select{width:100%;min-height:9rem}button{background:#087b70;color:white;border:0;cursor:pointer;margin:.6rem .5rem .6rem 0}a{color:#087b70}small{color:#526462}</style></head><body><main><h1>${escapeHtml(title)}</h1>${content}</main></body></html>`, {
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
      `SELECT s.id, s.status, s.preference_revision, s.management_generation, c.status AS contact_status, sub.status AS subscriber_status,
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
    return page(title, `${notice ? `<p role="status">${escapeHtml(notice)}</p>` : ""}<p>${escapeHtml(zh ? row.list_name_zh || row.list_name : row.list_name)} · ${escapeHtml(row.status)}</p><form method="post" action="/api/subscriptions/manage"><input type="hidden" name="token" value="${escapeHtml(token)}"><input type="hidden" name="revision" value="${revision}">${controls}<button name="action" value="save">${zh ? "保存偏好" : "Save preferences"}</button><button name="action" value="${action}">${action === "pause" ? (zh ? "暂停订阅" : "Pause subscription") : (zh ? "恢复订阅" : "Resume subscription")}</button></form><form method="post" action="/api/subscriptions/manage"><input type="hidden" name="token" value="${escapeHtml(token)}"><input type="hidden" name="revision" value="${revision}"><button name="action" value="unsubscribe_all">${zh ? "退订此邮箱的全部更新" : "Unsubscribe all updates for this email"}</button></form><p><a href="/api/subscriptions/manage">${zh ? "重新获取管理链接" : "Request another management link"}</a></p>`, 200, zh ? "zh" : "en");
  }

  async function requestLink(request: Request, env: Env, form: FormData, ctx?: ExecutionContext): Promise<Response> {
    const email = normalizeEmail(String(form.get("email") || ""));
    const message = "If this address has an eligible subscription, a management link will be sent. / 如果该邮箱有可管理的订阅，我们会发送管理链接。";
    const work = deliverManagementLink(request, env, email).catch(() => {
      // Do not expose the address or provider response through logs.
      console.error(JSON.stringify({ message: "subscription_management_delivery_failed" }));
    });
    if (ctx) ctx.waitUntil(work);
    else await work; // The isolated handler tests do not provide an execution context.
    return requestPage(message);
  }

  async function deliverManagementLink(request: Request, env: Env, email: string): Promise<void> {
    if (!email || !env.TOKEN_SIGNING_SECRET) return;
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (!await consumeLimit(env, `ip:${ip}`, 20) || !await consumeLimit(env, `email:${email}`, 2)) return;
    const addressHash = await sha256Hex(`${env.TOKEN_SIGNING_SECRET}:email:${email}`);
    const contact = await env.DB.prepare("SELECT id, subscriber_id FROM subscriber_contacts WHERE channel='email' AND address_hash=? AND status='active'")
      .bind(addressHash).first<{ id: string; subscriber_id: string }>();
    if (!contact) return;
    const rows = await env.DB.prepare(
      `SELECT s.id, l.name FROM subscriptions s JOIN subscription_lists l ON l.id=s.list_id
       JOIN subscribers sub ON sub.id=s.subscriber_id
       WHERE s.contact_id=? AND s.status IN ('active','paused') AND sub.status='active' ORDER BY l.sort_order`,
    ).bind(contact.id).all<{ id: string; name: string }>();
    const config = deps.smtpConfig(env);
    if (!config || !rows.results?.length) return;
    const links = await Promise.all(rows.results.map(async row => ({
      name: row.name,
      url: `${publicBaseUrl(request, env)}/api/subscriptions/manage?token=${await deps.createSignedToken(env, "manage", row.id, 1800)}`,
    })));
    const deliveryId = crypto.randomUUID();
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO transactional_email_deliveries (id,subscriber_id,contact_id,delivery_type,channel,recipient,subject,provider,status,attempts,request_source,created_at,updated_at)
       VALUES (?,?,?,'subscription_management','email',?,'Manage your GIDS subscriptions','smtp','queued',1,'preferences',?,?)`,
    ).bind(deliveryId, contact.subscriber_id, contact.id, email, now, now).run();
    let receipt: void | { providerMessageId: string | null };
    try {
      receipt = await deps.sendSmtpEmail(config, {
        to: email, subject: "Manage your GIDS subscriptions / 管理 GIDS 订阅",
        text: `These links expire in 30 minutes. / 链接 30 分钟后过期。\n\n${links.map(link => `${link.name}: ${link.url}`).join("\n")}`,
        html: `<p>These links expire in 30 minutes. / 链接 30 分钟后过期。</p><ul>${links.map(link => `<li><a href="${escapeHtml(link.url)}">${escapeHtml(link.name)}</a></li>`).join("")}</ul>`,
        messageId: `${deliveryId}@globalinfectiousdisease.com`,
      }, "transactional");
    } catch {
      // A lost connection does not prove the server rejected the message.
      await env.DB.prepare("UPDATE transactional_email_deliveries SET status='unknown', error_code='smtp_result_unknown', updated_at=? WHERE id=? AND status='queued'")
        .bind(new Date().toISOString(), deliveryId).run();
      return;
    }
    // Keep already-arrived terminal provider feedback intact.
    await env.DB.prepare("UPDATE transactional_email_deliveries SET status=CASE WHEN status='queued' THEN 'sent' ELSE status END, sent_at=?, provider_message_id=?, updated_at=? WHERE id=?")
      .bind(new Date().toISOString(), receipt?.providerMessageId || `${deliveryId}@globalinfectiousdisease.com`, new Date().toISOString(), deliveryId).run();
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
    const statements = [env.DB.prepare(
      `UPDATE subscriptions SET status=?, updated_at=?, preference_revision=? WHERE id=? AND status=?
       AND COALESCE(preference_revision, '') = ?
       AND management_generation = ?
       AND EXISTS (SELECT 1 FROM subscriber_contacts c JOIN subscribers sub ON sub.id=c.subscriber_id
       WHERE c.id=subscriptions.contact_id AND c.status='active' AND sub.status='active')`,
    ).bind(newStatus, now, revision, row.id, row.status, suppliedRevision, row.management_generation)];
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
    ).bind(crypto.randomUUID(), `preferences_${action}`, JSON.stringify({ action }), now, row.id, revision));
    const result = await env.DB.batch(statements);
    if (!result[0]?.meta?.changes) throw new HttpError(409, "subscription_changed_retry");
    if (action === "unsubscribe_all") {
      return page(row.locale === "zh" ? "已退订" : "Unsubscribed", row.locale === "zh" ? "<p>此邮箱的全部更新已退订。</p>" : "<p>All updates for this email have been unsubscribed.</p>", 200, row.locale === "zh" ? "zh" : "en");
    }
    return render(request, env, token, row.locale === "zh" ? "订阅偏好已更新。" : "Your subscription preferences were updated.");
  }

  return {
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
