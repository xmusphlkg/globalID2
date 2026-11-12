/** Render deterministic, synthetic examples without making network requests. */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildConfirmationEmail, buildManagementEmail, buildNotificationEmail } from "../src/lib/email.ts";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const output = process.argv[2] ? resolve(process.argv[2]) : resolve(root, "exports/subscription-email-previews");
const sampleUrl = "https://example.test/sample";
const manageUrl = "https://example.test/manage";
const unsubscribeUrl = "https://example.test/unsubscribe";
const examples = (["en", "zh"] as const).flatMap((locale) => {
  const zh = locale === "zh";
  return [
    { name: `confirmation-${locale}`, label: zh ? "订阅确认" : "Confirmation", content: buildConfirmationEmail(locale, [
      { id: "sample-report", listCode: "reports", frequency: "weekly", confirmUrl: sampleUrl },
      { id: "sample-alert", listCode: "alerts", frequency: "instant", confirmUrl: sampleUrl },
    ]) },
    { name: `management-${locale}`, label: zh ? "订阅管理" : "Management", content: buildManagementEmail(locale, [
      { name: zh ? "报告更新" : "Report updates", url: manageUrl },
      { name: zh ? "重点提醒" : "Priority alerts", url: manageUrl },
    ]) },
    { name: `report-${locale}`, label: zh ? "监测报告" : "Surveillance reports", content: buildNotificationEmail(locale, {
      subject: zh ? "GIDS 监测报告周报 · 样例" : "GIDS surveillance reports · example",
      markdown: zh ? "## 本周报告\n\n- [查看监测报告样例](https://example.test/sample)\n\n此内容仅用于模板预览。" : "## This week's reports\n\n- [View a sample surveillance report](https://example.test/sample)\n\nThis content is for template preview only.",
    }, unsubscribeUrl, manageUrl, "report") },
    { name: `research-${locale}`, label: zh ? "研究周报" : "Research brief", content: buildNotificationEmail(locale, {
      subject: zh ? "Research Radar 每周研究简报 · 样例" : "Research Radar weekly brief · example",
      markdown: zh ? "## 研究发现样例\n\n此处展示符合订阅偏好的研究摘要。\n\n[查看文献样例](https://example.test/sample)" : "## Sample research finding\n\nResearch matching the reader's preferences appears here.\n\n[View a sample article](https://example.test/sample)",
    }, unsubscribeUrl, manageUrl, "research") },
    { name: `alert-${locale}`, label: zh ? "官方通报" : "Official notice", content: buildNotificationEmail(locale, {
      subject: zh ? "GIDS 官方通报提醒 · 样例" : "GIDS official notice alert · example",
      markdown: zh ? "## 官方通报样例\n\n此内容仅用于演示，不代表真实疫情。\n\n[查看来源样例](https://example.test/sample)" : "## Sample official notice\n\nThis example does not describe a real outbreak.\n\n[View a sample source](https://example.test/sample)",
    }, unsubscribeUrl, manageUrl, "alert") },
  ].map(item => ({ ...item, locale }));
});

await mkdir(output, { recursive: true });
for (const item of examples) {
  await writeFile(resolve(output, `${item.name}.html`), item.content.html, "utf8");
  await writeFile(resolve(output, `${item.name}.txt`), item.content.text, "utf8");
}
const links = examples.map(item => `<li>${item.label} (${item.locale}): <a href="${item.name}.html">HTML</a> · <a href="${item.name}.txt">Plain text</a></li>`).join("\n");
await writeFile(resolve(output, "index.html"), `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GIDS email previews</title></head><body style="font:16px/1.6 Arial,sans-serif;max-width:720px;margin:40px auto;padding:0 20px"><h1>GIDS email previews / 邮件样例</h1><p>Synthetic examples only; no email is sent. / 所有内容仅供预览，不发送邮件。</p><ul>${links}</ul></body></html>`, "utf8");
process.stdout.write(`Email previews written to ${output}\n`);
