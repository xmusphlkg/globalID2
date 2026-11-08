import { X509Certificate, verify } from "node:crypto";
import { isRecord } from "./input.ts";

export type SnsMessageType = "Notification" | "SubscriptionConfirmation" | "UnsubscribeConfirmation";
export interface SnsEnvelope {
  Type: SnsMessageType; MessageId: string; TopicArn: string; Message: string;
  Timestamp: string; SignatureVersion: "1" | "2"; Signature: string;
  SigningCertURL: string; Subject?: string; Token?: string; SubscribeURL?: string;
}

export class SnsError extends Error {
  status: number;
  constructor(message: string, status = 400) { super(message); this.status = status; }
}

export function topicEndpoint(topic: string): string {
  const m = /^arn:(aws|aws-us-gov|aws-cn):sns:([a-z0-9-]+):[0-9]{12}:[A-Za-z0-9_-]{1,256}(?:\.fifo)?$/.exec(topic);
  if (!m) throw new SnsError("invalid_sns_topic_configuration", 503);
  return `sns.${m[2]}.${m[1] === "aws-cn" ? "amazonaws.com.cn" : "amazonaws.com"}`;
}

function text(value: unknown, max: number, field: string): string {
  if (typeof value !== "string" || !value || value.length > max) throw new SnsError(`invalid_sns_${field}`);
  return value;
}

export function parseEnvelope(value: unknown, configuredTopic: string): SnsEnvelope {
  if (!isRecord(value)) throw new SnsError("invalid_sns_message");
  topicEndpoint(configuredTopic);
  const type = text(value.Type, 64, "type");
  if (!["Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"].includes(type)) throw new SnsError("unsupported_sns_type");
  const topic = text(value.TopicArn, 512, "topic");
  if (topic !== configuredTopic) throw new SnsError("sns_topic_not_allowed", 403);
  const version = text(value.SignatureVersion, 4, "signature_version");
  if (version !== "1" && version !== "2") throw new SnsError("invalid_sns_signature_version");
  const timestamp = text(value.Timestamp, 64, "timestamp");
  const millis = Date.parse(timestamp);
  if (!Number.isFinite(millis) || Math.abs(Date.now() - millis) > 30 * 24 * 60 * 60 * 1000) throw new SnsError("sns_timestamp_out_of_range");
  const result: SnsEnvelope = {
    Type: type as SnsMessageType, MessageId: text(value.MessageId, 200, "message_id"), TopicArn: topic,
    Message: text(value.Message, 245760, "message"), Timestamp: timestamp, SignatureVersion: version,
    Signature: text(value.Signature, 2048, "signature"), SigningCertURL: text(value.SigningCertURL, 2048, "certificate_url"),
  };
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@/-]*$/.test(result.MessageId)) throw new SnsError("invalid_sns_message_id");
  if (type === "Notification" && value.Subject !== undefined) {
    if (typeof value.Subject !== "string" || value.Subject.length > 4096) throw new SnsError("invalid_sns_subject");
    result.Subject = value.Subject;
  }
  if (type !== "Notification") {
    result.Token = text(value.Token, 4096, "token");
    result.SubscribeURL = text(value.SubscribeURL, 8192, "subscribe_url");
  }
  return result;
}

export function canonicalMessage(message: SnsEnvelope): string {
  const fields = message.Type === "Notification"
    ? ["Message", "MessageId", ...(message.Subject !== undefined ? ["Subject"] : []), "Timestamp", "TopicArn", "Type"]
    : ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"];
  return fields.map(key => `${key}\n${message[key as keyof SnsEnvelope]}\n`).join("");
}

export function certificateUrl(raw: string, topic: string): URL {
  let url: URL;
  try { url = new URL(raw); } catch { throw new SnsError("invalid_sns_certificate_url"); }
  if (url.protocol !== "https:" || url.hostname !== topicEndpoint(topic) || url.port || url.username || url.password ||
      url.search || url.hash || !/^\/SimpleNotificationService-[a-f0-9]{32,64}\.pem$/.test(url.pathname)) {
    throw new SnsError("invalid_sns_certificate_url");
  }
  return url;
}

export function confirmationUrl(message: SnsEnvelope): URL {
  let url: URL;
  try { url = new URL(message.SubscribeURL || ""); } catch { throw new SnsError("invalid_sns_confirmation_url"); }
  const keys = [...url.searchParams.keys()];
  if (url.protocol !== "https:" || url.hostname !== topicEndpoint(message.TopicArn) || url.port || url.username || url.password ||
      url.pathname !== "/" || url.hash || keys.length !== 3 || new Set(keys).size !== 3 ||
      url.searchParams.get("Action") !== "ConfirmSubscription" || url.searchParams.get("TopicArn") !== message.TopicArn ||
      url.searchParams.get("Token") !== message.Token) throw new SnsError("invalid_sns_confirmation_url");
  return url;
}

export async function readLimited(response: Response, maximum: number): Promise<Uint8Array> {
  if (!response.body) throw new SnsError("sns_empty_body");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = []; let size = 0;
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.byteLength;
    if (size > maximum) { await reader.cancel(); throw new SnsError("sns_body_too_large", 413); }
    chunks.push(part.value);
  }
  const result = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

export async function verifyEnvelope(message: SnsEnvelope, send: typeof fetch = fetch): Promise<void> {
  const url = certificateUrl(message.SigningCertURL, message.TopicArn);
  let pem: string;
  try {
    const response = await send(url.toString(), { method: "GET", redirect: "error", signal: AbortSignal.timeout(8000) });
    if (!response.ok || response.redirected) throw new Error();
    pem = new TextDecoder().decode(await readLimited(response, 32768));
  } catch { throw new SnsError("sns_certificate_unavailable", 503); }
  let certificate: X509Certificate;
  try { certificate = new X509Certificate(pem); } catch { throw new SnsError("invalid_sns_certificate"); }
  const now = Date.now(), validFrom = Date.parse(certificate.validFrom), validTo = Date.parse(certificate.validTo);
  if (!Number.isFinite(validFrom) || !Number.isFinite(validTo) || now < validFrom || now > validTo ||
      certificate.publicKey.asymmetricKeyType !== "rsa" || (certificate.publicKey.asymmetricKeyDetails?.modulusLength || 0) < 2048) {
    throw new SnsError("invalid_sns_certificate");
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(message.Signature)) throw new SnsError("invalid_sns_signature");
  let signature: Uint8Array;
  try { signature = Uint8Array.from(atob(message.Signature), c => c.charCodeAt(0)); }
  catch { throw new SnsError("invalid_sns_signature"); }
  const valid = verify(message.SignatureVersion === "2" ? "RSA-SHA256" : "RSA-SHA1",
    new TextEncoder().encode(canonicalMessage(message)), certificate.publicKey, signature);
  if (!valid) throw new SnsError("invalid_sns_signature", 403);
}
