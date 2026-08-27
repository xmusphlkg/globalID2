const PROVIDER_MESSAGE_ID = /^[A-Za-z0-9._@+-]{6,240}$/;

/** Extract a provider queue/message identifier without accepting generic SMTP prose. */
export function parseSmtpProviderMessageId(value: string): string {
  const patterns = [
    /\bqueued\s+as\s+<?([A-Za-z0-9._@+-]{6,240})>?/i,
    /\bmessage[- ]?id\s*[:=]\s*<?([A-Za-z0-9._@+-]{6,240})>?/i,
    /\bid\s*=\s*<?([A-Za-z0-9._@+-]{6,240})>?/i,
  ];
  for (const pattern of patterns) {
    const candidate = pattern.exec(value)?.[1] || "";
    if (PROVIDER_MESSAGE_ID.test(candidate)) return candidate;
  }
  return "";
}
