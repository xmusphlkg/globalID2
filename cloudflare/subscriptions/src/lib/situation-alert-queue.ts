import { parseSituationAlertJob, type SituationAlertJob } from "./situation-alert.ts";

import type { Env } from "../types.ts";

import type { Dependencies } from "./situation-alert-types.ts";
import { errorMessage, structuredLog } from "./situation-alert-observability.ts";
import { processSituationAlertOutbox, retryDelaySeconds } from "./situation-alert-delivery.ts";
export async function consumeSituationAlertBatch(
  batch: MessageBatch<SituationAlertJob>,
  env: Env,
  deps: Dependencies,
): Promise<void> {
  for (const message of batch.messages) {
    const job = parseSituationAlertJob(message.body);
    if (!job) {
      structuredLog("error", "situation_alert_queue_invalid_message", {
        queue_message_id: message.id,
      });
      message.ack();
      continue;
    }
    try {
      const result = await processSituationAlertOutbox(env, deps, { eventId: job.event_id });
      if (result.reason === "smtp_not_configured") {
        message.retry({ delaySeconds: 300 });
      } else {
        message.ack();
      }
    } catch (error) {
      structuredLog("error", "situation_alert_queue_processing_failed", {
        event_id: job.event_id,
        queue_message_id: message.id,
        error: errorMessage(error),
      });
      message.retry({ delaySeconds: retryDelaySeconds(message.attempts) });
    }
  }
}
