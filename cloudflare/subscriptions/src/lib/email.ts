import { escapeHtml, normalizeLocale } from "./input.ts";
import { cleanHeaderValue, cleanMarkdown, markdownToHtml } from "./markdown.ts";

const TEXT = new TextEncoder();

export interface EmailSubscriptionItem {
  id: string;
  listCode: string;
  frequency: string;
  confirmUrl: string;
}

export interface EmailContent {
  subject: string;
  text: string;
  html: string;
  unsubscribeUrl?: string;
}

export interface RawEmailConfig {
  fromEmail: string;
  fromName: string;
  configurationSet?: string;
}

export interface NotificationEmailContent {
  subject: string;
  markdown: string;
}

export type NotificationKind = "update" | "report" | "research" | "alert";

export interface ManagementEmailLink {
  name: string;
  url: string;
}

const BRAND = "GIDS - Global Infectious Disease Surveillance";

/** Inline styles and presentation tables work in clients that remove CSS. */
function emailLayout(locale: string, subject: string, section: string, body: string, footer: string): string {
  const lang = normalizeLocale(locale, "en") || "en";
  return `<!doctype html>
<html lang="${escapeHtml(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>${escapeHtml(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f6f5;color:#172e2d;font-family:Arial,'Helvetica Neue',sans-serif;word-break:break-word">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background-color:#f2f6f5">
<tr><td align="center" style="padding:28px 12px">
<!--[if mso]><table role="presentation" width="640" cellspacing="0" cellpadding="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;max-width:640px;background-color:#ffffff;border:1px solid #d9e6e2">
<tr><td style="padding:22px 28px 18px;background-color:#123d3a;color:#ffffff">
<p style="margin:0;font-size:20px;line-height:26px;font-weight:bold;letter-spacing:1px">GIDS</p>
<p style="margin:3px 0 0;font-size:12px;line-height:18px;color:#d5eae4">Global Infectious Disease Surveillance</p>
</td></tr>
<tr><td style="padding:28px 28px 12px;font-size:16px;line-height:1.6">
<p style="margin:0 0 10px;font-size:12px;line-height:18px;color:#176b60;font-weight:bold;letter-spacing:0.5px">${escapeHtml(section)}</p>
<h1 style="margin:0 0 22px;font-size:25px;line-height:1.3;color:#163b37">${escapeHtml(subject)}</h1>
${body}
</td></tr>
<tr><td style="padding:18px 28px 24px;border-top:1px solid #e3ece9;font-size:13px;line-height:1.6;color:#526965">${footer}</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
<p style="margin:16px 0 0;font-size:12px;line-height:18px;color:#526965">${BRAND}</p>
</td></tr></table>
</body></html>`;
}

function actionLink(label: string, url: string): string {
  return `<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;margin:8px 0 16px"><tr><td bgcolor="#176b60" style="background-color:#176b60"><a href="${escapeHtml(url)}" style="display:inline-block;padding:12px 20px;color:#ffffff;font-size:15px;font-weight:bold;line-height:20px;text-decoration:none">${escapeHtml(label)}</a></td></tr></table>`;
}

function plainMarkdown(markdown: string): string {
  return markdown
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, "$1 ($2)")
    .replace(/^#{1,4}\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\x60([^\x60]+)\x60/g, "$1");
}

export function buildNotificationEmail(
  locale: string,
  content: NotificationEmailContent,
  unsubscribeUrl: string,
  manageUrl?: string,
  kind: NotificationKind = "update",
): EmailContent {
  const normalized = normalizeLocale(locale, "en");
  const labels = notificationTemplateLabels(normalized);
  const subject = cleanHeaderValue(content.subject, 200) || labels.title;
  const markdown = cleanMarkdown(content.markdown);
  const manageLabel = normalized === "zh" ? "管理订阅偏好" : normalized === "fr" ? "Gérer les préférences" : "Manage preferences";
  const sections: Record<NotificationKind, { en: string; zh: string }> = {
    update: { en: "GIDS UPDATE", zh: "GIDS 更新" },
    report: { en: "SURVEILLANCE REPORTS", zh: "监测报告" },
    research: { en: "RESEARCH RADAR", zh: "研究雷达" },
    alert: { en: "OFFICIAL NOTICE", zh: "官方通报" },
  };
  const section = sections[kind][normalized === "zh" ? "zh" : "en"];
  const text = [subject, "", plainMarkdown(markdown), "", labels.reason,
    `${labels.unsubscribe}: ${unsubscribeUrl}`,
    ...(manageUrl ? [`${manageLabel}: ${manageUrl}`] : []), "", BRAND].join("\n");
  const footer = `<p style="margin:0 0 8px">${escapeHtml(labels.reason)}</p>
<p style="margin:0"><a href="${escapeHtml(unsubscribeUrl)}" style="color:#176b60;text-decoration:underline">${escapeHtml(labels.unsubscribe)}</a>${manageUrl ? ` &nbsp;·&nbsp; <a href="${escapeHtml(manageUrl)}" style="color:#176b60;text-decoration:underline">${escapeHtml(manageLabel)}</a>` : ""}</p>`;
  return { subject, text, html: emailLayout(normalized, subject, section, markdownToHtml(markdown), footer), unsubscribeUrl };
}

export function buildConfirmationEmail(locale: string, subscriptions: EmailSubscriptionItem[]): EmailContent {
  const lang = normalizeLocale(locale, "en") === "zh" ? "zh" : "en";
  const zh = lang === "zh";
  const subject = zh ? "请确认你的 GIDS 订阅" : "Confirm your GIDS subscription";
  const intro = zh ? "我们收到了你的订阅请求。请分别确认以下订阅，确认后才会收到对应更新。"
    : "We received your subscription request. Confirm each subscription below to start receiving its updates.";
  const action = zh ? "确认订阅" : "Confirm subscription";
  const ignore = zh ? "如果不是你本人操作，可以忽略本邮件。" : "If you did not request this subscription, you can ignore this email.";
  const frequencies: Record<string, { en: string; zh: string }> = {
    instant: { en: "Instant", zh: "即时" }, weekly: { en: "Weekly", zh: "每周" }, monthly: { en: "Monthly", zh: "每月" },
  };
  const frequency = (item: EmailSubscriptionItem) => frequencies[item.frequency]?.[lang] || "";
  const itemsText = subscriptions.flatMap(item => [
    `${listDisplayName(item.listCode, lang)}${zh ? "：" : ": "}${item.confirmUrl}`,
    ...(frequency(item) ? [`${zh ? "频率" : "Frequency"}: ${frequency(item)}`] : []),
  ]);
  const itemsHtml = subscriptions.map(item => `<div style="margin:0 0 18px;padding:0 0 12px;border-bottom:1px solid #e3ece9"><p style="margin:0 0 4px;font-size:16px;font-weight:bold">${escapeHtml(listDisplayName(item.listCode, lang))}</p>${frequency(item) ? `<p style="margin:0;color:#526965;font-size:14px">${zh ? "频率" : "Frequency"}: ${escapeHtml(frequency(item))}</p>` : ""}${actionLink(`${action}: ${listDisplayName(item.listCode, lang)}`, item.confirmUrl)}</div>`).join("");
  const text = [subject, "", intro, "", ...itemsText, "", ignore, "", BRAND].join("\n");
  const html = emailLayout(lang, subject, zh ? "订阅确认" : "SUBSCRIPTION CONFIRMATION",
    `<p style="margin:0 0 20px">${escapeHtml(intro)}</p>${itemsHtml}`,
    `<p style="margin:0">${escapeHtml(ignore)}</p>`);
  return { subject, text, html };
}

export function buildManagementEmail(locale: string, links: ManagementEmailLink[]): EmailContent {
  const lang = normalizeLocale(locale, "en") === "zh" ? "zh" : "en";
  const zh = lang === "zh";
  const subject = zh ? "管理你的 GIDS 订阅" : "Manage your GIDS subscriptions";
  const intro = zh ? "你可以修改偏好、暂停或退订。请选择要管理的订阅。"
    : "Choose a subscription to change preferences, pause, or unsubscribe.";
  const expiry = zh ? "这些链接将在 30 分钟后失效。如果不是你本人操作，可以忽略本邮件。"
    : "These links expire in 30 minutes. If you did not request them, you can ignore this email.";
  const action = zh ? "管理订阅" : "Manage subscription";
  const text = [subject, "", intro, "", ...links.map(link => `${link.name}: ${link.url}`), "", expiry, "", BRAND].join("\n");
  const body = `<p style="margin:0 0 20px">${escapeHtml(intro)}</p>` + links.map(link =>
    `<div style="margin:0 0 18px;padding:0 0 12px;border-bottom:1px solid #e3ece9"><p style="margin:0 0 4px;font-size:16px;font-weight:bold">${escapeHtml(link.name)}</p>${actionLink(`${action}: ${link.name}`, link.url)}</div>`).join("");
  const html = emailLayout(lang, subject, zh ? "订阅管理" : "SUBSCRIPTION MANAGEMENT", body,
    `<p style="margin:0">${escapeHtml(expiry)}</p>`);
  return { subject, text, html };
}

export function buildRawEmail(
  config: RawEmailConfig,
  email: EmailContent & { to: string; messageId?: string },
): string {
  const boundary = `gids-${crypto.randomUUID()}`;
  const from = `${formatAddressName(config.fromName)} <${cleanAddressHeader(config.fromEmail)}>`;
  const unsubscribe = safeUnsubscribeUrl(email.unsubscribeUrl);
  const correlation = email.messageId?.split("@")[0] || "";
  const safeCorrelation = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(correlation) ? correlation : "";
  const sesSet = config.configurationSet && /^[A-Za-z0-9_-]{1,64}$/.test(config.configurationSet) ? config.configurationSet : "";
  return [
    `From: ${from}`,
    `To: <${cleanAddressHeader(email.to)}>`,
    `Subject: ${encodeHeader(email.subject)}`,
    ...(email.messageId ? [`Message-ID: <${cleanMessageId(email.messageId)}>`] : []),
    ...(safeCorrelation ? [`X-GIDS-Delivery-ID: ${safeCorrelation}`] : []),
    ...(sesSet ? [`X-SES-CONFIGURATION-SET: ${sesSet}`] : []),
    ...(sesSet && safeCorrelation ? [`X-SES-MESSAGE-TAGS: gids-delivery=${safeCorrelation}`] : []),
    ...(unsubscribe ? [
      `List-Unsubscribe: <${unsubscribe}>`,
      "List-Unsubscribe-Post: List-Unsubscribe=One-Click",
    ] : []),
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

function safeUnsubscribeUrl(value: string | undefined): string | null {
  if (!value || /[\r\n<>\s]/.test(value) || value.length > 1800) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

function cleanMessageId(value: string): string {
  const cleaned = value.replace(/[^A-Za-z0-9._@+-]+/g, "");
  const at = cleaned.indexOf("@");
  if (at < 1) return cleaned.replace(/@/g, "").slice(0, 240);
  return `${cleaned.slice(0, at + 1)}${cleaned.slice(at + 1).replace(/@/g, "")}`.slice(0, 240);
}

function notificationTemplateLabels(locale: string): { title: string; reason: string; unsubscribe: string } {
  const normalized = normalizeLocale(locale, "en");
  const labels: Record<string, { title: string; reason: string; unsubscribe: string }> = {
    en: {
      title: "GIDS Update",
      reason: "You are receiving this message because you subscribed to GIDS updates.",
      unsubscribe: "Unsubscribe",
    },
    zh: { title: "GIDS 更新通知", reason: "你收到这封邮件，是因为你订阅了 GIDS 更新。", unsubscribe: "退订" },
    ja: { title: "GIDS 更新", reason: "GIDS の更新を購読しているため、このメールをお送りしています。", unsubscribe: "購読解除" },
    ko: { title: "GIDS 업데이트", reason: "GIDS 업데이트를 구독하셨기 때문에 이 메일을 보내드립니다.", unsubscribe: "구독 해지" },
    es: { title: "Actualización de GIDS", reason: "Recibes este mensaje porque te suscribiste a las actualizaciones de GIDS.", unsubscribe: "Cancelar suscripción" },
    fr: { title: "Mise a jour GIDS", reason: "Vous recevez ce message car vous etes abonne aux mises a jour de GIDS.", unsubscribe: "Se desabonner" },
    de: { title: "GIDS Update", reason: "Sie erhalten diese Nachricht, weil Sie GIDS Updates abonniert haben.", unsubscribe: "Abbestellen" },
    pt: { title: "Atualizacao GIDS", reason: "Voce esta recebendo esta mensagem porque assinou as atualizacoes do GIDS.", unsubscribe: "Cancelar assinatura" },
  };
  return labels[normalized] || labels.en;
}

function listDisplayName(code: string, locale: string): string {
  const labels: Record<string, { en: string; zh: string }> = {
    reports: { en: "Report updates", zh: "报告更新" },
    alerts: { en: "Priority alerts", zh: "重点提醒" },
    weekly_digest: { en: "Weekly digest", zh: "每周摘要" },
    research_digest: { en: "Research Radar", zh: "研究雷达" },
  };
  const label = labels[code] || { en: code, zh: code };
  return locale === "zh" ? label.zh : label.en;
}

function cleanAddressHeader(value: string): string {
  return value.replace(/[\r\n]+/g, " ").trim();
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
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
