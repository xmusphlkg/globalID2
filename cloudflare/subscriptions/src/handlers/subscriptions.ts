import { SUPPORTED_LOCALES, boundedText, normalizeCode, valueAsString } from "../lib/input.ts";
import { HttpError, configInt, json, publicBaseUrl, requireAdmin, type JsonValue } from "../lib/http.ts";
import { FREQUENCIES, LOCALE_LABELS, SUBSCRIPTION_STATUSES, escapeLike, maskEmail, normalizeFilter, parseFilterGroups, pendingCutoff, rowsToCounts } from "../lib/subscriptions.ts";
import type { Env, Payload } from "../types.ts";

export type SubscriptionHandlerDependencies = {
  readPayload(request: Request): Promise<Payload>;
  createSignedToken(env: Env, purpose: string, subscriptionId: string, ttlSeconds: number): Promise<string>;
};

export async function listSubscriptionOptions(request: Request, env: Env): Promise<Response> {
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
    locales: SUPPORTED_LOCALES.map((locale) => ({
      value: locale,
      label_en: LOCALE_LABELS[locale]?.label_en || locale,
      label_zh: LOCALE_LABELS[locale]?.label_zh || locale,
    })),
    frequencies: [
      { value: "weekly", label_en: "Weekly", label_zh: "每周" },
      { value: "monthly", label_en: "Monthly", label_zh: "每月" },
      { value: "instant", label_en: "Instant", label_zh: "即时" },
    ],
    filters,
  }, request, env);
}

export async function listAudience(request: Request, env: Env, deps: SubscriptionHandlerDependencies): Promise<Response> {
  requireAdmin(request, env);
  const payload = await deps.readPayload(request);
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
      await deps.createSignedToken(env, "unsubscribe", row.subscription_id, 60 * 60 * 24 * 365)
    }`,
  })));

  return json({ ok: true, recipients }, request, env);
}

export async function subscriptionStats(request: Request, env: Env): Promise<Response> {
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
  ).bind(pendingCutoff(env.PENDING_EXPIRY_DAYS)).first<{ count: number }>();

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

export async function listSubscriptionsAdmin(request: Request, env: Env): Promise<Response> {
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
