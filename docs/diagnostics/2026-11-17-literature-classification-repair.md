# 2026-11-17 文献分类准确性修复

本次完成分类代码修复、回归测试和现有数据库只读试算。未执行生产库重分类，
未重新生成或发布文献数据，不能据此声称线上历史记录已经修复。

## 已证实的问题

本地 `astro-site/src/data/research/articles/` 有 9,806 篇详情记录，其中 1,218 篇
标题包含 COVID 或 SARS-CoV-2，同时带有 D003（SARS）标签。这是需要复核的
候选集合，不代表 1,218 篇全部错误：比较 SARS 与 COVID-19 的研究可以同时涉及两者。

具体证据：

- DOI `10.1016/j.cmi.2022.01.033`、PMID `35219807` 是挪威 COVID-19 住院队列。
  [Europe PMC 原始记录](https://europepmc.org/article/MED/35219807) 的摘要、MeSH 和
  关键词含 SARS-CoV-2。数据库记录明确把这些证据映射成 `SARS-CoV-2=>SARS`，
  错误关联 D003，置信度达到 0.98。修复后仅保留 D004（COVID-19），挪威地区保留。
- DOI `10.1016/j.vaccine.2019.06.035` 的标题为
  “Recombinant subunit vaccines protect guinea pigs from lethal Ebola virus challenge”。
  分类器把动物名 guinea pigs 中的 Guinea 当作国家。修复后移除 GN，其他独立地区证据保留。
- DOI `10.1371/journal.pntd.0007757` 的历史双语摘要解释字段仍提到 Norway，
  但研究中的 Norway rat 是褐家鼠，不能据此推断研究发生在挪威。
  重分类只修复分类与关联，**不会改写已审核摘要**；这类历史叙述须另行复核。
- DOI `10.1126/science.1085953` 是 2003 年原始 SARS 基因组研究；OpenAlex 的
  “SARS-CoV-2 and COVID-19 Research”粗粒度主题会反向错误增加 COVID-19 标签。
  现已排除这种只有宽泛主题、没有独立原文或具体语义证据的交叉关联。

## 修复行为

分类版本由 5 升为 6。匹配 D003 时屏蔽 SARS-CoV-2 及其完整病原体名称，
覆盖标题、摘要、Europe PMC 与 OpenAlex 语义元数据；仅屏蔽完整名称所在位置。
独立出现的 SARS、2003 年 SARS 研究或 SARS 与 SARS-CoV-2 比较仍保留 SARS 标签。
当原文明确涉及其中一种疾病、另一种仅由 OpenAlex 大类主题支持时，后者不建立
疾病关联；原文、MeSH 或具体关键词的独立证据仍有效。
地区匹配同理排除 Norway rat、guinea pig 内嵌的国家名，独立国家名保持有效。

重分类维护工具新增 `diseases_removed`、`countries_removed`、`topics_removed`
计数，以便在试算时检查错误关联的移除量。发布状态、编辑锁、摘要和来源证据仍由
原来的维护契约保留。

## 已执行验证

```bash
PYTHONPATH=. venv/bin/python -m pytest -q \
  tests/unit/test_literature_classification.py \
  tests/unit/test_literature_reclassification.py \
  tests/unit/test_literature_source_enrichment.py \
  tests/unit/test_literature_release_validation.py \
  tests/unit/test_literature_health.py
```

结果：**101 passed**。回归覆盖多种 SARS-CoV-2 写法、完整病原体名称、原始 SARS 独立证据、动物名与
真实国家名共存，以及重分类试算不写库、执行时保留编辑状态的事务行为。

现有数据库的有界试算：

```bash
PYTHONPATH=. venv/bin/python scripts/reclassify_literature.py --dry-run \
  --doi 10.1016/j.cmi.2022.01.033 \
  --doi 10.1371/journal.pntd.0007757 \
  --doi 10.1016/j.vaccine.2019.06.035 \
  --doi 10.1126/science.1085953
```

结果：检查 4 条、版本或分类变化 4 条；疾病关联移除 1 条、国家关联移除 1 条；
新增国家、主题关联各 1 条，新增疾病 0 条。新增关联来自以当前完整词表重算历史记录，
不能把 `changed=4` 理解为四篇原文均有错误。最后一个 DOI 是原始 SARS
基因组研究对照。试算未进行任何来源请求或数据库写入。

## 尚待执行的数据修复与发布

发布校验要求 `classification_version >= 6`；既有 v5 发布记录会被拒绝。
这是防止旧错误关联继续进入新发布的版本门禁，不能通过只改版本号绕过。

先用上述四个 DOI 的试算检查变更，再去掉 `--dry-run` 执行相同范围的修复。
需要覆盖历史记录时，使用现有有界游标流程：

```bash
PYTHONPATH=. venv/bin/python scripts/reclassify_literature.py \
  --dry-run --limit 200 --after-id 0
```

每批复核后去掉 `--dry-run` 执行相同游标范围，把返回的 `next_after_id` 作为下一批
的 `--after-id`，直到 `examined=0`。记录每批新增和移除计数。该流程遍历全部存量
记录，不能仅修复四个样本就宣布全库完成。作用域内维护会跳过全库自动信号关联
重算；需要在批次完成后通过现有自动信号关联维护流程进行核对。

随后复核曾引用错误分类的摘要，重新冻结文献快照，执行发布校验并经现有
`plan → task → receipt` 流程发布文献 JSON 与 Papers 文章投影，最后核查公开页面。
仅发布模板不会修复已存储的分类或摘要。
