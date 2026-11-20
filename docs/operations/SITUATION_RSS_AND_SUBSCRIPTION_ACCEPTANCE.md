# Situation RSS and email subscription acceptance

## Implemented scope

The read-only data Worker renders RSS directly from one published Situation
publication. Each request reads `current.json` once, then one immutable object
referenced by that manifest. It validates the object reference, uncompressed
byte count and SHA-256, including the publisher's optional gzip encoding.
The manifest is limited to 2 MiB and the input object to 8 MiB both before and
after decompression. Missing or corrupt input returns HTTP 503 with `no-store`
and `Retry-After: 300`; a valid published empty shard returns an empty RSS feed.

| Feed | URL |
| --- | --- |
| All event notices, English | `/site-data/situation/rss.xml?lang=en` |
| All event notices, Chinese | `/site-data/situation/rss.xml?lang=zh` |
| All event notices, French | `/site-data/situation/rss.xml?lang=fr` |
| One country/area | `/site-data/situation/rss.xml?lang=zh&country=AU` |
| One disease | `/site-data/situation/rss.xml?lang=zh&disease=D014` |

Choose at most one entity filter. Unknown parameters, duplicate parameters,
invalid identities and combined country/disease filters return HTTP 400.
Language defaults to English and accepts only `en`, `zh` or `fr`. Responses
support GET, HEAD, OPTIONS and conditional `If-None-Match`; write methods are
rejected. RSS responses use `no-store` so a saved RSS URL always checks the
latest Situation publication. The release is exposed as
`X-GIDS-Situation-Release` for verification. No user URL or secret is accepted.

Each feed contains at most 100 recent published event notices, excluding
`routine` monitoring records. The global `feed.json` retains the newest 200
unique alert identities as a compact input; entity feeds read their existing
country/disease shard before applying the limit. Items use source publication
dates, a stable `gids:situation:<alert_id>` GUID and the original report URL.
Dates are not first-detected timestamps. An older backfill can consequently
appear below the current 100-item window. These are recent feeds, not an
archive or a guarantee of delivery of every intervening event.

Published summaries and source labels are XML escaped. Missing host fields
from older generations mean “host not specified”; they never default to human.
The feed does not generate a risk score or infer deaths from report changes.
Full source text, review payloads and host-evidence quotations are absent from
the new global feed input.

The three subscription pages expose a global RSS link and separate country
and disease selectors. Selector choices come only from Situation facets with
published events and are refreshed with the page template. Existing saved
feed URLs continue reading current published JSON without a template build.

## Publication order and verification

This implementation has local checks, not production acceptance. It neither
enables public email campaigns nor sends test mail.

1. Apply any Situation schema migration required by the accompanying host
   metadata change before exporting a new database snapshot.
2. Publish a new Situation generation through the normal plan/task/receipt
   workflow. Verify that `current.json` includes `feed.json` and that its
   immutable object exists and passes the existing publication verification.
   The exporter explicitly adds `feed.json` to the manifest.
3. Publish the read-only data Worker code and the subscription page template
   through the established release workflow. Both the zone Worker and packaged
   Pages Worker route `/site-data/situation/*`; no new wildcard is required.
   Updating only the zone Worker does not update the Pages Functions bundle. A template release is required when the handler changes.
4. Verify all three global feeds and at least one country/disease feed against
   the expected `X-GIDS-Situation-Release`. Parse the result as XML and open a
   source-report link. Check animal and unspecified-host wording.
5. Check HEAD and `If-None-Match` returning 304 for the same representation.
   Publish a later Situation generation and verify the saved feed URL advances
   without rebuilding the HTML page; the content ETag changes when RSS changes.
6. Confirm that an intentionally empty registered shard returns 200 with zero
   items, while an absent object returns 503 without a fabricated empty feed.

Publishing the RSS handler before a generation containing `feed.json` makes
the global feed return 503 until that data publication completes. Country and
disease feeds can read older valid shards. A code-only site release alone
cannot create or refresh the global feed input.

## Email pilot exit checks

The existing subscription service already has confirmation, unsubscribe,
disease/area matching, an outbox, retries and delivery feedback. The
[2026-11-14 acceptance record](../acceptance/DATA_UPDATES_20261114.md) identifies
pilot status and explicitly says real content delivery was not verified in
that maintenance release. The following checks require a separately authorized
test run using operator-controlled inboxes; the coding checks do not establish
mailbox receipt.

- Confirm one new subscription and exercise a preference update using the
  signed management flow. Record which lists and filters became effective.
- Deliver one already-published official notice to each intended matching
  pilot inbox. Confirm a non-matching disease and area receive no message.
- Verify the recipient received the expected localized content, source link
  and unsubscribe link; correlate the outbox/provider identifiers with the
  delivery feedback. Provider acceptance alone is not inbox receipt.
- Replay the identical release event and confirm it creates no additional
  recipient delivery. Separately review whether multiple notices for one
  outbreak produce an acceptable notification frequency; technical deduplication
  does not establish that policy.
- Simulate a transient provider failure in a controlled environment, recover
  processing and verify bounded retries, final disposition and no duplicate
  accepted delivery.
- Unsubscribe and verify suppression for future content. Exercise hard-bounce
  and complaint suppression with the provider's supported test mechanism.
- Record the pilot configuration, publication identity, inbox evidence,
  delivery/feedback state and any failed scenario before opening public
  content delivery. Keep maintenance-release suppression intact until then.

Event-state subscriptions, notification priority, death-change triggers and
user-defined outbound Webhooks remain future increments. They require their
own semantics and acceptance evidence rather than being implied by RSS or the
existing internal callbacks.
