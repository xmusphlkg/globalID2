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
}

export interface RawEmailConfig {
  fromEmail: string;
  fromName: string;
}

export interface NotificationEmailContent {
  subject: string;
  markdown: string;
}

export function buildNotificationEmail(
  locale: string,
  content: NotificationEmailContent,
  unsubscribeUrl: string,
): EmailContent {
  const labels = notificationTemplateLabels(locale);
  const subject = cleanHeaderValue(content.subject, 200) || labels.title;
  const markdown = cleanMarkdown(content.markdown);
  const bodyHtml = markdownToHtml(markdown);
  const text = [
    subject,
    "",
    markdown,
    "",
    `${labels.unsubscribe}: ${unsubscribeUrl}`,
    "",
    "GIDS - Global Infectious Disease Surveillance",
  ].join("\n");
  const html = `<!doctype html>
<html lang="${escapeHtml(locale)}">
  <head>
    <meta charset="utf-8">
    <title>${escapeHtml(subject)}</title>
  </head>
  <body style="margin:0;background:#f8fafc;color:#0f172a;font-family:Arial,'Helvetica Neue',sans-serif;line-height:1.6">
    <div style="max-width:680px;margin:0 auto;padding:28px 20px">
      <div style="border:1px solid #dbe5e1;background:#ffffff;padding:26px">
        <p style="margin:0 0 12px;color:#0f766e;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase">GIDS Alerts</p>
        <h1 style="margin:0 0 18px;font-size:24px;line-height:1.25">${escapeHtml(subject)}</h1>
        <div style="font-size:15px;color:#0f172a">${bodyHtml}</div>
        <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0 14px">
        <p style="margin:0;color:#64748b;font-size:12px">${escapeHtml(labels.reason)}</p>
        <p style="margin:8px 0 0;color:#64748b;font-size:12px">
          <a href="${escapeHtml(unsubscribeUrl)}" style="color:#0f766e">${escapeHtml(labels.unsubscribe)}</a>
        </p>
      </div>
      <p style="margin:14px 0 0;color:#64748b;font-size:12px">GIDS - Global Infectious Disease Surveillance</p>
    </div>
  </body>
</html>`;

  return { subject, text, html };
}

export function buildConfirmationEmail(
  locale: string,
  subscriptions: EmailSubscriptionItem[],
): EmailContent {
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

  return { subject, text: textLines.join("\n"), html };
}

export function buildRawEmail(config: RawEmailConfig, email: EmailContent & { to: string }): string {
  const boundary = `gids-${crypto.randomUUID()}`;
  const from = `${formatAddressName(config.fromName)} <${cleanAddressHeader(config.fromEmail)}>`;
  return [
    `From: ${from}`,
    `To: <${cleanAddressHeader(email.to)}>`,
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
