import type { SubscriptionOptions, SubscriptionStats } from "@/lib/hooks/useSubscriptions";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { formatNumber } from "@/lib/utils";

type Props = {
  options?: SubscriptionOptions;
  stats?: SubscriptionStats;
  isZh: boolean;
};

/** Keep provider acceptance separate from downstream delivery feedback. */
export function DeliveryStatus({ options, stats, isZh }: Props) {
  const campaigns = stats?.campaign_deliveries_last_7_days;
  const feedback = stats?.provider_feedback_last_7_days;
  const feedbackCount = feedback ? Object.values(feedback).reduce((sum, value) => sum + value, 0) : 0;
  const count = (value: number | undefined, available: boolean) => available ? formatNumber(value ?? 0) : "—";

  return (
    <section className="app-panel p-4" aria-label={isZh ? "内容投递状态" : "Content delivery status"}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {isZh ? "内容投递状态" : "Content delivery status"}
          </h2>
          <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {isZh ? "投递与回执为最近 7 天；管理队列为当前状态。服务商接收不代表邮件已送达。" : "Deliveries and feedback cover the last 7 days; the management queue is current. Provider acceptance does not confirm delivery."}
          </p>
        </div>
        <StatusBadge tone={options?.pilot_mode === true ? "warning" : options?.pilot_mode === false ? "success" : "neutral"}>
          {options?.pilot_mode === true ? (isZh ? "管理员试运行" : "Administrator pilot")
            : options?.pilot_mode === false ? (isZh ? "全量投递" : "Full delivery")
              : (isZh ? "投递模式未知" : "Delivery mode unknown")}
        </StatusBadge>
      </div>
      {options?.pilot_mode === true ? (
        <p className="mt-3 text-sm text-amber-800 dark:text-amber-200">
          {isZh ? "内容邮件仅发送给试运行名单；公众仍可确认和管理订阅。" : "Content emails are limited to pilot recipients; readers can still confirm and manage subscriptions."}
        </p>
      ) : null}
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          { label: isZh ? "群发已接收" : "Campaigns accepted", value: count((campaigns?.sent ?? 0) + (campaigns?.delivered ?? 0) + (campaigns?.deferred ?? 0), Boolean(campaigns)) },
          { label: isZh ? "群发结果待核实" : "Campaigns awaiting reconciliation", value: count(campaigns?.unknown, Boolean(campaigns)) },
          { label: isZh ? "服务商送达回执" : "Provider delivery events", value: count(feedback?.delivered, Boolean(feedback)) },
          { label: isZh ? "退信 / 投诉回执" : "Bounce / complaint events", value: feedback ? `${formatNumber(feedback.bounced ?? 0)} / ${formatNumber(feedback.complained ?? 0)}` : "—" },
          { label: isZh ? "管理链接队列 / 待核实 / 失败" : "Management queue / unknown / failed", value: stats?.management_outbox
            ? `${formatNumber((stats.management_outbox.queued ?? 0) + (stats.management_outbox.retry ?? 0) + (stats.management_outbox.sending ?? 0))} / ${formatNumber(stats.management_outbox.unknown ?? 0)} / ${formatNumber(stats.management_outbox.failed ?? 0)}` : "—" },
        ].map((item) => (
          <div key={item.label} className="rounded-tremor-default border border-tremor-border p-3 dark:border-dark-tremor-border">
            <dt className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{item.label}</dt>
            <dd className="mt-1 text-lg font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{item.value}</dd>
          </div>
        ))}
      </dl>
      {feedback && feedbackCount === 0 ? (
        <p className="mt-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {isZh ? "此期间没有回执；请检查服务商事件接入和测试邮箱。" : "No feedback events in this period; check the provider integration and test inbox."}
        </p>
      ) : null}
      {options?.lists.length ? (
        <div className="mt-4 flex flex-wrap gap-2" aria-label={isZh ? "订阅内容通道" : "Subscription content streams"}>
          {options.lists.map((list) => (
            <StatusBadge key={list.code} tone={list.available === true ? "success" : list.available === false ? "warning" : "neutral"}>
              {isZh ? list.name_zh : list.name_en}: {list.available === true ? (isZh ? "开放" : "Available")
                : list.available === false ? (isZh ? "未开放" : "Unavailable") : (isZh ? "未知" : "Unknown")}
            </StatusBadge>
          ))}
        </div>
      ) : null}
    </section>
  );
}
