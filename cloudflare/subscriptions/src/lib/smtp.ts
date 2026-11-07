import { connect } from "cloudflare:sockets";
import { recipientAllowed } from "./pilot.ts";
import { normalizeEmail } from "./input.ts";
import { buildRawEmail, type EmailContent } from "./email.ts";
import { type SmtpConfig } from "./smtp-config.ts";
import { parseSmtpProviderMessageId } from "./smtp-response.ts";
export { smtpConfig, type SmtpConfig, type SmtpEnvironment } from "./smtp-config.ts";

const TEXT = new TextEncoder();
const SMTP_OPERATION_TIMEOUT_MS = 15_000;
// waitUntil work has a 30-second post-response grace period. Leave time for
// the outbox to persist an uncertain result before the runtime can end it.
const SMTP_TOTAL_TIMEOUT_MS = 24_000;
const SMTP_MAX_RESPONSE_BYTES = 65_536;
type SmtpSocket = ReturnType<typeof connect>;

export async function sendSmtpEmail(
  config: SmtpConfig,
  email: EmailContent & { to: string; messageId?: string },
): Promise<{ providerMessageId: string | null }> {
  const recipient = normalizeEmail(email.to);
  if (!recipient) throw new Error("invalid_smtp_recipient");
  if (!recipientAllowed({ DELIVERY_MODE: config.deliveryMode, PILOT_RECIPIENTS: config.pilotRecipients }, recipient)) throw new Error("pilot_recipient_blocked");

  const implicitTls = !config.useTls || config.port === 465 || config.port === 2465;
  const socketOptions = implicitTls
    ? { secureTransport: "on" as const, allowHalfOpen: false }
    : { secureTransport: "starttls" as const, allowHalfOpen: false };
  let socket = connect({ hostname: config.host, port: config.port }, socketOptions);
  const deadline = Date.now() + SMTP_TOTAL_TIMEOUT_MS;
  let session = createSmtpSession(socket, deadline);

  try {
    await session.expect([220], "CONNECT");
    await session.sendLineExpect(`EHLO ${smtpHeloName(config.fromEmail)}`, [250], "EHLO");
    if (!implicitTls) {
      await session.sendLineExpect("STARTTLS", [220], "STARTTLS");
      session.release();
      socket = socket.startTls();
      session = createSmtpSession(socket, deadline);
      await session.sendLineExpect(`EHLO ${smtpHeloName(config.fromEmail)}`, [250], "EHLO_TLS");
    }

    await session.sendLineExpect("AUTH LOGIN", [334], "AUTH_LOGIN");
    await session.sendLineExpect(base64StdEncode(config.username), [334], "AUTH_USERNAME");
    await session.sendLineExpect(base64StdEncode(config.password), [235], "AUTH_PASSWORD");
    await session.sendLineExpect(`MAIL FROM:<${config.fromEmail}>`, [250], "MAIL_FROM");
    await session.sendLineExpect(`RCPT TO:<${recipient}>`, [250, 251], "RCPT_TO");
    await session.sendLineExpect("DATA", [354], "DATA");
    await session.writeData(buildRawEmail(config, { ...email, to: recipient }));
    const accepted = await session.expect([250], "DATA_END");
    try {
      await session.sendLineExpect("QUIT", [221], "QUIT");
    } catch {
      // The message has already been accepted; ignore QUIT failures.
    }
    return { providerMessageId: parseSmtpProviderMessageId(accepted.text) || email.messageId || null };
  } finally {
    session.release();
    try { await socket.close(); } catch { /* cleanup must not overwrite the delivery result */ }
  }
}

function createSmtpSession(socket: SmtpSocket, deadline: number) {
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
      const result = await withSmtpTimeout(reader.read(), deadline);
      if (result.done) throw new Error("smtp_connection_closed");
      buffer += decoder.decode(result.value, { stream: true });
      if (buffer.length > SMTP_MAX_RESPONSE_BYTES) throw new Error("smtp_response_too_large");
    }
  }

  async function readResponse(): Promise<{ code: number; lines: string[]; text: string }> {
    const lines: string[] = [];
    let code = 0;
    for (;;) {
      const line = await readLine();
      lines.push(line);
      if (lines.length > 100 || lines.reduce((size, item) => size + item.length, 0) > SMTP_MAX_RESPONSE_BYTES) {
        throw new Error("smtp_response_too_large");
      }
      const match = /^(\d{3})([\s-])/.exec(line);
      if (match) {
        code = Number(match[1]);
        if (match[2] === " ") break;
      }
    }
    return { code, lines, text: lines.join("\n") };
  }

  function assertResponse(response: { code: number; text: string }, expected: number[], label: string): void {
    if (!expected.includes(response.code)) throw new Error(`${label}_rejected:${response.code}`);
  }

  return {
    async expect(expected: number[], label: string) {
      const response = await readResponse();
      assertResponse(response, expected, label);
      return response;
    },
    async sendLineExpect(line: string, expected: number[], label: string) {
      await withSmtpTimeout(writer.write(TEXT.encode(`${line}\r\n`)), deadline);
      const response = await readResponse();
      assertResponse(response, expected, label);
      return response;
    },
    async writeData(message: string) {
      const normalized = message.replace(/\r?\n/g, "\r\n").replace(/^\./gm, "..");
      await withSmtpTimeout(writer.write(TEXT.encode(`${normalized}\r\n.\r\n`)), deadline);
    },
    release() {
      try { reader.releaseLock(); } catch { /* ignored */ }
      try { writer.releaseLock(); } catch { /* ignored */ }
    },
  };
}

async function withSmtpTimeout<T>(operation: Promise<T>, deadline: number): Promise<T> {
  const remaining = Math.min(SMTP_OPERATION_TIMEOUT_MS, deadline - Date.now());
  if (remaining <= 0) throw new Error("smtp_timeout");
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("smtp_timeout")), remaining); }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function smtpHeloName(fromEmail: string): string {
  return fromEmail.split("@")[1] || "globalinfectiousdisease.com";
}

function base64StdEncode(value: string): string {
  const bytes = TEXT.encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
