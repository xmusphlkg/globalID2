import { recipientSql } from "./pilot.ts";

import { boundedText, isRecord, valueAsString } from "./input.ts";
import { cleanMarkdown } from "./markdown.ts";
import { normalizeFilters } from "./subscriptions.ts";

import type { Env, Payload } from "../types.ts";

export type ContentItem = { id: string; filters: Array<{ type: string; value: string }>; localized_markdown?: Record<string, string> };
export const CONTENT_FILTER_TYPES = new Set(["country", "disease", "research_topic", "study_type", "peer_review_status"]);
export const PERSONALIZED_MARKER = "{{GIDS_PERSONALIZED_ITEMS}}";

export function contentItems(payload: Payload): ContentItem[] | null {
  if (payload.content_items === undefined) return null;
  if (!Array.isArray(payload.content_items) || payload.content_items.length < 1 || payload.content_items.length > 100) {
    throw new Error("invalid_content_items");
  }
  const ids = new Set<string>();
  const items = payload.content_items.map((item) => {
    if (!isRecord(item)) throw new Error("invalid_content_items");
    const id = boundedText(valueAsString(item.id), "", 200);
    if (!id || ids.has(id)) throw new Error("invalid_content_items");
    ids.add(id);
    const filters = normalizeFilters(item).filter((filter) => CONTENT_FILTER_TYPES.has(filter.type));
    if (filters.length > 200) throw new Error("invalid_content_items");
    const localized: Record<string, string> = {};
    if (item.localized_markdown !== undefined) {
      if (!isRecord(item.localized_markdown)) throw new Error("invalid_content_items");
      for (const [locale, value] of Object.entries(item.localized_markdown)) {
        if (!['en', 'zh'].includes(locale) || typeof value !== "string" || value.length > 20000 || !cleanMarkdown(value) || value.includes(PERSONALIZED_MARKER)) throw new Error("invalid_content_items");
        localized[locale] = cleanMarkdown(value);
      }
    }
    return { id, filters: filters.sort((a, b) => `${a.type}:${a.value}`.localeCompare(`${b.type}:${b.value}`)),
      ...(Object.keys(localized).length ? { localized_markdown: localized } : {}) };
  }).sort((a, b) => a.id.localeCompare(b.id));
  if (new TextEncoder().encode(JSON.stringify(items)).length > 1_000_000) throw new Error("invalid_content_items");
  return items;
}

export function personalizedTemplates(payload: Payload, items: ContentItem[] | null, locales: string[]): Record<string, string> | null {
  if (payload.personalized_templates === undefined) {
    if (items?.some(item => item.localized_markdown)) throw new Error("invalid_personalization");
    return null;
  }
  if (!items || !isRecord(payload.personalized_templates) || locales.length === 0) throw new Error("invalid_personalization");
  const templates: Record<string, string> = {};
  for (const locale of locales) {
    const value = payload.personalized_templates[locale];
    if (!['en', 'zh'].includes(locale) || typeof value !== "string" || value.length > 50000 || value.split(PERSONALIZED_MARKER).length !== 2 || !items.every(item => item.localized_markdown?.[locale])) throw new Error("invalid_personalization");
    // buildNotificationEmail bounds markdown to 50,000 characters. Check the
    // largest possible issue before queueing so neither a matching item nor
    // source/disclosure text can be silently truncated for any recipient.
    const length = value.length - PERSONALIZED_MARKER.length
      + items.reduce((sum, item) => sum + item.localized_markdown![locale].length, 0)
      + Math.max(0, items.length - 1) * 2;
    if (length > 50000) throw new Error("invalid_personalization");
    templates[locale] = cleanMarkdown(value);
  }
  if (Object.keys(payload.personalized_templates).some(locale => !locales.includes(locale))) throw new Error("invalid_personalization");
  return templates;
}

export function matchedItems(items: ContentItem[], filters: Array<{ filter_type: string; filter_value: string }>): ContentItem[] {
  const selected = new Map<string, Set<string>>();
  for (const filter of filters) {
    if (!CONTENT_FILTER_TYPES.has(filter.filter_type)) continue;
    if (!selected.has(filter.filter_type)) selected.set(filter.filter_type, new Set());
    selected.get(filter.filter_type)!.add(filter.filter_value);
  }
  return items.filter(item => [...selected].every(([type, values]) =>
    item.filters.some(fact => fact.type === type && values.has(fact.value))));
}

export function retryChangedPreferences(env: Env, delivery: { delivery_id: string; preference_revision: string | null },
                                 claimToken: string, frequency: string) {
  return env.DB.prepare(`UPDATE message_deliveries
    SET status = CASE WHEN attempts >= 5 THEN 'failed' ELSE 'queued' END,
    last_error = CASE WHEN attempts >= 5 THEN 'preference_changed_too_often' ELSE 'preference_changed_retry' END,
    failed_at = CASE WHEN attempts >= 5 THEN ? ELSE NULL END,
    transaction_delivery_id = NULL, claim_token = NULL, claim_expires_at = NULL
    WHERE id = ? AND status = 'sending' AND claim_token = ? AND claim_expires_at > ?
    AND EXISTS (SELECT 1 FROM subscriptions s JOIN subscriber_contacts c ON c.id = s.contact_id
      JOIN subscribers sub ON sub.id = s.subscriber_id
      WHERE s.id = message_deliveries.subscription_id AND s.status = 'active'
        AND c.status = 'active' AND sub.status = 'active'
        AND COALESCE(s.preference_revision, '') <> ? AND (? = '' OR s.frequency = ?))`)
    .bind(new Date().toISOString(), delivery.delivery_id, claimToken, new Date().toISOString(),
      delivery.preference_revision || "", frequency, frequency);
}

export async function audience(
  env: Env,
  codes: string[],
  limit: number,
  filters: Array<{ type: string; value: string }> = [],
  frequency = "",
  items: ContentItem[] | null = null,
) {
  const clauses = ["s.status = 'active'", "c.channel = 'email'", "c.status = 'active'", "sub.status = 'active'"];
  const binds: unknown[] = [];
  const policy = recipientSql(env, "c.address");
  clauses.push(policy.sql); binds.push(...policy.binds);
  if (codes.length) { clauses.push(`l.code IN (${codes.map(() => "?").join(", ")})`); binds.push(...codes); }
  if (frequency) { clauses.push("s.frequency = ?"); binds.push(frequency); }
  const grouped = new Map<string, string[]>();
  for (const filter of filters) {
    if (items && CONTENT_FILTER_TYPES.has(filter.type)) continue;
    const values = grouped.get(filter.type) || [];
    if (!values.includes(filter.value)) values.push(filter.value);
    grouped.set(filter.type, values);
  }
  for (const [type, values] of grouped) {
    clauses.push(
      `(NOT EXISTS (
        SELECT 1 FROM subscription_filters f
        WHERE f.subscription_id = s.id AND f.filter_type = ?
      ) OR EXISTS (
        SELECT 1 FROM subscription_filters f
        WHERE f.subscription_id = s.id AND f.filter_type = ?
          AND f.filter_value IN (${values.map(() => "?").join(", ")})
      ))`,
    );
    binds.push(type, type, ...values);
  }
  if (items) {
    // A subscriber matches when one item satisfies every selected filter
    // dimension. Values within one dimension are alternatives (OR). Missing
    // item metadata does not match a subscriber's explicit preference.
    clauses.push(`EXISTS (
      SELECT 1 FROM json_each(?) AS item
      WHERE NOT EXISTS (
        SELECT f.filter_type FROM subscription_filters f
        WHERE f.subscription_id = s.id
          AND f.filter_type IN ('country', 'disease', 'research_topic', 'study_type', 'peer_review_status')
        GROUP BY f.filter_type
        HAVING NOT EXISTS (
          SELECT 1 FROM subscription_filters matched
          JOIN json_each(item.value, '$.filters') AS fact
            ON json_extract(fact.value, '$.type') = matched.filter_type
           AND json_extract(fact.value, '$.value') = matched.filter_value
          WHERE matched.subscription_id = s.id AND matched.filter_type = f.filter_type
        )
      )
    )`);
    // The selection query needs only facts; keep localized email bodies out of
    // the SQL argument, especially for larger bilingual research issues.
    binds.push(JSON.stringify(items.map(({ id, filters: facts }) => ({ id, filters: facts }))));
  }
  binds.push(limit);
  const rows = await env.DB.prepare(
    `SELECT MIN(s.id) AS subscription_id, MIN(s.subscriber_id) AS subscriber_id, s.contact_id AS contact_id,
     c.address AS email, COALESCE(NULLIF(sub.locale, ''), 'en') AS locale, MIN(s.created_at) AS first_subscription_at
     FROM subscriptions s JOIN subscriber_contacts c ON c.id = s.contact_id
     JOIN subscribers sub ON sub.id = s.subscriber_id JOIN subscription_lists l ON l.id = s.list_id
     WHERE ${clauses.join(" AND ")} GROUP BY s.contact_id, c.address, sub.locale
     ORDER BY first_subscription_at ASC LIMIT ?`,
  ).bind(...binds).all<{ subscription_id: string; subscriber_id: string; contact_id: string; email: string; locale: string }>();
  return (rows.results || []).filter((row) => row.subscription_id && row.contact_id && row.email);
}
