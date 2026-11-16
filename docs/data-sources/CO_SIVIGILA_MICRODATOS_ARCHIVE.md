# INS Sivigila 4.0 原始微观数据保存

独立本地目录：`data/raw/co/sivigila-microdatos/`。

## 全量归档完成

2026-11-16 17:40（Asia/Shanghai）完成下载及独立核验：**2007–2025 年共
1,222 个 XLSX 原件，9,330,171,681 字节**。完整官方目录中的所有条目均有
本地原件，逐一核对年份、事件代码、来源修改时间、字节数和 SHA-256，
无遗漏、无失败、无校验错误。`verification.json` 的状态为
`complete_verified`，归档服务正常退出。

`inventory.csv` 提供完整文件索引，`SHA256SUMS` 包含全部原件以及本次官方
查询页和目录快照的校验值。目录中的原始逐例数据保持本地保存。

2026-11-16 通过 `globalid-co-ins-proxy.service` 实际访问官方
[微观数据查询页](https://portalsivigila.ins.gov.co/Paginas/Buscador.aspx)，
并读取该页 JavaScript 使用的 SharePoint `Microdatos` 公共目录。
完整目录返回 **1,222 个 XLSX 文件，覆盖 2007–2025 年，共
9,330,171,681 字节**。其中 2023 年 69 个、2024 年 71 个、2025 年
72 个。目录中的 `NombreEvento`、`CodigoEvento`、年份和 `FileRef`
用于记录来源；保留原始响应，不依赖可能与事件名称不一致的旧 `Evento` 字段。

目录记录代表文件发布情况，不代表每个文件都包含病例、字段一致或已通过
流行病学分析验收。2026 年指南第 136–137 页的描述来自用户提供的线索，
此采集任务没有据此推断文件内容或指南真实性。

## 保存结构

```text
data/raw/co/sivigila-microdatos/
  README.md
  source-notes.md
  manifest.json                     # 当前任务状态、下载记录及错误
  verification.json                 # 完整官方目录与本地文件的独立核对结果
  inventory.csv                     # 全量核验通过后生成的年份、事件、文件路径索引
  SHA256SUMS                        # 全量核验通过后生成的便携校验清单
  snapshots/<UTC时间>/
    buscador.html                   # 官方查询页原始响应
    catalogue-0001.json             # 官方目录原始响应；支持后续分页
    manifest.json                   # 页面和目录的 URL、时间、大小、SHA-256
    download-manifest.json          # 每次正常结束的任务结果，失败也会记录
  files/<年份>/<事件代码>/<SHA-256>.xlsx
```

文件先写临时路径，核对目录大小、HTTP 大小及 XLSX ZIP 成员 CRC 后再正式保存。
每个文件记录官方 URL、目录修改时间、抓取时间、字节数与 SHA-256。
中断后再次运行会校验已有文件并跳过；损坏文件重新下载。官方内容更新时保留
旧版本。历史原件不因目录后续删除某一条目而删除。

运行中可查看：

```bash
systemctl status globalid-co-microdatos-archive.service
journalctl -u globalid-co-microdatos-archive.service -f
```

服务配置在 `configs/systemd/globalid-co-microdatos-archive.service`，已明确配置：

```ini
[Unit]
Wants=globalid-co-ins-proxy.service
After=globalid-co-ins-proxy.service
```

服务使用 `CO_INS_HTTP_PROXY`，验证官方 HTTPS 证书，不在代理失败时切换直连。
服务在全部下载完成后运行独立核验，逐项对照目录记录、文件大小和 SHA-256。
它是手动触发的完整归档任务，不设置周期定时器。完成后复查 `manifest.json`
中的 `status`、`completed_count`、`selected_count` 与 `errors`。
再次执行可恢复或更新：

```bash
sudo systemctl start --no-block globalid-co-microdatos-archive.service
```

也可单独保存指定年份：

```bash
CO_INS_HTTP_PROXY=http://169.254.231.2:18088 \
venv/bin/python scripts/imports/collect_co_microdatos.py --years 2023 2024 2025
```

脚本默认四个并发下载，已安装的全量归档服务使用八个并发。
按年份倒序、同年文件大小升序开始。
失败文件重试三次，最终错误逐条写入清单，任务以非零状态退出。
独占锁避免两个采集进程同时修改归档。

本目录是本机原始文件存档，受项目 `/data/` 忽略规则管理，不会进入代码 Git
历史；保存目录并不等于异机备份。此脚本只保存官方原件，不导入数据库，
不将逐例数据发布到公开下载仓库。

离线复查归档并重新生成完整文件索引：

```bash
venv/bin/python scripts/verify_co_microdatos_archive.py
# 或在归档目录内：sha256sum -c SHA256SUMS
```

实现验证：`venv/bin/python -m pytest tests/unit/test_co_microdatos_archive.py tests/unit/test_co_microdatos_archive_verifier.py tests/unit/test_co_ins_transport.py -q`，20 项通过。
