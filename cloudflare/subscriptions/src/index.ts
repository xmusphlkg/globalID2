import { connect } from "cloudflare:sockets";

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

interface Env {
  DB: D1Database;
  PUBLIC_BASE_URL?: string;
  ALLOWED_ORIGINS?: string;
  DEBUG_RETURN_TOKENS?: string;
  TURNSTILE_SECRET_KEY?: string;
  TOKEN_SIGNING_SECRET?: string;
  ADMIN_API_TOKEN?: string;
  SMTP_HOST?: string;
  SMTP_PORT?: string;
  SMTP_USERNAME?: string;
  SMTP_PASSWORD?: string;
  SMTP_FROM_EMAIL?: string;
  SMTP_FROM_NAME?: string;
  SMTP_USE_TLS?: string;
  PENDING_EXPIRY_DAYS?: string;
  SUBMISSION_RATE_LIMIT_PER_HOUR?: string;
  CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES?: string;
}

interface D1Database {
  prepare(query: string): D1PreparedStatement;
}

interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<{ results?: T[] }>;
  run(): Promise<unknown>;
}

type Payload = Record<string, unknown>;
type SmtpSocket = ReturnType<typeof connect>;

interface EmailSubscriptionItem {
  id: string;
  listCode: string;
  frequency: string;
  confirmUrl: string;
}

interface EmailDeliveryResult {
  status: "sent" | "failed" | "skipped";
  provider: "smtp";
  delivery_id?: string;
  recipient?: string;
  from?: string;
  reason?: string;
  message?: string;
}

interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  fromEmail: string;
  fromName: string;
  useTls: boolean;
}

const TEXT = new TextEncoder();
const FREQUENCIES = new Set(["instant", "daily", "weekly", "monthly"]);
const LOCALES = new Set(["en", "zh"]);
const SUBSCRIPTION_STATUSES = new Set(["pending", "active", "paused", "unsubscribed", "expired"]);

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }

    try {
      const url = new URL(request.url);
      const path = trimTrailingSlash(url.pathname);

      if (request.method === "GET" && path === "/health") {
        return json({ ok: true, service: "globalid-subscriptions" }, request, env);
      }

      if (request.method === "GET" && path === "/api/subscriptions/options") {
        return listSubscriptionOptions(request, env);
      }

      if (request.method === "POST" && path === "/api/subscriptions") {
        return createSubscription(request, env);
      }

      if (request.method === "GET" && path === "/api/subscriptions/confirm") {
        return confirmSubscription(request, env);
      }

      if (request.method === "GET" && path === "/api/subscriptions/unsubscribe") {
        return unsubscribe(request, env);
      }

      if (request.method === "POST" && path === "/api/admin/audience") {
        return listAudience(request, env);
      }

      if (request.method === "GET" && path === "/api/admin/stats") {
        return subscriptionStats(request, env);
      }

      if (request.method === "GET" && path === "/api/admin/subscriptions") {
        return listSubscriptionsAdmin(request, env);
      }

      if (request.method === "POST" && path === "/api/admin/maintenance") {
        requireAdmin(request, env);
        return json(await runMaintenance(env), request, env);
      }

      return json({ error: "not_found" }, request, env, 404);
    } catch (error) {
      const message = error instanceof HttpError ? error.message : "internal_error";
      const status = error instanceof HttpError ? error.status : 500;
      return json({ error: message }, request, env, status);
    }
  },

  async scheduled(_controller: unknown, env: Env): Promise<void> {
    await runMaintenance(env);
  },
};

async function listSubscriptionOptions(request: Request, env: Env): Promise<Response> {
  const lists = await env.DB.prepare(
    `SELECT code, name, name_zh, description, description_zh, default_frequency
     FROM subscription_lists
     WHERE is_public = 1
     ORDER BY sort_order ASC, name ASC`
  ).all<{
    code: string;
    name: string;
    name_zh?: string | null;
    description?: string | null;
    description_zh?: string | null;
    default_frequency: string;
  }>();

  const options = await env.DB.prepare(
    `SELECT filter_type, filter_value, label_en, label_zh, description_en, description_zh
     FROM subscription_filter_options
     WHERE is_public = 1 AND filter_type IN ('country', 'disease')
     ORDER BY filter_type ASC, sort_order ASC, label_en ASC`
  ).all<{
    filter_type: string;
    filter_value: string;
    label_en: string;
    label_zh?: string | null;
    description_en?: string | null;
    description_zh?: string | null;
  }>();

  const filters: Record<string, JsonValue[]> = {
    country: [],
    disease: [],
  };

  for (const option of options.results || []) {
    const item = {
      value: option.filter_value,
      label_en: option.label_en,
      label_zh: option.label_zh || option.label_en,
      description_en: option.description_en || "",
      description_zh: option.description_zh || "",
    };
    if (option.filter_type === "country") {
      filters.country.push(item);
    } else if (option.filter_type === "disease") {
      filters.disease.push(item);
    }
  }

  return json({
    ok: true,
    lists: (lists.results || []).map((list) => ({
      code: list.code,
      name_en: list.name,
      name_zh: list.name_zh || list.name,
      description_en: list.description || "",
      description_zh: list.description_zh || list.description || "",
      default_frequency: list.default_frequency,
    })),
    locales: [
      { value: "en", label_en: "English", label_zh: "英文" },
      { value: "zh", label_en: "Chinese", label_zh: "中文" },
    ],
    frequencies: [
      { value: "weekly", label_en: "Weekly", label_zh: "每周" },
      { value: "monthly", label_en: "Monthly", label_zh: "每月" },
      { value: "instant", label_en: "Instant", label_zh: "即时" },
    ],
    filters,
  }, request, env);
}

async function createSubscription(request: Request, env: Env): Promise<Response> {
  const payload = await readPayload(request);
  await verifyTurnstileIfConfigured(payload, request, env);

  const email = normalizeEmail(valueAsString(payload.email));
  if (!email) {
    throw new HttpError(400, "valid_email_required");
  }

  await enforceSubmissionRateLimit(request, env);

  const now = new Date().toISOString();
  const locale = pick(valueAsString(payload.locale), LOCALES, "en");
  const timezone = boundedText(valueAsString(payload.timezone), "UTC", 80);
  const requestedFrequency = valueAsString(payload.frequency).trim().toLowerCase();
  const frequency = FREQUENCIES.has(requestedFrequency) ? requestedFrequency : "";
  const source = boundedText(valueAsString(payload.source), "website", 80);
  const listCodes = normalizeListCodes(payload);
  const filters = normalizeFilters(payload);
  const addressHash = await sha256Hex(`${signingSecret(env)}:email:${email}`);

  let contact = await env.DB.prepare(
    "SELECT id, subscriber_id FROM subscriber_contacts WHERE channel = ? AND address_hash = ?"
  ).bind("email", addressHash).first<{ id: string; subscriber_id: string }>();

  let subscriberId = contact?.subscriber_id;
  let contactId = contact?.id;

  if (!subscriberId || !contactId) {
    subscriberId = crypto.randomUUID();
    contactId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO subscribers (id, locale, timezone, status, created_at, updated_at)
       VALUES (?, ?, ?, 'active', ?, ?)`
    ).bind(subscriberId, locale, timezone, now, now).run();
    await env.DB.prepare(
      `INSERT INTO subscriber_contacts (
         id, subscriber_id, channel, address, address_hash, status, created_at, updated_at
       ) VALUES (?, ?, 'email', ?, ?, 'pending', ?, ?)`
    ).bind(contactId, subscriberId, email, addressHash, now, now).run();
  } else {
    await env.DB.prepare(
      "UPDATE subscribers SET locale = ?, timezone = ?, updated_at = ? WHERE id = ?"
    ).bind(locale, timezone, now, subscriberId).run();
    await env.DB.prepare(
      "UPDATE subscriber_contacts SET address = ?, updated_at = ? WHERE id = ?"
    ).bind(email, now, contactId).run();
  }

  if (!subscriberId || !contactId) {
    throw new HttpError(500, "subscriber_contact_not_created");
  }

  const createdSubscriptions: JsonValue[] = [];
  const emailSubscriptions: EmailSubscriptionItem[] = [];

  for (const code of listCodes) {
    const list = await env.DB.prepare(
      "SELECT id, default_frequency FROM subscription_lists WHERE code = ? AND is_public = 1"
    ).bind(code).first<{ id: string; default_frequency: string }>();

    if (!list) {
      throw new HttpError(400, `unknown_list:${code}`);
    }

    const existing = await env.DB.prepare(
      `SELECT id FROM subscriptions
       WHERE subscriber_id = ? AND contact_id = ? AND list_id = ?`
    ).bind(subscriberId, contactId, list.id).first<{ id: string }>();

    const subscriptionId = existing?.id ?? crypto.randomUUID();
    const effectiveFrequency = frequency || list.default_frequency || "weekly";

    if (existing) {
      await env.DB.prepare(
        `UPDATE subscriptions
         SET status = 'pending', frequency = ?, source = ?, updated_at = ?
         WHERE id = ?`
      ).bind(effectiveFrequency, source, now, subscriptionId).run();
      await env.DB.prepare("DELETE FROM subscription_filters WHERE subscription_id = ?")
        .bind(subscriptionId).run();
    } else {
      await env.DB.prepare(
        `INSERT INTO subscriptions (
           id, subscriber_id, contact_id, list_id, status, frequency, source, created_at, updated_at
         ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)`
      ).bind(subscriptionId, subscriberId, contactId, list.id, effectiveFrequency, source, now, now).run();
    }

    for (const filter of filters) {
      await env.DB.prepare(
        `INSERT INTO subscription_filters (id, subscription_id, filter_type, filter_value, created_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(subscription_id, filter_type, filter_value) DO NOTHING`
      ).bind(crypto.randomUUID(), subscriptionId, filter.type, filter.value, now).run();
    }

    await recordEvent(env, {
      subscriberId,
      subscriptionId,
      eventType: existing ? "subscription_updated" : "subscription_created",
      actorType: "subscriber",
      request,
      metadata: { list_code: code, frequency: effectiveFrequency, filters },
    });

    const confirmToken = await createSignedToken(env, "confirm", subscriptionId, 60 * 60 * 24 * 7);
    const confirmUrl = `${publicBaseUrl(request, env)}/api/subscriptions/confirm?token=${confirmToken}`;
    const item: Record<string, JsonValue> = {
      id: subscriptionId,
      list_code: code,
      status: "pending",
    };
    if (env.DEBUG_RETURN_TOKENS === "true") {
      item.confirm_url = confirmUrl;
    }
    createdSubscriptions.push(item);
    emailSubscriptions.push({
      id: subscriptionId,
      listCode: code,
      frequency: effectiveFrequency,
      confirmUrl,
    });
  }

  const emailDelivery = await sendAndRecordConfirmationEmail(env, request, {
    email,
    locale,
    subscriberId,
    contactId,
    source,
    subscriptions: emailSubscriptions,
  });

  return json({
    ok: true,
    status: "pending",
    message: "subscription_created_pending_confirmation",
    subscriptions: createdSubscriptions,
    email: emailDelivery,
  }, request, env, 201);
}

async function sendAndRecordConfirmationEmail(
  env: Env,
  request: Request,
  input: {
    email: string;
    locale: string;
    subscriberId: string;
    contactId: string;
    source: string;
    subscriptions: EmailSubscriptionItem[];
  },
): Promise<EmailDeliveryResult> {
  const config = smtpConfig(env);
  const now = new Date().toISOString();
  const deliveryId = crypto.randomUUID();
  const content = buildConfirmationEmail(input.locale, input.subscriptions);
  const primarySubscriptionId = input.subscriptions[0]?.id || null;
  const baseDelivery = {
    deliveryId,
    subscriberId: input.subscriberId,
    contactId: input.contactId,
    subscriptionId: primarySubscriptionId,
    recipient: input.email,
    subject: content.subject,
    deliveryType: "subscription_confirmation",
    provider: "smtp",
    source: input.source,
    metadata: {
      subscription_ids: input.subscriptions.map((subscription) => subscription.id),
      list_codes: input.subscriptions.map((subscription) => subscription.listCode),
    },
    now,
  };

  if (!config) {
    await insertEmailDelivery(env, {
      ...baseDelivery,
      status: "skipped",
      attempts: 0,
      errorCode: "smtp_not_configured",
      errorMessage: "SMTP is not fully configured.",
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_skipped",
      actorType: "system",
      request,
      metadata: { reason: "smtp_not_configured" },
    });
    return {
      status: "skipped",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      reason: "smtp_not_configured",
      message: "SMTP delivery is not configured.",
    };
  }

  const emailLimit = await confirmationEmailRateLimit(env, input.contactId);
  if (!emailLimit.allowed) {
    await insertEmailDelivery(env, {
      ...baseDelivery,
      status: "skipped",
      attempts: 0,
      errorCode: "confirmation_rate_limited",
      errorMessage: `Confirmation email limit reached. Try again after ${emailLimit.retryAfterSeconds} seconds.`,
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_rate_limited",
      actorType: "system",
      request,
      metadata: { retry_after_seconds: emailLimit.retryAfterSeconds },
    });
    return {
      status: "skipped",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      reason: "confirmation_rate_limited",
      message: "Confirmation email was recently sent. Please wait before requesting another one.",
    };
  }

  await insertEmailDelivery(env, {
    ...baseDelivery,
    status: "queued",
    attempts: 1,
  });

  try {
    await sendSmtpEmail(config, {
      to: input.email,
      subject: content.subject,
      text: content.text,
      html: content.html,
    });
    await updateEmailDelivery(env, {
      deliveryId,
      status: "sent",
      sentAt: new Date().toISOString(),
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_sent",
      actorType: "system",
      request,
      metadata: {
        provider: "smtp",
        recipient_domain: input.email.split("@")[1] || "",
      },
    });
    return {
      status: "sent",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      from: config.fromEmail,
      message: "Confirmation email sent.",
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "smtp_delivery_failed";
    await updateEmailDelivery(env, {
      deliveryId,
      status: "failed",
      errorCode: "smtp_delivery_failed",
      errorMessage: boundedText(errorMessage, "smtp_delivery_failed", 500),
    });
    await recordEvent(env, {
      subscriberId: input.subscriberId,
      subscriptionId: primarySubscriptionId || undefined,
      eventType: "confirmation_email_failed",
      actorType: "system",
      request,
      metadata: { provider: "smtp", reason: "smtp_delivery_failed" },
    });
    return {
      status: "failed",
      provider: "smtp",
      delivery_id: deliveryId,
      recipient: input.email,
      from: config.fromEmail,
      reason: "smtp_delivery_failed",
      message: "SMTP delivery failed.",
    };
  }
}

async function confirmSubscription(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token") || "";
  const parsed = await verifySignedToken(env, token, "confirm");
  if (!parsed) {
    throw new HttpError(400, "invalid_or_expired_token");
  }

  const subscription = await env.DB.prepare(
    `SELECT s.id, s.subscriber_id, s.contact_id
     FROM subscriptions s
     WHERE s.id = ?`
  ).bind(parsed.subscriptionId).first<{ id: string; subscriber_id: string; contact_id: string }>();

  if (!subscription) {
    throw new HttpError(404, "subscription_not_found");
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'active', confirmed_at = COALESCE(confirmed_at, ?), updated_at = ? WHERE id = ?"
  ).bind(now, now, subscription.id).run();
  await env.DB.prepare(
    "UPDATE subscriber_contacts SET status = 'active', verified_at = COALESCE(verified_at, ?), updated_at = ? WHERE id = ?"
  ).bind(now, now, subscription.contact_id).run();
  await recordEvent(env, {
    subscriberId: subscription.subscriber_id,
    subscriptionId: subscription.id,
    eventType: "subscription_confirmed",
    actorType: "subscriber",
    request,
  });

  return htmlPage("Subscription confirmed", "Your GIDS subscription is active.");
}

async function unsubscribe(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("token") || "";
  const parsed = await verifySignedToken(env, token, "unsubscribe");
  if (!parsed) {
    throw new HttpError(400, "invalid_or_expired_token");
  }

  const subscription = await env.DB.prepare(
    "SELECT id, subscriber_id FROM subscriptions WHERE id = ?"
  ).bind(parsed.subscriptionId).first<{ id: string; subscriber_id: string }>();

  if (!subscription) {
    throw new HttpError(404, "subscription_not_found");
  }

  const now = new Date().toISOString();
  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'unsubscribed', updated_at = ? WHERE id = ?"
  ).bind(now, subscription.id).run();
  await recordEvent(env, {
    subscriberId: subscription.subscriber_id,
    subscriptionId: subscription.id,
    eventType: "subscription_unsubscribed",
    actorType: "subscriber",
    request,
  });

  return htmlPage("Unsubscribed", "This GIDS subscription has been stopped.");
}

async function listAudience(request: Request, env: Env): Promise<Response> {
  requireAdmin(request, env);
  const payload = await readPayload(request);
  const listCode = normalizeCode(valueAsString(payload.list_code) || "reports");
  const publicList = await env.DB.prepare(
    "SELECT id FROM subscription_lists WHERE code = ? AND is_public = 1"
  ).bind(listCode).first<{ id: string }>();
  if (!publicList) {
    throw new HttpError(400, "unknown_list");
  }

  const country = normalizeFilter("country", valueAsString(payload.country));
  const disease = normalizeFilter("disease", valueAsString(payload.disease));
  const reportType = normalizeFilter("report_type", valueAsString(payload.report_type));
  const severity = normalizeFilter("severity", valueAsString(payload.severity));
  const requestedLimit = Number(payload.limit ?? 500);
  const limit = Math.min(Math.max(Number.isFinite(requestedLimit) ? requestedLimit : 500, 1), 1000);

  const clauses: string[] = [
    "s.status = 'active'",
    "c.channel = 'email'",
    "c.status = 'active'",
    "l.code = ?",
  ];
  const binds: unknown[] = [listCode];

  for (const filter of [
    { type: "country", value: country },
    { type: "disease", value: disease },
    { type: "report_type", value: reportType },
    { type: "severity", value: severity },
  ]) {
    if (!filter.value) continue;
    clauses.push(
      `(NOT EXISTS (
          SELECT 1 FROM subscription_filters f
          WHERE f.subscription_id = s.id AND f.filter_type = ?
        )
        OR EXISTS (
          SELECT 1 FROM subscription_filters f
          WHERE f.subscription_id = s.id AND f.filter_type = ? AND f.filter_value = ?
        ))`
    );
    binds.push(filter.type, filter.type, filter.value);
  }

  binds.push(limit);
  const query = `
    SELECT
      s.id AS subscription_id,
      s.frequency,
      sub.locale,
      sub.timezone,
      c.address AS email,
      l.code AS list_code
    FROM subscriptions s
    JOIN subscriber_contacts c ON c.id = s.contact_id
    JOIN subscribers sub ON sub.id = s.subscriber_id
    JOIN subscription_lists l ON l.id = s.list_id
    WHERE ${clauses.join(" AND ")}
    ORDER BY s.created_at ASC
    LIMIT ?
  `;

  const rows = await env.DB.prepare(query).bind(...binds).all<{
    subscription_id: string;
    frequency: string;
    locale: string;
    timezone: string;
    email: string;
    list_code: string;
  }>();

  const recipients = await Promise.all((rows.results || []).map(async (row) => ({
    ...row,
    unsubscribe_url: `${publicBaseUrl(request, env)}/api/subscriptions/unsubscribe?token=${
      await createSignedToken(env, "unsubscribe", row.subscription_id, 60 * 60 * 24 * 365)
    }`,
  })));

  return json({ ok: true, recipients }, request, env);
}

async function subscriptionStats(request: Request, env: Env): Promise<Response> {
  requireAdmin(request, env);
  const subscriptionRows = await env.DB.prepare(
    "SELECT status, COUNT(*) AS count FROM subscriptions GROUP BY status ORDER BY status"
  ).all<{ status: string; count: number }>();
  const contactRows = await env.DB.prepare(
    "SELECT status, COUNT(*) AS count FROM subscriber_contacts GROUP BY status ORDER BY status"
  ).all<{ status: string; count: number }>();
  const deliveryRows = await env.DB.prepare(
    `SELECT status, COUNT(*) AS count
     FROM transactional_email_deliveries
     WHERE created_at >= ?
     GROUP BY status
     ORDER BY status`
  ).bind(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString()).all<{ status: string; count: number }>();
  const stalePending = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM subscriptions WHERE status = 'pending' AND created_at < ?"
  ).bind(pendingCutoff(env)).first<{ count: number }>();

  return json({
    ok: true,
    generated_at: new Date().toISOString(),
    subscriptions: rowsToCounts(subscriptionRows.results || []),
    contacts: rowsToCounts(contactRows.results || []),
    deliveries_last_7_days: rowsToCounts(deliveryRows.results || []),
    stale_pending_subscriptions: Number(stalePending?.count || 0),
    pending_expiry_days: configInt(env.PENDING_EXPIRY_DAYS, 14, 1, 365),
  }, request, env);
}

async function listSubscriptionsAdmin(request: Request, env: Env): Promise<Response> {
  requireAdmin(request, env);
  const url = new URL(request.url);
  const requestedStatus = valueAsString(url.searchParams.get("status")).toLowerCase();
  const status = SUBSCRIPTION_STATUSES.has(requestedStatus) ? requestedStatus : "";
  const listCode = normalizeCode(valueAsString(url.searchParams.get("list_code")));
  const search = boundedText(valueAsString(url.searchParams.get("q")), "", 160).toLowerCase();
  const requestedLimit = Number(url.searchParams.get("limit") || 50);
  const requestedOffset = Number(url.searchParams.get("offset") || 0);
  const limit = Math.min(Math.max(Number.isFinite(requestedLimit) ? Math.trunc(requestedLimit) : 50, 1), 250);
  const offset = Math.max(Number.isFinite(requestedOffset) ? Math.trunc(requestedOffset) : 0, 0);

  const clauses: string[] = ["1 = 1"];
  const binds: unknown[] = [];
  if (status) {
    clauses.push("s.status = ?");
    binds.push(status);
  }
  if (listCode) {
    clauses.push("l.code = ?");
    binds.push(listCode);
  }
  if (search) {
    clauses.push("LOWER(c.address) LIKE ? ESCAPE '\\'");
    binds.push(`%${escapeLike(search)}%`);
  }

  const where = clauses.join(" AND ");
  const total = await env.DB.prepare(
    `SELECT COUNT(*) AS count
     FROM subscriptions s
     JOIN subscriber_contacts c ON c.id = s.contact_id
     JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscription_lists l ON l.id = s.list_id
     WHERE ${where}`
  ).bind(...binds).first<{ count: number }>();

  const rows = await env.DB.prepare(
    `SELECT
       s.id AS subscription_id,
       s.status AS subscription_status,
       s.frequency,
       s.source,
       s.created_at,
       s.updated_at,
       s.confirmed_at,
       sub.id AS subscriber_id,
       sub.locale,
       sub.timezone,
       sub.status AS subscriber_status,
       c.id AS contact_id,
       c.address AS email,
       c.status AS contact_status,
       c.verified_at,
       l.code AS list_code,
       l.name AS list_name,
       l.name_zh AS list_name_zh,
       (
         SELECT GROUP_CONCAT(f.filter_type || ':' || f.filter_value, '|')
         FROM subscription_filters f
         WHERE f.subscription_id = s.id
       ) AS filters
     FROM subscriptions s
     JOIN subscriber_contacts c ON c.id = s.contact_id
     JOIN subscribers sub ON sub.id = s.subscriber_id
     JOIN subscription_lists l ON l.id = s.list_id
     WHERE ${where}
     ORDER BY s.created_at DESC
     LIMIT ? OFFSET ?`
  ).bind(...binds, limit, offset).all<{
    subscription_id: string;
    subscription_status: string;
    frequency: string;
    source?: string | null;
    created_at: string;
    updated_at: string;
    confirmed_at?: string | null;
    subscriber_id: string;
    locale: string;
    timezone: string;
    subscriber_status: string;
    contact_id: string;
    email: string;
    contact_status: string;
    verified_at?: string | null;
    list_code: string;
    list_name: string;
    list_name_zh?: string | null;
    filters?: string | null;
  }>();

  return json({
    ok: true,
    generated_at: new Date().toISOString(),
    subscriptions: (rows.results || []).map((row) => ({
      subscription_id: row.subscription_id,
      status: row.subscription_status,
      frequency: row.frequency,
      source: row.source || "",
      created_at: row.created_at,
      updated_at: row.updated_at,
      confirmed_at: row.confirmed_at || null,
      subscriber_id: row.subscriber_id,
      subscriber_status: row.subscriber_status,
      contact_id: row.contact_id,
      email: row.email,
      email_masked: maskEmail(row.email),
      contact_status: row.contact_status,
      verified_at: row.verified_at || null,
      locale: row.locale,
      timezone: row.timezone,
      list_code: row.list_code,
      list_name: row.list_name,
      list_name_zh: row.list_name_zh || row.list_name,
      filters: parseFilterGroups(row.filters || ""),
    })),
    pagination: {
      total: Number(total?.count || 0),
      limit,
      offset,
    },
  }, request, env);
}

async function runMaintenance(env: Env): Promise<JsonValue> {
  const now = new Date().toISOString();
  const cutoff = pendingCutoff(env);

  const pendingSubscriptions = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM subscriptions WHERE status = 'pending' AND created_at < ?"
  ).bind(cutoff).first<{ count: number }>();

  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE status = 'pending' AND created_at < ?"
  ).bind(now, cutoff).run();

  const pendingContacts = await env.DB.prepare(
    `SELECT COUNT(*) AS count
     FROM subscriber_contacts c
     WHERE c.status = 'pending'
       AND c.created_at < ?
       AND NOT EXISTS (
         SELECT 1 FROM subscriptions s
         WHERE s.contact_id = c.id AND s.status IN ('pending', 'active')
       )`
  ).bind(cutoff).first<{ count: number }>();

  await env.DB.prepare(
    `UPDATE subscriber_contacts
     SET status = 'expired', updated_at = ?
     WHERE status = 'pending'
       AND created_at < ?
       AND NOT EXISTS (
         SELECT 1 FROM subscriptions s
         WHERE s.contact_id = subscriber_contacts.id AND s.status IN ('pending', 'active')
       )`
  ).bind(now, cutoff).run();

  return {
    ok: true,
    ran_at: now,
    cutoff,
    expired_subscriptions: Number(pendingSubscriptions?.count || 0),
    expired_contacts: Number(pendingContacts?.count || 0),
  };
}

async function enforceSubmissionRateLimit(request: Request, env: Env): Promise<void> {
  const limit = configInt(env.SUBMISSION_RATE_LIMIT_PER_HOUR, 30, 1, 1000);
  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (!ip || limit <= 0) return;

  const ipHash = await sha256Hex(`${signingSecret(env)}:ip:${ip}`);
  const cutoff = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const recent = await env.DB.prepare(
    `SELECT COUNT(*) AS count
     FROM subscription_events
     WHERE ip_hash = ?
       AND created_at >= ?
       AND event_type IN (
         'subscription_created',
         'subscription_updated',
         'confirmation_email_sent',
         'confirmation_email_failed',
         'confirmation_email_rate_limited'
       )`
  ).bind(ipHash, cutoff).first<{ count: number }>();

  if (Number(recent?.count || 0) >= limit) {
    throw new HttpError(429, "rate_limited");
  }
}

async function confirmationEmailRateLimit(
  env: Env,
  contactId: string,
): Promise<{ allowed: boolean; retryAfterSeconds: number }> {
  const limit = configInt(env.CONFIRMATION_EMAIL_LIMIT_PER_10_MINUTES, 2, 1, 50);
  const cutoffMs = Date.now() - 10 * 60 * 1000;
  const cutoff = new Date(cutoffMs).toISOString();
  const recent = await env.DB.prepare(
    `SELECT COUNT(*) AS count, MIN(created_at) AS oldest
     FROM transactional_email_deliveries
     WHERE contact_id = ?
       AND delivery_type = 'subscription_confirmation'
       AND created_at >= ?
       AND status IN ('queued', 'sent', 'failed')`
  ).bind(contactId, cutoff).first<{ count: number; oldest?: string | null }>();

  const count = Number(recent?.count || 0);
  if (count < limit) {
    return { allowed: true, retryAfterSeconds: 0 };
  }

  const oldestMs = recent?.oldest ? Date.parse(recent.oldest) : cutoffMs;
  const retryAfterSeconds = Math.max(60, Math.ceil((oldestMs + 10 * 60 * 1000 - Date.now()) / 1000));
  return { allowed: false, retryAfterSeconds };
}

function pendingCutoff(env: Env): string {
  const days = configInt(env.PENDING_EXPIRY_DAYS, 14, 1, 365);
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

function rowsToCounts(rows: Array<{ status: string; count: number }>): Record<string, number> {
  return rows.reduce<Record<string, number>>((acc, row) => {
    acc[row.status] = Number(row.count || 0);
    return acc;
  }, {});
}

function parseFilterGroups(value: string): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const item of value.split("|")) {
    const [type, ...rest] = item.split(":");
    const filterValue = rest.join(":");
    if (!type || !filterValue) continue;
    groups[type] = groups[type] || [];
    groups[type].push(filterValue);
  }
  return groups;
}

function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!local || !domain) return email;
  const visible = local.length <= 2 ? local[0] || "" : local.slice(0, 2);
  return `${visible}${"*".repeat(Math.max(2, local.length - visible.length))}@${domain}`;
}

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, "\\$&");
}

function configInt(value: string | undefined, fallback: number, min: number, max: number): number {
  const parsed = Number(value || "");
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

function smtpConfig(env: Env): SmtpConfig | null {
  const host = valueAsString(env.SMTP_HOST);
  const port = Number(valueAsString(env.SMTP_PORT) || "587");
  const username = valueAsString(env.SMTP_USERNAME);
  const password = valueAsString(env.SMTP_PASSWORD);
  const fromEmail = normalizeEmail(valueAsString(env.SMTP_FROM_EMAIL));
  const fromName = boundedText(valueAsString(env.SMTP_FROM_NAME), "GIDS Alerts", 80);
  const useTls = valueAsString(env.SMTP_USE_TLS).toLowerCase() !== "false";

  if (!host || !Number.isFinite(port) || port <= 0 || !username || !password || !fromEmail) {
    return null;
  }

  return {
    host,
    port,
    username,
    password,
    fromEmail,
    fromName,
    useTls,
  };
}

async function sendSmtpEmail(
  config: SmtpConfig,
  email: { to: string; subject: string; text: string; html: string },
): Promise<void> {
  const implicitTls = !config.useTls || config.port === 465 || config.port === 2465;
  const socketOptions = implicitTls
    ? { secureTransport: "on" as const }
    : { secureTransport: "starttls" as const };
  let socket = connect({ hostname: config.host, port: config.port }, socketOptions);
  let session = createSmtpSession(socket);

  await session.expect([220], "CONNECT");
  await session.sendLineExpect(`EHLO ${smtpHeloName(config.fromEmail)}`, [250], "EHLO");

  if (!implicitTls) {
    await session.sendLineExpect("STARTTLS", [220], "STARTTLS");
    session.release();
    socket = socket.startTls();
    session = createSmtpSession(socket);
    await session.sendLineExpect(`EHLO ${smtpHeloName(config.fromEmail)}`, [250], "EHLO_TLS");
  }

  await session.sendLineExpect("AUTH LOGIN", [334], "AUTH_LOGIN");
  await session.sendLineExpect(base64StdEncode(config.username), [334], "AUTH_USERNAME");
  await session.sendLineExpect(base64StdEncode(config.password), [235], "AUTH_PASSWORD");
  await session.sendLineExpect(`MAIL FROM:<${config.fromEmail}>`, [250], "MAIL_FROM");
  await session.sendLineExpect(`RCPT TO:<${email.to}>`, [250, 251], "RCPT_TO");
  await session.sendLineExpect("DATA", [354], "DATA");
  await session.writeData(buildRawEmail(config, email));
  await session.expect([250], "DATA_END");

  try {
    await session.sendLineExpect("QUIT", [221], "QUIT");
  } catch {
    // The message has already been accepted; ignore QUIT failures.
  } finally {
    session.release();
    await socket.close();
  }
}

function createSmtpSession(socket: SmtpSocket) {
  const reader = socket.readable.getReader();
  const writer = socket.writable.getWriter();
  const decoder = new TextDecoder();
  let buffer = "";

  async function readLine(): Promise<string> {
    for (;;) {
      const newlineIndex = buffer.indexOf("\n");
      if (newlineIndex >= 0) {
        const line = buffer.slice(0, newlineIndex + 1);
        buffer = buffer.slice(newlineIndex + 1);
        return line.replace(/\r?\n$/, "");
      }

      const result = await reader.read();
      if (result.done) {
        throw new Error("smtp_connection_closed");
      }
      buffer += decoder.decode(result.value, { stream: true });
    }
  }

  async function readResponse(): Promise<{ code: number; lines: string[]; text: string }> {
    const lines: string[] = [];
    let code = 0;
    for (;;) {
      const line = await readLine();
      lines.push(line);
      const match = /^(\d{3})([\s-])/.exec(line);
      if (match) {
        code = Number(match[1]);
        if (match[2] === " ") {
          break;
        }
      }
    }
    return { code, lines, text: lines.join("\n") };
  }

  function assertResponse(response: { code: number; text: string }, expectedCodes: number[], label: string): void {
    if (!expectedCodes.includes(response.code)) {
      throw new Error(`${label}_rejected:${response.code}`);
    }
  }

  return {
    async expect(expectedCodes: number[], label: string) {
      const response = await readResponse();
      assertResponse(response, expectedCodes, label);
      return response;
    },
    async sendLineExpect(line: string, expectedCodes: number[], label: string) {
      await writer.write(TEXT.encode(`${line}\r\n`));
      const response = await readResponse();
      assertResponse(response, expectedCodes, label);
      return response;
    },
    async writeData(message: string) {
      const normalized = message.replace(/\r?\n/g, "\r\n").replace(/^\./gm, "..");
      await writer.write(TEXT.encode(`${normalized}\r\n.\r\n`));
    },
    release() {
      try {
        reader.releaseLock();
      } catch {
        // ignored
      }
      try {
        writer.releaseLock();
      } catch {
        // ignored
      }
    },
  };
}

function buildConfirmationEmail(locale: string, subscriptions: EmailSubscriptionItem[]): {
  subject: string;
  text: string;
  html: string;
} {
  const lang = locale === "zh" ? "zh" : "en";
  const subject = lang === "zh"
    ? "请确认你的 GIDS 订阅"
    : "Confirm your GIDS subscription";
  const textLines = lang === "zh"
    ? [
        "你好，",
        "",
        "我们已收到你的 GIDS 订阅请求。这封邮件用于确认你的邮箱可以接收 GIDS 更新。",
        "请点击下面的链接确认订阅：",
        "",
        ...subscriptions.flatMap((subscription) => [
          `${listDisplayName(subscription.listCode, "zh")}：${subscription.confirmUrl}`,
        ]),
        "",
        "如果不是你本人操作，可以忽略本邮件。",
        "",
        "GIDS Alerts",
      ]
    : [
        "Hello,",
        "",
        "We received your GIDS subscription request. This message confirms that your inbox can receive GIDS updates.",
        "Confirm your subscription using the link below:",
        "",
        ...subscriptions.flatMap((subscription) => [
          `${listDisplayName(subscription.listCode, "en")}: ${subscription.confirmUrl}`,
        ]),
        "",
        "If you did not request this subscription, you can ignore this email.",
        "",
        "GIDS Alerts",
      ];

  const intro = lang === "zh"
    ? "我们已收到你的 GIDS 订阅请求。这封邮件用于确认你的邮箱可以接收 GIDS 更新。"
    : "We received your GIDS subscription request. This message confirms that your inbox can receive GIDS updates.";
  const action = lang === "zh" ? "确认订阅" : "Confirm subscription";
  const ignore = lang === "zh"
    ? "如果不是你本人操作，可以忽略本邮件。"
    : "If you did not request this subscription, you can ignore this email.";

  const links = subscriptions.map((subscription) => `
    <li style="margin:12px 0">
      <strong>${escapeHtml(listDisplayName(subscription.listCode, lang))}</strong><br>
      <a href="${escapeHtml(subscription.confirmUrl)}" style="display:inline-block;margin-top:6px;color:#0f766e">${escapeHtml(action)}</a>
    </li>
  `).join("");

  const html = `<!doctype html>
<html lang="${lang}">
  <head>
    <meta charset="utf-8">
    <title>${escapeHtml(subject)}</title>
  </head>
  <body style="margin:0;background:#f8fafc;color:#0f172a;font-family:Arial,'Helvetica Neue',sans-serif;line-height:1.6">
    <div style="max-width:620px;margin:0 auto;padding:28px 20px">
      <div style="border:1px solid #dbe5e1;background:#ffffff;padding:24px">
        <p style="margin:0 0 12px;color:#0f766e;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase">GIDS Alerts</p>
        <h1 style="margin:0 0 16px;font-size:24px;line-height:1.25">${escapeHtml(subject)}</h1>
        <p style="margin:0 0 18px">${escapeHtml(intro)}</p>
        <ul style="margin:0 0 18px 20px;padding:0">${links}</ul>
        <p style="margin:18px 0 0;color:#475569;font-size:13px">${escapeHtml(ignore)}</p>
      </div>
      <p style="margin:14px 0 0;color:#64748b;font-size:12px">GIDS - Global Infectious Disease Surveillance</p>
    </div>
  </body>
</html>`;

  return {
    subject,
    text: textLines.join("\n"),
    html,
  };
}

function buildRawEmail(
  config: SmtpConfig,
  email: { to: string; subject: string; text: string; html: string },
): string {
  const boundary = `gids-${crypto.randomUUID()}`;
  const from = `${formatAddressName(config.fromName)} <${config.fromEmail}>`;
  return [
    `From: ${from}`,
    `To: <${email.to}>`,
    `Subject: ${encodeHeader(email.subject)}`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: 8bit",
    "",
    email.text,
    "",
    `--${boundary}`,
    "Content-Type: text/html; charset=UTF-8",
    "Content-Transfer-Encoding: 8bit",
    "",
    email.html,
    "",
    `--${boundary}--`,
  ].join("\r\n");
}

async function insertEmailDelivery(env: Env, input: {
  deliveryId: string;
  subscriberId: string;
  contactId: string;
  subscriptionId: string | null;
  recipient: string;
  subject: string;
  deliveryType: string;
  provider: string;
  status: string;
  attempts: number;
  source: string;
  metadata?: JsonValue;
  errorCode?: string;
  errorMessage?: string;
  now: string;
}): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO transactional_email_deliveries (
         id, subscriber_id, contact_id, subscription_id, delivery_type, channel,
         recipient, subject, provider, status, attempts, request_source,
         error_code, error_message, metadata_json, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, 'email', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(
      input.deliveryId,
      input.subscriberId,
      input.contactId,
      input.subscriptionId,
      input.deliveryType,
      input.recipient,
      input.subject,
      input.provider,
      input.status,
      input.attempts,
      input.source,
      input.errorCode || null,
      input.errorMessage || null,
      input.metadata ? JSON.stringify(input.metadata) : null,
      input.now,
      input.now,
    ).run();
  } catch {
    // Delivery logs are useful but must not block subscription creation.
  }
}

async function updateEmailDelivery(env: Env, input: {
  deliveryId: string;
  status: string;
  sentAt?: string;
  errorCode?: string;
  errorMessage?: string;
}): Promise<void> {
  try {
    await env.DB.prepare(
      `UPDATE transactional_email_deliveries
       SET status = ?, sent_at = COALESCE(?, sent_at), error_code = ?, error_message = ?, updated_at = ?
       WHERE id = ?`
    ).bind(
      input.status,
      input.sentAt || null,
      input.errorCode || null,
      input.errorMessage || null,
      new Date().toISOString(),
      input.deliveryId,
    ).run();
  } catch {
    // Delivery logs are useful but must not block API responses.
  }
}

function listDisplayName(code: string, locale: string): string {
  const labels: Record<string, { en: string; zh: string }> = {
    reports: { en: "Report updates", zh: "报告更新" },
    alerts: { en: "Priority alerts", zh: "重点提醒" },
    weekly_digest: { en: "Weekly digest", zh: "每周摘要" },
  };
  const label = labels[code] || { en: code, zh: code };
  return locale === "zh" ? label.zh : label.en;
}

function smtpHeloName(fromEmail: string): string {
  return fromEmail.split("@")[1] || "globalinfectiousdisease.com";
}

function encodeHeader(value: string): string {
  const safe = value.replace(/[\r\n]+/g, " ").trim();
  return /^[\x20-\x7E]*$/.test(safe) ? safe : `=?UTF-8?B?${base64StdEncode(safe)}?=`;
}

function formatAddressName(value: string): string {
  const safe = value.replace(/[\r\n"]+/g, " ").trim() || "GIDS Alerts";
  return `"${safe}"`;
}

function base64StdEncode(value: string): string {
  const bytes = TEXT.encode(value);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function normalizeListCodes(payload: Payload): string[] {
  const values = toArray(payload.list_codes ?? payload.lists ?? payload.list_code ?? payload.list);
  const normalized = values.map(normalizeCode).filter(Boolean);
  const unique = [...new Set(normalized)];
  return unique.length ? unique : ["reports"];
}

function normalizeFilters(payload: Payload): Array<{ type: string; value: string }> {
  const result: Array<{ type: string; value: string }> = [];
  const specs = [
    { type: "country", keys: ["country", "countries"] },
    { type: "disease", keys: ["disease", "diseases"] },
    { type: "report_type", keys: ["report_type", "report_types"] },
    { type: "severity", keys: ["severity", "severities"] },
  ];

  for (const spec of specs) {
    const values = spec.keys.flatMap((key) => toArray(payload[key]));
    for (const value of values) {
      const normalized = normalizeFilter(spec.type, value);
      if (normalized) {
        result.push({ type: spec.type, value: normalized });
      }
    }
  }

  const seen = new Set<string>();
  return result.filter((item) => {
    const key = `${item.type}:${item.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function normalizeFilter(type: string, value: string): string {
  const text = boundedText(value, "", 120);
  if (!text) return "";
  if (type === "country") return text.toUpperCase();
  return text.toLowerCase();
}

async function readPayload(request: Request): Promise<Payload> {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await request.json() as Payload;
  }

  if (contentType.includes("form")) {
    const form = await request.formData();
    const payload: Payload = {};
    for (const [key, value] of form.entries()) {
      if (typeof value !== "string") continue;
      const existing = payload[key];
      if (Array.isArray(existing)) {
        existing.push(value);
      } else if (typeof existing === "string") {
        payload[key] = [existing, value];
      } else {
        payload[key] = value;
      }
    }
    return payload;
  }

  return {};
}

async function verifyTurnstileIfConfigured(payload: Payload, request: Request, env: Env): Promise<void> {
  if (!env.TURNSTILE_SECRET_KEY) return;

  const token = valueAsString(payload.turnstileToken) || valueAsString(payload["cf-turnstile-response"]);
  if (!token) {
    throw new HttpError(400, "turnstile_token_required");
  }

  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET_KEY);
  form.append("response", token);
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) form.append("remoteip", ip);

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: form,
  });
  const result = await response.json() as { success?: boolean };
  if (!result.success) {
    throw new HttpError(400, "turnstile_verification_failed");
  }
}

async function recordEvent(env: Env, input: {
  subscriberId?: string;
  subscriptionId?: string;
  eventType: string;
  actorType: string;
  request: Request;
  metadata?: JsonValue;
}): Promise<void> {
  const now = new Date().toISOString();
  const ip = input.request.headers.get("CF-Connecting-IP") || "";
  const ipHash = ip ? await sha256Hex(`${signingSecret(env)}:ip:${ip}`) : null;
  await env.DB.prepare(
    `INSERT INTO subscription_events (
       id, subscriber_id, subscription_id, event_type, actor_type, ip_hash,
       user_agent, metadata_json, created_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    crypto.randomUUID(),
    input.subscriberId || null,
    input.subscriptionId || null,
    input.eventType,
    input.actorType,
    ipHash,
    boundedText(input.request.headers.get("user-agent") || "", "", 500),
    input.metadata ? JSON.stringify(input.metadata) : null,
    now,
  ).run();
}

async function createSignedToken(
  env: Env,
  kind: "confirm" | "unsubscribe",
  subscriptionId: string,
  ttlSeconds: number,
): Promise<string> {
  const payload = base64UrlEncode(JSON.stringify({
    k: kind,
    sid: subscriptionId,
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
  }));
  const signature = await hmacSha256(payload, signingSecret(env));
  return `${payload}.${signature}`;
}

async function verifySignedToken(
  env: Env,
  token: string,
  kind: "confirm" | "unsubscribe",
): Promise<{ subscriptionId: string } | null> {
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return null;
  const expected = await hmacSha256(payload, signingSecret(env));
  if (!constantTimeEqual(signature, expected)) return null;

  try {
    const parsed = JSON.parse(base64UrlDecode(payload)) as { k?: string; sid?: string; exp?: number };
    if (parsed.k !== kind || !parsed.sid || !parsed.exp) return null;
    if (parsed.exp < Math.floor(Date.now() / 1000)) return null;
    return { subscriptionId: parsed.sid };
  } catch {
    return null;
  }
}

async function hmacSha256(value: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    TEXT.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, TEXT.encode(value));
  return base64UrlEncode(signature);
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", TEXT.encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function signingSecret(env: Env): string {
  if (!env.TOKEN_SIGNING_SECRET) {
    throw new HttpError(500, "TOKEN_SIGNING_SECRET_not_configured");
  }
  return env.TOKEN_SIGNING_SECRET;
}

function requireAdmin(request: Request, env: Env): void {
  if (!env.ADMIN_API_TOKEN) {
    throw new HttpError(500, "ADMIN_API_TOKEN_not_configured");
  }
  const expected = `Bearer ${env.ADMIN_API_TOKEN}`;
  if (request.headers.get("authorization") !== expected) {
    throw new HttpError(401, "unauthorized");
  }
}

function publicBaseUrl(request: Request, env: Env): string {
  return (env.PUBLIC_BASE_URL || new URL(request.url).origin).replace(/\/+$/, "");
}

function htmlPage(title: string, message: string): Response {
  const body = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${escapeHtml(title)} | GIDS</title>
    <style>
      body{margin:0;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0}
      main{min-height:100vh;display:grid;place-items:center;padding:24px}
      section{max-width:560px}
      h1{font-size:32px;line-height:1.1;margin:0 0 12px}
      p{color:#94a3b8;font-size:16px;line-height:1.6;margin:0}
    </style>
  </head>
  <body>
    <main>
      <section>
        <h1>${escapeHtml(title)}</h1>
        <p>${escapeHtml(message)}</p>
      </section>
    </main>
  </body>
</html>`;
  return new Response(body, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function json(data: JsonValue, request: Request, env: Env, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...corsHeaders(request, env),
    },
  });
}

function corsHeaders(request: Request, env: Env): HeadersInit {
  const origin = request.headers.get("origin") || "";
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((item) => item.trim()).filter(Boolean);
  const allowOrigin = allowed.length === 0 || allowed.includes(origin) ? (origin || "*") : allowed[0];
  return {
    "access-control-allow-origin": allowOrigin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,authorization",
    "access-control-max-age": "86400",
  };
}

function trimTrailingSlash(path: string): string {
  if (path === "/") return path;
  return path.replace(/\/+$/, "");
}

function normalizeEmail(value: string): string {
  const email = value.trim().toLowerCase();
  if (!email || email.length > 254) return "";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "";
  return email;
}

function normalizeCode(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9_:-]/g, "").slice(0, 80);
}

function pick(value: string, allowed: Set<string>, fallback: string): string {
  const normalized = value.trim().toLowerCase();
  return allowed.has(normalized) ? normalized : fallback;
}

function boundedText(value: string, fallback: string, maxLength: number): string {
  const text = value.trim();
  return text ? text.slice(0, maxLength) : fallback;
}

function valueAsString(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}

function toArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => toArray(item));
  }
  if (typeof value === "string") {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function base64UrlEncode(value: string | ArrayBuffer): string {
  let binary = "";
  if (typeof value === "string") {
    binary = value;
  } else {
    for (const byte of new Uint8Array(value)) {
      binary += String.fromCharCode(byte);
    }
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(value: string): string {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return atob(padded);
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let index = 0; index < a.length; index += 1) {
    result |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return result === 0;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
