# Realtime publication monitoring

Run the read-only monitor after enabling the independent JSON channels:

```bash
venv/bin/python scripts/monitor_realtime_data.py --pretty
venv/bin/python scripts/monitor_realtime_data.py --format text
```

The command uses the configured database and `data_release.main_site_origin`.
It checks Surveillance at `/site-data/current.json` and Situation at
`/site-data/situation/current.json`. Use `--channel surveillance` or
`--channel situation` during staged deployment. Public overrides are
`--origin`, `--surveillance-manifest-url`, `--situation-manifest-url`, or
`REALTIME_SURVEILLANCE_MANIFEST_URL` / `REALTIME_SITUATION_MANIFEST_URL`.
Manifest URLs must use HTTP(S), without embedded credentials, queries or
fragments. No Cloudflare API token is needed for these public reads.

The database snapshot runs in a repeatable, read-only transaction and always
rolls back. It reports pending dirty markers and their oldest age, active
queue counts, running/retrying task details, recent failure count, last
successful publication and latest terminal result. Old `data_only` tasks
without `data_line` belong to Surveillance; Situation uses
`data_only=true,data_line="situation"`. Static site jobs do not count as JSON
publication successes.

Each public check downloads and validates the complete manifest, calculates
its SHA-256, and verifies one immutable object against its declared decoded
byte length and SHA-256. Gzip is decoded before validation. This samples
object availability; it does not download every country history. The smallest
object is chosen to keep a minute-by-minute check inexpensive. Manifest and
sample reads have bounded response sizes and share a hard 10-second deadline
per channel. The DB snapshot also has a 10-second deadline. Checks run
concurrently; redirects fail visibly rather than silently probing another
endpoint. API errors, connection strings, credentials and raw remote errors
are not printed.

## Health and exit codes

| Exit | Status | Meaning |
| --- | --- | --- |
| 0 | `ok` | No active delay or version/integrity fault |
| 1 | `warning` | Pending changes at least 120 seconds old, slow work, or a recovering failure |
| 2 | `critical` | Pending changes at least 600 seconds old, terminal failure, unavailable DB/HTTP, or inconsistent published bytes/version |
| 130 | interrupted | An operator stopped an observation loop |

`pending` retains all unresolved markers. `publishable_pending` uses the same
active-country/bootstrap visibility and parent expansion as `plan_changes`;
only that group's age enters the 120/600-second thresholds. Precise parent
markers stay exact, and coarse parents include eligible descendants.
`blocked_pending` exposes disabled, inactive or unknown scopes with a warning,
including counts, age and up to 100 scope codes. Monitoring never acknowledges
or hides them. Situation markers form a directly publishable global group.

The age of `generated_at` alone does **not** cause an alert: an idle channel
can legitimately retain the same version. The monitor compares the public
release ID and manifest hash with the latest completed JSON task. A mismatch
is a warning for the first 60 seconds after task completion, then critical.
A pointer already naming an active task is allowed while that task completes
its DB acknowledgement. Future generated timestamps more than five minutes
ahead are a warning.

Running work warns at 600 seconds and becomes critical at 1,800 seconds.
Scheduled retries remain visible; a retry more than 120/600 seconds overdue
warns/fails. Historical failures followed by success do not alert. A latest
failed/cancelled task is critical unless another attempt is active, in which
case it is a warning. Thresholds are CLI options; see `--help`. Hidden or
retired scopes remain in `blocked_pending` for operational review.

## Observe a release

```bash
venv/bin/python scripts/monitor_realtime_data.py \
  --duration-seconds 600 --interval-seconds 15 \
  --archive-dir data/cache/build-verification/realtime-rollout
```

`--samples 5 --interval-seconds 30` performs five checks. `--watch` continues
until interrupted. A bounded observation returns the worst status seen, so
an early rollout failure remains visible even after recovery. Every check is
printed immediately. Archives contain one UTC-dated `.ndjson` file and an
atomically replaced `latest.json`. They contain only the same sanitized
report. Keep release verification records with deployment evidence; rotate
long-lived daily logs according to the host's retention policy.

The one-minute systemd timer writes local journals and
`logs/realtime-data-monitor/`. It has no `OnFailure` notification, email or
Slack integration. The normal installer renders its templates:

```bash
scripts/install_systemd_services.sh --dry-run
sudo scripts/install_systemd_services.sh --user "$USER"
sudo systemctl enable --now globalid-realtime-data-monitor.timer
systemctl list-timers globalid-realtime-data-monitor.timer
journalctl -u globalid-realtime-data-monitor.service -n 20
cat logs/realtime-data-monitor/latest.json
```

Install without `--start` to avoid restarting the existing stack. The monitor
unit's failed exit status is intentional when a critical condition exists;
the timer still checks again on its next interval. Enable it after both
channels exist, or temporarily override `ExecStart` with the selected channel
during rollout. Deployment gate changes and queue triggers belong to the
release workflow, not to this monitor.

## Respond to failures

- `pending_changes_delayed`: inspect scheduler/worker health and JSON queue
  reservation/retry state; confirm the selected dirty scopes are publishable.
  Read `docs/SURVEILLANCE_PUBLISH_QUEUE.md` before resuming a stuck release.
- `pending_scopes_blocked`: review inactive, disabled or unknown scope markers.
  These are retained for review and are not treated as publishable backlog.
- `retry_overdue` or `retry_schedule_missing`: inspect the scheduler's persisted
  automatic-retry reservation; avoid enqueueing a competing release blindly.
- `http_status` / `http_unreachable` / `http_timeout`: inspect the public Worker
  route and current pointer; a 404 during first rollout means initialization or
  routing has not completed.
- `public_completed_version_mismatch`: compare the completed task receipt with
  the public pointer. Check for delayed acknowledgement or an obsolete
  candidate. Do not acknowledge all pending markers to clear an alert.
- `object_checksum_mismatch` / `invalid_manifest`: stop further publication and
  inspect the immutable candidate and Worker response encoding before retry.
- `database_unavailable` / `database_timeout`: public HTTP evidence is still
  reported. Check database/network health without exposing the connection URL.

The monitor never publishes, acknowledges changes, retries jobs or sends
messages. Those actions remain explicit release operations.
