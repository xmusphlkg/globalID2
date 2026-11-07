const PROVIDER_MESSAGE_ID = /^[A-Za-z0-9._@+-]{6,240}$/;

/** Extract a provider queue/message identifier without accepting generic SMTP prose. */
export function parseSmtpProviderMessageId(value: string): string {
  const patterns = [
    // SES's DATA response is "250 Ok <SES message ID>", without an id= label.
    /^250[ -]Ok\s+<?([0-9a-f]{16}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[0-9]{6})>?\s*$/im,
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
