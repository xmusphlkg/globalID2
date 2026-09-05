export type ChangeKind = 'new' | 'improved' | 'fixed';

export type ChangelogSection = {
  kind: ChangeKind;
  labelEn: string;
  labelZh: string;
  items: Array<{
    en: string;
    zh: string;
  }>;
};

export type ChangelogRelease = {
  version: string;
  date: string;
  titleEn: string;
  titleZh: string;
  summaryEn: string;
  summaryZh: string;
  sections: ChangelogSection[];
};

export const changelogReleases: ChangelogRelease[] = [
  {
    version: '0.9.5',
    date: '2026-09-05',
    titleEn: 'Research Radar publication-boundary repair',
    titleZh: '研究雷达公开边界修复',
    summaryEn:
      'GIDS now revalidates autopilot-published Research Radar records against hard public-boundary rules, recovers non-public-domain leaks, and keeps model summaries flowing when a field is returned as prose instead of a structured object.',
    summaryZh:
      'GIDS 现在会用硬性公开边界规则重新校验自动发布的研究雷达记录，回收非公开研究域泄漏，并在模型把字段返回为普通文本而非结构对象时继续保留摘要结果。',
    sections: [
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Autopilot-managed articles with animal-only, basic-research, or plant-only classification evidence can no longer remain in the public Research Radar catalogue.',
            zh: '带有动物实验、基础研究或植物研究分类证据的自动发布文章不再能停留在公开研究雷达目录中。',
          },
          {
            en: 'Automatically published summaries now return to review or archive when their parent article is removed from the public boundary.',
            zh: '当父文章离开公开边界时，自动发布的摘要现在会回到待审或归档状态。',
          },
          {
            en: 'Summary enrichment now accepts scalar/list model fields as conservative evidence objects, reducing avoidable failures from otherwise usable model responses.',
            zh: '摘要补全现在会把模型返回的文本或列表字段保守转换为证据对象，减少可用回答因结构轻微偏差而失败。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'The repaired Research Radar export passes public-release validation again, with 964 public articles and 334 weekly briefs after recovery.',
            zh: '修复后的研究雷达导出已重新通过公开发布校验，恢复后包含 964 篇公开文章和 334 份周报。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.4',
    date: '2026-09-04',
    titleEn: 'Autonomous knowledge repair, PubMed discovery, and richer curve comparison',
    titleZh: '知识库自动修复、PubMed 发现与更丰富的曲线比较',
    summaryEn:
      'GIDS now keeps source-first knowledge repairs moving through the control plane, adds PubMed as a bounded Research Radar source, exposes Model Center runtime health, and gives public disease curves safer comparison tools.',
    summaryZh:
      'GIDS 现在通过控制面维持来源优先的知识库修复队列，新增 PubMed 作为受限的研究雷达来源，展示模型中心运行健康，并为公开疾病曲线提供更安全的比较工具。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added a disease-knowledge autopilot that prioritizes real evidence gaps, refreshes targeted sources first, and queues model repair only after the evidence packet is ready.',
            zh: '新增疾病知识库自动驾驶流程，优先处理真实证据缺口，先刷新定向来源，并且只在证据包就绪后调度模型修复。',
          },
          {
            en: 'Added PubMed E-utilities for bounded biomedical discovery and Crossref fallback in Research Radar.',
            zh: '新增 PubMed E-utilities，用于研究雷达中的受限生物医学发现和 Crossref 临时不可用时的回退。',
          },
          {
            en: 'Added public curve controls for seasonal anomaly comparison, complete-calendar-year totals, quick date ranges, selected-only filtering, and sortable entity lists.',
            zh: '新增公开曲线控件，支持同期异常比较、完整自然年总量、快捷时间范围、仅看已选对象和实体排序。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Model governance now uses the shared Model Center path, including runtime health, route admission, failover, and provider/model catalogue discovery.',
            zh: '模型治理现在使用共享模型中心路径，包含运行健康、路由准入、失败切换以及 provider/model 目录发现。',
          },
          {
            en: 'Knowledge profiles now show an automating state while required evidence is still being enriched instead of sending unsupported content to manual review.',
            zh: '当必需证据仍在补全时，知识画像现在显示自动处理中状态，而不是把缺乏支撑的内容直接推给人工审核。',
          },
          {
            en: 'Research Radar summary enrichment now includes non-open-access records when a usable abstract is available and spreads concurrent requests across active Model Center route shards.',
            zh: '研究雷达摘要补全现在会纳入非开放获取但已有可用摘要的记录，并将并发请求分散到活跃的模型中心路由分片。',
          },
          {
            en: 'The Research evidence graph now loads relationships from a dedicated static JSON endpoint, keeping localized HTML pages inside performance budgets as the catalogue grows.',
            zh: '研究证据图谱现在从专用静态 JSON 端点加载关系数据，随着目录增长也能让中英文 HTML 页面保持在性能预算内。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Prevented aggregate or non-public catalogue rows, stale source-gap metadata, adapter timeouts, and repeated overlay notes from destabilizing knowledge publication.',
            zh: '防止聚合或非公开目录项、过期来源缺口元数据、适配器超时和重复覆盖说明影响知识发布稳定性。',
          },
          {
            en: 'Improved task-worker restart behavior with adaptive AI concurrency, per-disease knowledge serialization, model-recovery wakeups, and safer singleton lease handling.',
            zh: '改进任务 worker 重启行为，加入自适应 AI 并发、按疾病串行化知识任务、模型恢复唤醒和更安全的单例租约处理。',
          },
          {
            en: 'Transient model connectivity failures no longer consume Research Radar summary quality-attempt budgets, keeping affected abstracts retryable.',
            zh: '临时模型连接失败不再消耗研究雷达摘要质量尝试次数，受影响摘要会继续保持可重试。',
          },
          {
            en: 'Research Radar public exports and Astro builds now share a release lock, preventing continuous summary catch-up from changing source data midway through a site build.',
            zh: '研究雷达公开导出与 Astro 构建现在共享发布锁，避免连续摘要补全在站点构建中途改写源数据。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.3',
    date: '2026-09-02',
    titleEn: 'Evidence-backed knowledge repair and source-first automation',
    titleZh: '证据驱动的知识库修复与来源优先自动化',
    summaryEn:
      'GIDS now repairs only evidence-backed knowledge gaps, refreshes targeted sources before new model work, and preserves valid content through a controlled model-center recovery loop.',
    summaryZh:
      'GIDS 现在只修复具备明确证据支持的知识库缺口，在新的模型任务前先刷新定向来源，并通过受控的模型中心恢复闭环保护已有有效内容。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added a bounded quality-repair prompt that returns failed fields, source-grounded evidence fragments, and the prior JSON to the model for one constrained repair pass.',
            zh: '新增受限质量修复提示词，将失败字段、来源支撑的证据片段和上一轮 JSON 一并返回模型，进行一次受控修复。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'New knowledge repairs now refresh and assess targeted sources before scheduling model-center generation, reducing calls that cannot meet publication gates.',
            zh: '新的知识库修复现在会先刷新并评估定向来源，再调度模型中心生成，减少无法通过发布门槛的调用。',
          },
          {
            en: 'Quality repair locks previously valid fields so the model can change only the rejected evidence-backed sections.',
            zh: '质量修复会锁定此前有效的字段，使模型只能修改被拒绝且有证据支持的章节。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Unsupported fields now remain in review instead of triggering repeated model calls from incomplete evidence.',
            zh: '缺乏支撑证据的字段现在会保持待审，不再基于不完整证据反复调用模型。',
          },
          {
            en: 'Worker restart now uses a clean drain-and-lease handoff before the replacement worker resumes recoverable queued work.',
            zh: 'worker 重启现在采用清晰的排空与租约交接流程，替换进程再继续处理可恢复队列任务。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.2',
    date: '2026-09-01',
    titleEn: 'AI content governance and knowledge repair automation',
    titleZh: 'AI 内容治理与知识库自动修复',
    summaryEn:
      'GIDS now routes knowledge gaps, Research Radar review queues, source review, and learning suggestions through auditable AI governance loops instead of defaulting to manual intervention.',
    summaryZh:
      'GIDS 现在将知识库缺口、研究雷达审核队列、来源审核和学习建议接入可审计的 AI 治理闭环，不再默认依赖人工干预。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added AI content governance with model-center JSON review prompts, confidence gates, audit metadata, and fallback-to-hold behavior.',
            zh: '新增 AI 内容治理服务，包含模型中心 JSON 审核提示词、置信度门槛、审计元数据以及失败时保持待定的保护行为。',
          },
          {
            en: 'Added control-plane endpoints for knowledge repair runs, failed knowledge retries, Research Radar article and summary review, knowledge-source review, and learning-suggestion mapping.',
            zh: '新增控制面接口，用于知识库修复运行、失败知识任务重试、研究雷达文章和摘要审核、知识来源审核以及学习建议映射。',
          },
          {
            en: 'Added AI-assisted Research Radar article review so high-confidence review-band articles can be published or excluded before summary gates run.',
            zh: '新增 AI 辅助的研究雷达文章审核，使高置信审核带文章可在摘要发布门槛前自动发布或排除。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Knowledge repair now supports language-targeted retry, allowing single-language failures to regenerate only the affected English or Chinese brief.',
            zh: '知识库修复现在支持按语言定向重试，单语失败时只重新生成受影响的英文或中文摘要。',
          },
          {
            en: 'Learning suggestions can now resolve obvious placeholders and high-confidence standard disease mappings automatically.',
            zh: '学习建议现在可以自动处理明显占位项和高置信标准病种映射。',
          },
          {
            en: 'Long-running workers now emit less idle log noise and release Python and libc heap memory after task completion.',
            zh: '长运行 worker 现在减少空闲日志噪音，并在任务完成后释放 Python 与 libc 堆内存。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Recoverable model-center and partial-language knowledge repair failures can be requeued automatically up to their retry limit.',
            zh: '可恢复的模型中心错误和单语知识库发布门槛失败现在可在重试上限内自动回队列。',
          },
          {
            en: 'Release retry classification now distinguishes transient OpenSSL EOF preflight diagnostics from permanent Cloudflare production-branch configuration blockers.',
            zh: '发布重试分类现在可区分临时 OpenSSL EOF 预检诊断与永久性的 Cloudflare 生产分支配置阻断。',
          },
          {
            en: 'Dashboard standalone startup now prunes older retained releases after switching to the active build.',
            zh: 'Dashboard standalone 启动在切换到活动构建后会清理超出保留数量的旧版本目录。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.1',
    date: '2026-08-30',
    titleEn: 'Localized public site and jurisdiction-aware operations',
    titleZh: '公开站点本地化与辖区化运营',
    summaryEn:
      'GIDS now has first-class Chinese public routes, a refreshed static-site experience, jurisdiction-aware Control Center selection, and stronger source-series-first data handling across public APIs and exports.',
    summaryZh:
      'GIDS 现已提供完整中文公开路由、焕新的静态站点体验、支持辖区选择的控制中心，以及贯穿公开 API 和导出的来源序列优先数据处理。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added Chinese routes for home, countries, diseases, reports, Situation Room, Research Radar, downloads, legal pages, and Changelog with localized canonical navigation.',
            zh: '新增首页、国家、病种、报告、态势室、研究雷达、下载、法律页面和更新日志的中文路由，并提供本地化 canonical 导航。',
          },
          {
            en: 'Added a China provincial monthly-source framework with province source configuration, 31 adapter registrations, documentation, tests, and Control Center/public metadata wiring.',
            zh: '新增中国省级月度来源框架，包括省级来源配置、31 个适配器注册、文档、测试，以及控制中心和公开端元数据接入。',
          },
          {
            en: 'Added jurisdiction-aware country and region classifications for coverage, source labels, API metadata, and the Control Center jurisdiction picker.',
            zh: '新增面向辖区的国家与区域分类，用于覆盖地图、来源标签、API 元数据和控制中心辖区选择器。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Refreshed the public home, country, download, about, copyright, terms, and changelog experiences and synchronized offline country flags for deterministic static builds.',
            zh: '焕新公开首页、国家、下载、关于、版权、条款和更新日志体验，并同步离线国家旗帜以保证静态构建可复现。',
          },
          {
            en: 'Promoted source-series-first handling through APIs, site generation, charts, downloads, and coverage summaries so canonical projections and source-only series stay distinct.',
            zh: '将来源序列优先处理贯穿 API、站点生成、图表、下载和覆盖摘要，使标准投影与仅来源序列保持清晰区分。',
          },
          {
            en: 'Strengthened reusable ECDC, Singapore CDA, and Canada CNDSS ingestion paths with source-contract review, attribution, scheduled checks, and fail-closed refresh behavior.',
            zh: '强化可复用的 ECDC、新加坡 CDA 和加拿大 CNDSS 摄取路径，补充来源契约审核、归属说明、计划检查和失效关闭刷新行为。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened tests for disease-series policy, ontology validation, country coverage, classifications, generated API contracts, and source-series-first site exports.',
            zh: '强化病种序列策略、 ontology 校验、国家覆盖、分类、生成式 API 契约和来源序列优先站点导出的测试。',
          },
          {
            en: 'Improved chart and map behavior for sparse source data, hidden projections, mixed cadence series, and localized country/disease pages.',
            zh: '改进稀疏来源数据、隐藏投影、混合频率序列以及本地化国家/病种页面中的图表和地图表现。',
          },
          {
            en: 'Kept local Playwright run output and generated flag assets out of release commits while preserving stable static-site builds.',
            zh: '将本地 Playwright 运行输出和生成的旗帜资源排除在发布提交之外，同时保留稳定的静态站点构建。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.0',
    date: '2026-08-29',
    titleEn: 'Complete EU/EEA baselines and Canada national history',
    titleZh: '欧盟/欧洲经济区基线全覆盖与加拿大国家历史数据',
    summaryEn:
      'All 30 EU/EEA countries, the historical United Kingdom baseline, and Canada PHAC CNDSS are now fully integrated with reviewed source contracts and public provenance.',
    summaryZh:
      '全部 30 个欧盟/欧洲经济区国家、英国历史基线及加拿大 PHAC CNDSS 现已通过经审核的来源契约和公开来源说明实现完整接入。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added 23 ECDC countries with 20,334 observations across 1,193 source series with values; the complete 31-country ECDC collection now contains 27,843 source observations.',
            zh: '新增 23 个 ECDC 国家、20,334 条观测和 1,193 条有值来源序列；完整的 31 国 ECDC 数据集现包含 27,843 条来源观测。',
          },
          {
            en: 'Added Canada PHAC CNDSS with 3,671 national annual observations across 69 source series from 1924 through 2023 under the Open Government Licence – Canada.',
            zh: '依据加拿大开放政府许可，新增加拿大 PHAC CNDSS 1924—2023 年 3,671 条全国年度观测，覆盖 69 条来源序列。',
          },
          {
            en: 'Integrated every new country into Control Center, automation, APIs, bilingual country pages, provenance, coverage, and CSV/JSON/XLSX downloads.',
            zh: '将所有新增国家完整接入控制中心、自动化、API、中英文国家页面、来源追溯、覆盖地图以及 CSV/JSON/XLSX 下载。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Added 24 independent daily publication checks and kept regional baselines separate from higher-frequency national feeds, preventing overlapping records from being counted twice.',
            zh: '新增 24 个独立的每日发布检查，并将区域基线与更高频国家来源保持分离，避免重叠记录被重复计数。',
          },
          {
            en: 'Canonicalized the historical ECDC UK geography to platform code GB while retaining UK in provenance; England-only laboratory notifications remain separate.',
            zh: '将 ECDC 历史来源代码 UK 规范化为平台代码 GB，同时在来源追溯中保留 UK；英格兰实验室通知继续独立管理。',
          },
          {
            en: 'Clarified that Canada values are PHAC published national aggregates with varying jurisdiction coverage, including 44 disease contracts without Manitoba data in 2023.',
            zh: '明确加拿大数值是 PHAC 发布的全国汇总，辖区覆盖会随病种和年份变化，其中 2023 年有 44 个病种契约缺少 Manitoba 数据。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Mapped Canadian viral meningitis to active concept D134 and retained historical non-A/non-B hepatitis as a related source-only series rather than using a deprecated concept.',
            zh: '将加拿大病毒性脑膜炎映射到有效概念 D134，并将历史“非 A 非 B 型肝炎”保留为相关的仅来源序列，不再使用废弃概念。',
          },
          {
            en: 'Made Canada refreshes preserve the full reviewed history and fail closed when the official last year changes; all-null contracts remain pending and 645 explicit zeroes remain observations.',
            zh: '使加拿大刷新保留完整已审核历史，并在官方末年变化时失效关闭；全空契约保持待定，645 个明确零值继续作为观测保留。',
          },
          {
            en: 'Kept Thailand in source research after its public snapshot, login-gated national exports, zero-reporting semantics, and reuse terms failed the publication contract review; no placeholder data were added.',
            zh: '泰国公开快照、需登录的全国导出、零报告语义及复用条款未通过发布契约审核，因此继续保留为来源研究状态，未添加任何占位数据。',
          },
        ],
      },
    ],
  },
  {
    version: '0.8.3',
    date: '2026-08-28',
    titleEn: 'Seven-country ECDC baseline expansion',
    titleZh: 'ECDC 年度基线扩展至七个国家',
    summaryEn:
      'Spain, Italy, Portugal, Poland, Czechia, Greece, and Romania are now fully supported using attributed ECDC closed-year surveillance baselines.',
    summaryZh:
      '西班牙、意大利、葡萄牙、波兰、捷克、希腊和罗马尼亚现已通过注明归属的 ECDC 已闭合年度监测基线实现全量支持。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added 6,618 annual observations across 375 source series with values, backed by 385 reviewed country-specific ECDC contracts from 1990 through 2025.',
            zh: '新增 1990—2025 年 6,618 条年度观测，覆盖 375 条有值来源序列，并由 385 条经审核的国家级 ECDC 契约支撑。',
          },
          {
            en: 'Integrated all seven countries into Control Center, APIs, coverage, bilingual country pages, provenance, and CSV/JSON/XLSX downloads.',
            zh: '将七国全部接入控制中心、API、覆盖地图、中英文国家页面、来源追溯以及 CSV/JSON/XLSX 下载。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Added staggered local-time daily publication checks and normalized the ECDC EL source code to the canonical GR identity for Greece.',
            zh: '新增按各国本地时间错峰执行的每日发布检查，并将 ECDC 的希腊来源代码 EL 规范化为平台标准身份 GR。',
          },
          {
            en: 'Preserved asynchronous disease coverage, source gaps as unknown, explicit zeroes, and source-only composite categories without double counting.',
            zh: '保留各病种异步覆盖、来源断点的未知语义、明确零值及仅来源层复合类别，避免重复计数。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Withheld in-progress current-year Atlas cells so partial Czechia 2026 values cannot appear as completed annual totals.',
            zh: '暂不发布尚未闭合的当年 Atlas 单元，避免捷克 2026 年部分累计值被误呈现为完整年度总量。',
          },
          {
            en: 'Made refreshed ECDC windows authoritative in CSV and database storage so withdrawn cells become unknown instead of remaining stale.',
            zh: '将 ECDC 刷新窗口在 CSV 与数据库中统一设为权威替换，使来源撤回的单元恢复为未知而不会残留旧值。',
          },
        ],
      },
    ],
  },
  {
    version: '0.8.2',
    date: '2026-08-28',
    titleEn: 'France ECDC annual baseline and reusable regional adapter',
    titleZh: '法国 ECDC 年度基线与可复用区域适配器',
    summaryEn:
      'France is now publicly supported across ingestion, Control Center, APIs, downloads, provenance, and country pages using attributed ECDC annual Member-State surveillance data.',
    summaryZh:
      '法国现已通过注明归属的 ECDC 成员国年度监测数据，完整接入摄取、控制中心、API、下载、来源说明和国家页面。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added 891 France annual observations across 51 published series from 1990 through 2025, backed by 55 reviewed ECDC source contracts.',
            zh: '新增法国 1990—2025 年 891 条年度观测，覆盖 51 条已发布序列，并由 55 条经审核的 ECDC 来源合同支撑。',
          },
          {
            en: 'Integrated France into Control Center source status and automation, public APIs, coverage, downloads, bilingual source metadata, and static generation.',
            zh: '将法国接入控制中心来源状态与自动化、公开 API、覆盖地图、下载、双语来源元数据和静态生成。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Added a reusable ECDC Atlas adapter that discovers current measure IDs while failing closed on disease labels, population definitions, geography, frequency, and count semantics.',
            zh: '新增可复用的 ECDC Atlas 适配器：动态发现当前指标 ID，同时对病种标签、人群定义、地理层级、频率和病例计数语义实行失效关闭。',
          },
          {
            en: 'Enabled a daily 09:50 Europe/Paris publication check for annual source revisions.',
            zh: '启用每日 09:50（Europe/Paris）的发布检查，以发现年度来源修订。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Governance',
        labelZh: '治理',
        items: [
          {
            en: 'Missing country/year cells stay unknown, explicit zeroes are preserved, composite categories remain source-only, and rate-only hepatitis B totals are not converted into case counts.',
            zh: '缺失的国家/年份单元保持未知，明确零值予以保留，复合类别仅保留来源序列，且不会把仅有比率的乙肝总量换算成病例数。',
          },
          {
            en: 'Every France record carries the required ECDC and reporting Member-State attribution.',
            zh: '每条法国记录均保留 ECDC 与报告成员国所要求的归属说明。',
          },
        ],
      },
    ],
  },
  {
    version: '0.8.1',
    date: '2026-08-28',
    titleEn: 'Singapore weekly surveillance and full public integration',
    titleZh: '新加坡周度监测与公开端全量接入',
    summaryEn:
      'Singapore is now supported across ingestion, Control Center, public APIs, downloads, provenance pages, and the country experience, with official CSV history joined safely to current CDA workbooks.',
    summaryZh:
      '新加坡现已接入数据摄取、控制中心、公开 API、下载、来源说明和国家页面，并将官方 CSV 历史与当前 CDA 工作簿安全衔接。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added Singapore national weekly notifications from the official 2012–2022 data.gov.sg CSV and 2023+ CDA annual workbooks, with 2023 weekly PDFs as a fail-closed fallback.',
            zh: '新增新加坡全国周度病例通报：历史采用 data.gov.sg 2012—2022 年官方 CSV，2023 年起采用 CDA 年度工作簿，并以 2023 年周报 PDF 作为失效关闭的回退来源。',
          },
          {
            en: 'Registered 76 historical and current source series across 39 source categories, with complete source URLs, hashes, definition versions, and missing-value semantics.',
            zh: '为 39 个来源分类注册 76 条历史与当前来源序列，并完整保留来源 URL、哈希、定义版本和缺失值语义。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Added a strict temporal-handoff projection so equivalent historical and successor series appear as one longitudinal curve only when their validity windows do not overlap.',
            zh: '新增严格的时间接力投影：仅当历史与后继序列口径一致且有效期不重叠时，才将其呈现为一条纵向曲线。',
          },
          {
            en: 'Integrated Singapore into Control Center source status, public country coverage, About metadata, bilingual labels, downloads, and static-site generation.',
            zh: '将新加坡完整接入控制中心来源状态、公开国家覆盖、About 元数据、双语标签、下载和静态站点生成。',
          },
          {
            en: 'Enabled a daily 09:30 Asia/Singapore Control Center publication check to discover each new weekly CDA workbook and bounded revisions automatically.',
            zh: '在控制中心启用每日 09:30（Asia/Singapore）的发布检查，自动发现新的 CDA 周度工作簿及有界修订。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Governance',
        labelZh: '治理',
        items: [
          {
            en: 'Public release is explicitly operator-authorized while provenance continues to distinguish the Singapore Open Data Licence for history from CDA terms requiring written permission for current publications.',
            zh: '公开发布以运营方明确授权为依据；溯源仍区分历史数据适用的新加坡开放数据许可与当前 CDA 发布物所记录的书面许可要求。',
          },
        ],
      },
    ],
  },
  {
    version: '0.8.0',
    date: '2026-08-27',
    titleEn: 'Auditable data governance and control-plane reliability',
    titleZh: '可审计数据治理与控制面可靠性',
    summaryEn:
      'This release closes the operational loop from source ingestion through evidence review and publication: every enabled country schedule was exercised through Control Center, Research Radar gains fail-closed AI and backlog governance, and background tasks now recover safely without hiding provider failures.',
    summaryZh:
      '本次更新打通从来源摄取、证据审核到数据发布的运维闭环：通过控制中心实际运行全部已启用国家调度，为研究雷达加入失效关闭的 AI 审核和积压治理，并使后台任务可安全恢复且不再掩盖来源失败。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added evidence-bounded AI review for weekly Research Radar briefs, with deterministic preflight checks, strict schemas, safe model aliases, fingerprint invalidation, concurrency protection, and explicit separation from editorial approval.',
            zh: '新增周报证据边界 AI 审核，包含确定性预检、严格结构、安全模型别名、指纹失效、并发保护，并与编辑签审明确分离。',
          },
          {
            en: 'Added dry-run-first governance and recovery tools for editorial backlog, metadata coverage, stale ingest runs, and abandoned background tasks, with bounded plans and explicit apply gates.',
            zh: '新增默认预演的治理与恢复工具，覆盖编辑积压、元数据覆盖、超时摄取运行和遗留后台任务，并提供有界计划与显式写入门。',
          },
          {
            en: 'Added optional Springer Nature, Elsevier, bioRxiv/medRxiv, and publisher RSS connectors with bounded pagination, resumable checkpoints, source isolation, credential gates, and preprint review controls; paid publisher sources remain off by default.',
            zh: '新增可选的 Springer Nature、Elsevier、bioRxiv/medRxiv 和出版商 RSS 连接器，支持有界分页、断点续运、来源隔离、凭据门和预印本审核；付费出版商来源仍默认关闭。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Hardened worker and scheduler ownership with Redis singleton leases, task heartbeats, stale-task recovery, worker-readiness watchdogs, richer health evidence, and safer systemd ordering.',
            zh: '通过 Redis 单例租约、任务心跳、超时任务恢复、worker 就绪看门狗、更完整的健康证据与更安全的 systemd 顺序，强化 worker 和 scheduler 所有权。',
          },
          {
            en: 'Made source health reflect completed per-provider result contracts instead of configured intent, and exposed actionable catch-up state, resume thresholds, queue age, and worker/scheduler readiness.',
            zh: '来源健康现在依据已完成的逐提供商结果契约，而不是配置意图；同时展示可执行的追赶状态、恢复阈值、队列年龄以及 worker/scheduler 就绪度。',
          },
          {
            en: 'Improved public release quality with stricter canonical and hreflang policy, real 301 legacy redirects, compact graph rendering, scalable HTML budgets, clearer copyright and provenance, and email-delivery feedback handling.',
            zh: '通过更严格的 canonical/hreflang 策略、真实 301 旧链接跳转、紧凑图谱渲染、可扩展 HTML 预算、更清晰的版权与溯源信息，以及邮件送达反馈处理，提升公开发布质量。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Verified all 21 enabled country and regional schedules through Control Center and fixed CDC NHSS HIV ingestion by using an honest provider-specific crawler identity; the acceptance run completed without source errors.',
            zh: '通过控制中心验证全部 21 个已启用国家和地区调度，并通过透明的提供商专用爬虫标识修复 CDC NHSS HIV 摄取；验收运行无来源错误完成。',
          },
          {
            en: 'Fixed exact-limit OAI checkpoint rollover, optional-provider checkpoint loss, metadata backfill selection and completion boundaries, model bootstrap query storms, and read-path mutation of overdue catch-up schedules.',
            zh: '修复 OAI 精确上限时的检查点翻页、可选来源检查点丢失、元数据回填选批与完成边界、模型初始化查询风暴，以及读路径修改逾期追赶调度的问题。',
          },
          {
            en: 'Made bilingual research summaries use the English evidence set as a canonical semantic contract so Chinese output cannot add, omit, or strengthen claims independently.',
            zh: '双语研究摘要现在以英文证据集作为规范语义契约，防止中文输出独立增加、遗漏或强化主张。',
          },
        ],
      },
    ],
  },
  {
    version: '0.7.3',
    date: '2026-08-23',
    titleEn: 'Downloads catalogue ergonomics and release-pipeline recovery',
    titleZh: '下载目录体验与发布流水线恢复能力改进',
    summaryEn:
      'This release turns Downloads into a more practical data catalogue with shareable filters, sorting, sticky table context, and mobile cards, while improving Taiwan monthly recovery and hardening automated publication checks.',
    summaryZh:
      '本次更新将 Downloads 打磨为更实用的数据目录，新增可分享筛选、排序、吸顶表头和移动端卡片，同时改进台湾月度数据恢复并强化自动发布校验。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added dataset sorting on Downloads by featured order, name, record volume, and dataset type so large catalogues are easier to scan.',
            zh: 'Downloads 新增按默认顺序、名称、记录量和数据集类型排序，便于浏览大型数据目录。',
          },
          {
            en: 'Added shareable Downloads state in the URL for search text, dataset type, and sort mode, with direct links restoring the same catalogue view.',
            zh: 'Downloads 的搜索词、数据集类型和排序方式会写入 URL，直接打开链接即可恢复相同目录视图。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Reworked the Downloads table into a stable data-tool layout with fixed format columns, a sticky header on desktop, and a card layout on mobile to avoid horizontal scrolling.',
            zh: '重构 Downloads 表格为更稳定的数据工具布局：桌面端固定格式列并吸顶表头，移动端改为卡片布局以避免横向滚动。',
          },
          {
            en: 'Raised the public-site HTML performance budget to match the expanded Research graph while keeping the full production build inside the total gzip budget.',
            zh: '根据扩展后的 Research graph 调整公开站点 HTML 性能预算，同时保持完整生产构建仍在总 gzip 预算内。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed misaligned Downloads rows by restoring native table-cell layout and constraining CSV, JSON, XLSX, and source-note columns.',
            zh: '修复 Downloads 行列错位：恢复原生 table-cell 布局，并约束 CSV、JSON、XLSX 与来源说明列宽。',
          },
          {
            en: 'Recovered Taiwan NIDSS national monthly updates from cached raw monthly files when live CSV endpoints are temporarily unavailable.',
            zh: '当台湾 NIDSS 在线 CSV 端点暂时不可用时，可从本地 raw 月度缓存恢复全国月度数据更新。',
          },
          {
            en: 'Made GitHub direct-download verification retry after publication so transient Contents API visibility delays no longer fail an otherwise successful release.',
            zh: '为 GitHub 直接下载仓库的发布后校验加入重试，避免 Contents API 短暂不可见导致已成功的发布误失败。',
          },
          {
            en: 'Hardened China registry coverage policy and Research export fallback text for edge cases with incomplete disease metadata.',
            zh: '强化中国数据 registry coverage 策略，并修复研究导出在疾病元数据不完整时的中文 fallback 文案。',
          },
        ],
      },
    ],
  },
  {
    version: '0.7.2',
    date: '2026-08-19',
    titleEn: 'Calibrated Situation Room controls and public-site reliability',
    titleZh: '态势室校准控制与公开站点可靠性改进',
    summaryEn:
      'This maintenance release advances Situation Room to a calibrated, multi-horizon v3.2 workflow and hardens the public GIDS experience with offline country flags, release-linked versioning, and broader browser regression checks.',
    summaryZh:
      '本次维护版本将态势室推进到经校准的多窗口 v3.2 工作流，并通过本地国旗资源、关联更新记录的版本信息和更广泛的浏览器回归检查，增强 GIDS 公开站点的可靠性。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added the Situation Room v3.2 multi-horizon Gamma-Poisson detector, which evaluates weekly and monthly count horizons through one correlated omnibus test and applies cadence- and expected-count-specific effect gates.',
            zh: '新增态势室 v3.2 多窗口 Gamma-Poisson 检测器：通过一个保留相关性的综合检验评估周度和月度计数窗口，并按频率和预期计数应用效应门槛。',
          },
          {
            en: 'Added durable event labels, calibration artifacts, and per-signal publication-policy decisions, with database migration, operator APIs, and scripts for auditable calibration registration.',
            zh: '新增可持久化的事件标签、校准制品和逐信号发布策略决策，并提供数据库迁移、运营 API 与脚本，以支持可审计的校准登记。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Made automatic Situation verification fail closed by source and cadence group until a registered calibration meets null-family safety, sensitivity, detection-delay, and optional official-corroboration requirements.',
            zh: '按来源和频率分组强化态势自动核验的失效关闭机制；只有已登记校准满足零假设族安全性、灵敏度、检出延迟及可选官方佐证要求后才可启用。',
          },
          {
            en: 'Expanded public-site and dashboard regression coverage for accessibility, responsive navigation, release provenance, and the new Situation calibration and policy-decision surfaces.',
            zh: '扩展公众站点与控制台的回归覆盖，涵盖无障碍、响应式导航、发布溯源，以及新增的态势校准和策略决策界面。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Restored country flags in Data Coverage map labels and hover tooltips with bundled local SVG assets, including every country and region in the coverage roadmap.',
            zh: '使用随站点打包的本地 SVG 资源恢复 Data Coverage 地图标签和悬浮提示中的国旗，并覆盖路线图中的全部国家和地区。',
          },
          {
            en: 'Made the footer version derive from the application package and link to the localized Changelog, preventing version text from drifting from the release manifest.',
            zh: '使底栏版本号从应用包清单自动读取并链接到本地化更新记录，避免版本文本与发布清单发生偏差。',
          },
        ],
      },
    ],
  },
  {
    version: '0.7.1',
    date: '2026-08-18',
    titleEn: 'Brand, bilingual experience, and release confidence rebuild',
    titleZh: '品牌、双语体验与发布可信度重构',
    summaryEn:
      'This release rebuilds the public GIDS experience around a scientific editorial brand, completes the first full pass of bilingual public navigation and research routes, and hardens accessibility, search, performance, and deployment verification.',
    summaryZh:
      '本次更新围绕科学编辑部式品牌重构 GIDS 公众体验，完成第一轮完整双语导航与研究路由覆盖，并强化无障碍、搜索、性能和部署一致性校验。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced the refreshed GIDS identity, local brand assets, a unified self-hosted interface typeface, and a separate Control Center sub-brand.',
            zh: '推出更新后的 GIDS 品牌系统、本地品牌资源、统一的自托管界面字体，以及独立的 Control Center 子品牌。',
          },
          {
            en: 'Added a compact static search index at `/site-data/search-index.json`, a noindex `/search/` experience, localized `/zh/search/`, and a Cmd/Ctrl+K quick search panel grouped across countries, diseases, Situation, reports, research, and pages.',
            zh: '新增紧凑静态搜索索引 `/site-data/search-index.json`、noindex 的 `/search/` 搜索页、本地化 `/zh/search/`，以及按国家、疾病、态势、报告、研究和页面分组的 Cmd/Ctrl+K 快速搜索面板。',
          },
          {
            en: 'Expanded Chinese public coverage with locale-aware Research routes, matching canonical and hreflang metadata, and sitemap entries for Research index, articles, topics, countries, diseases, integrity, preprints, graph, and weekly pages.',
            zh: '扩展中文公众覆盖，新增 locale-aware 的 Research 路由、对应 canonical 与 hreflang 元数据，并将研究首页、文章、主题、国家、疾病、完整性、预印本、图谱和周报纳入 sitemap。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Restructured the public home, header, footer, country pages, disease pages, Situation pages, report pages, and downloads entry around current status, source trust, analysis pathways, and clearer professional navigation.',
            zh: '围绕当前态势、来源可信度、分析入口和更清晰的专业导航，重组公众首页、页眉、页脚、国家页、疾病页、态势页、报告页和下载入口。',
          },
          {
            en: 'Improved mobile usability by removing the oversized mobile hero, tightening the menu and Control Center shell, preserving keyboard focus, supporting Escape dismissal, and preventing horizontal overflow across tested viewports.',
            zh: '优化移动端体验，移除过高的移动首屏英雄区，收紧菜单和 Control Center 外壳，保留键盘焦点，支持 Escape 关闭，并在测试视口中防止横向溢出。',
          },
          {
            en: 'Hardened accessibility and data controls with stable id/name attributes, labels, help-text associations, clearer CTA copy, stronger contrast, chart/table alternatives, and WCAG checks across public and Control Center routes.',
            zh: '强化无障碍和数据控件，为表单补齐稳定 id/name、标签与帮助文本关联，优化 CTA 文案和对比度，并为图表/表格替代视图及公众端、Control Center 路由加入 WCAG 检查。',
          },
          {
            en: 'Rebalanced public performance budgets for JavaScript chunks, route assets, HTML gzip size, and font payloads, while keeping chart rendering lazy and replacing runtime FlagCDN requests with local region markers.',
            zh: '重新校准公众端 JavaScript chunk、路由资源、HTML gzip 和字体体积预算，同时保留图表懒加载，并以本地区域标记替代运行时 FlagCDN 请求。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Changed language switching from DOM-only text replacement to locale URL navigation so the visible URL, page title, document language, canonical URL, and alternate links stay synchronized.',
            zh: '将语言切换从仅替换 DOM 文本改为跳转对应 locale URL，使可见地址、页面标题、文档语言、canonical 和 alternate 链接保持同步。',
          },
          {
            en: 'Added a post-deployment source-commit verifier so production HTML must expose the expected `gids-source-commit` before a release is accepted.',
            zh: '新增部署后源码 commit 校验器，要求生产 HTML 暴露预期的 `gids-source-commit` 后发布才可通过。',
          },
        ],
      },
    ],
  },
  {
    version: '0.7.0',
    date: '2026-08-17',
    titleEn: 'Situation Room v3, verified alerts, and evidence-grade Research Radar',
    titleZh: '态势室 v3、已核验提醒与证据级研究雷达',
    summaryEn:
      'This release moves Situation Room onto a versioned, auditable v3 contract, expands Research Radar into a searchable evidence product, and adds safer subscription, alerting, release, and quality-gate automation.',
    summaryZh:
      '本次更新将态势室迁移到可审计的 v3 版本化契约，扩展研究雷达为可检索的证据产品，并新增更安全的订阅、提醒、发布和质量门控自动化。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced Situation Room v3 with Pydantic-owned contracts, immutable daily, weekly, and monthly reports, versioned public JSON, source-readiness ledgers, and dedicated public archive routes.',
            zh: '推出态势室 v3，使用 Pydantic 作为契约源，支持不可变的日、周、月报告、版本化公开 JSON、来源就绪度台账和专用公开归档路由。',
          },
          {
            en: 'Added a Situation v3 operations API and dashboard workspace for runs, signals, source health, event clusters, reports, audited review decisions, publish actions, and rollback.',
            zh: '新增态势 v3 运营 API 与控制台工作区，覆盖运行、信号、来源健康、事件聚类、报告、审计化复核决策、发布操作和回滚。',
          },
          {
            en: 'Expanded Research Radar with Ask GIDS Research, an evidence graph, topic and country collections, preprint and integrity registers, scoped RSS feeds, social cards, and a richer public catalogue.',
            zh: '扩展研究雷达，新增“问研究雷达”、证据图谱、主题与国家集合、预印本与完整性登记、分范围 RSS、社交分享卡片和更丰富的公开目录。',
          },
          {
            en: 'Added subscription support for weekly Research Radar digests and verified Situation alerts, including D1 migrations, preference filters, idempotent campaigns, an alert outbox, and optional Cloudflare Queue fan-out.',
            zh: '新增研究雷达周报和已核验态势提醒的订阅支持，包括 D1 迁移、偏好筛选、幂等 campaign、提醒 outbox 和可选 Cloudflare Queue 分发。',
          },
          {
            en: 'Added production-oriented GitHub workflows for Situation Room release gates, exact artifact deployment verification, reviewed-alert dispatch, full project quality checks, and PostgreSQL migration smoke tests.',
            zh: '新增面向生产的 GitHub 工作流，支持态势室发布门控、精确制品部署验证、已复核提醒分发、全项目质量检查和 PostgreSQL 迁移冒烟测试。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Strengthened Situation analysis with source-cadence maturity windows, deterministic geography identity, bounded concurrent adapters, robust quasi-Poisson modeling, rare-count tail correction, and detector-tier FDR families.',
            zh: '加强态势分析，加入来源频率成熟窗口、确定性地理身份、有界并发适配器、稳健 quasi-Poisson 建模、稀有计数尾部修正和按检测层级划分的 FDR 检验族。',
          },
          {
            en: 'Made Situation publication fail closed with immutable history storage, quality-gated pointer advancement, calibrated backtesting, guarded automation diagnostics, and analyst-review-only production alert dispatch.',
            zh: '让态势发布默认失败关闭，支持不可变历史存储、质量门控后的指针推进、校准回测、受控自动化诊断，以及生产环境仅分发人工复核提醒。',
          },
          {
            en: 'Upgraded Research Radar ingestion with controlled discovery, publisher RSS, WHO IRIS guidance metadata, OpenAlex and Unpaywall enrichment, resumable metadata backfill, version-5 classification, and privacy-safe health checks.',
            zh: '升级研究雷达接入，支持受控发现、出版社 RSS、WHO IRIS 指南元数据、OpenAlex 与 Unpaywall 增强、可恢复元数据回填、v5 分类和隐私安全健康检查。',
          },
          {
            en: 'Improved static-site reliability and performance with deterministic build fixtures, research release validation, ECharts bundle splitting, world-map optimization, font/logo assets, sitemap coverage, and route-level performance budgets.',
            zh: '提升静态站点可靠性与性能，加入确定性构建夹具、研究发布验证、ECharts 拆包、世界地图优化、字体与标志资源、站点地图覆盖和路由级性能预算。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed unattended ingestion and data-release recovery so transient scheduled failures park in retrying, requeue atomically, preserve their task identity, and keep crawl-run audit rows from remaining indefinitely active.',
            zh: '修复无人值守接入与数据发布恢复流程，使计划任务的暂态失败进入 retrying、到期后原子化重入队列、保留原任务身份，并避免抓取运行审计行永久停留在活跃状态。',
          },
          {
            en: 'Tightened public evidence boundaries so raw abstracts, provider payloads, PDFs, unreviewed summaries, stale contracts, unverified automated alerts, and invalid research or Situation artifacts fail before publication.',
            zh: '收紧公开证据边界，确保原始摘要、供应商载荷、PDF、未复核摘要、陈旧契约、未核验自动提醒以及无效研究或态势制品在发布前失败关闭。',
          },
        ],
      },
    ],
  },
  {
    version: '0.6.1',
    date: '2026-08-14',
    titleEn: 'Research Radar, Situation Room v2, and stronger analysis workflows',
    titleZh: '研究雷达、态势室 v2 与更强的分析工作流',
    summaryEn:
      'This release introduces public literature intelligence, upgrades the Situation Room into a reviewable signal system, and gives operators safer automation, release, and disease-mapping controls.',
    summaryZh:
      '本次更新上线公开文献情报能力，将态势室升级为可复核的信号系统，并为运营人员提供更安全的自动化、发布和疾病映射控制。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added Research Radar with public literature pages for articles, disease evidence hubs, country collections, topic collections, weekly briefs, catalogue JSON, RSS, and curated historical baselines.',
            zh: '新增研究雷达，提供公开文献文章页、疾病证据中心、国家集合、主题集合、周报、目录 JSON、RSS 以及经策展的历史基线文献。',
          },
          {
            en: 'Added a literature operations workspace with Crossref and Europe PMC synchronization, editorial review, evidence-gap discovery, autopilot policy gates, and model-enriched bilingual summaries.',
            zh: '新增文献运营工作区，支持 Crossref 与 Europe PMC 同步、编辑复核、证据缺口发现、自动策略门控以及模型增强的中英文摘要。',
          },
          {
            en: 'Introduced Situation Room v2 with daily, weekly, and monthly snapshots, a dedicated history database, methodology pages, public/shadow preview controls, and richer signal detail pages.',
            zh: '推出态势室 v2，支持日、周、月快照、专用历史数据库、方法页、公开/影子预览控制以及更丰富的信号详情页面。',
          },
          {
            en: 'Added Austria and Germany source mappings, reviewed mapping registries, expanded provisional-source fixtures, and migrations for situation history, source tasks, and literature evidence gaps.',
            zh: '新增奥地利和德国来源映射、已复核映射注册表、扩展的临时来源测试夹具，并加入态势历史、来源任务和文献证据缺口迁移。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Upgraded epidemic curves with monitor, compare, and outbreak analysis modes, comparability safeguards, stable series colors, historical reference bands, event markers, and provisional-period treatment.',
            zh: '升级流行曲线，新增监测、比较和暴发分析模式，并加入可比性保护、稳定序列配色、历史参考带、事件标记和临时数据期间处理。',
          },
          {
            en: 'Expanded Situation Room scoring with respiratory, increasing, emerging, and unusual sections, priority queues, source freshness checks, quality gates, and SEO-safe public publication rules.',
            zh: '扩展态势室评分，覆盖呼吸道、上升、新发和异常栏目，并加入优先队列、来源新鲜度检查、质量门控和 SEO 安全的公开发布规则。',
          },
          {
            en: 'Strengthened disease mapping automation with retry windows, provider cooldowns, digest notifications, source-category reconciliation, and safer AI-assisted review workflows.',
            zh: '加强疾病映射自动化，支持重试窗口、供应商冷却、摘要通知、来源类别对账以及更安全的 AI 辅助复核流程。',
          },
          {
            en: 'Improved data release publishing with parallel raw/archive publishers, resumable GitHub pushes, SSH-over-443 fallback, atomic site-data writes, and expanded repository-boundary documentation.',
            zh: '优化数据发布，支持原始归档与下载发布器并行、可恢复的 GitHub 推送、SSH 443 端口回退、原子化站点数据写入以及更完整的仓库边界文档。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Kept Research Radar public JSON limited to published, integrity-safe metadata and GIDS-authored summaries; raw abstracts and provider payloads stay outside the static site.',
            zh: '确保研究雷达公开 JSON 只包含已发布且完整性安全的元数据与 GIDS 自有摘要，原始摘要和供应商响应不会进入静态站点。',
          },
          {
            en: 'Added regression coverage for Situation Room v2 statistics, history services, provisional ingestion policies, literature radar flows, task-log compaction, sitemap entries, and expanded source processors.',
            zh: '新增态势室 v2 统计、历史服务、临时接入策略、文献雷达流程、任务日志压缩、站点地图条目和扩展来源处理器的回归测试。',
          },
        ],
      },
    ],
  },
  {
    version: '0.5.3',
    date: '2026-08-10',
    titleEn: 'Broader surveillance coverage and a clearer public data experience',
    titleZh: '扩展监测覆盖，并改进公开数据体验',
    summaryEn:
      'This release adds new European source pipelines, expands public discovery with situation pages and multilingual routes, and gives operators stronger source, settings, and disease-mapping workflows.',
    summaryZh:
      '本次更新新增欧洲来源接入，利用态势页面和多语言路由扩展公开数据发现能力，并为运营人员提供更完善的来源、设置和疾病映射工作流。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added Austria AGES Radar, Germany RKI SurvStat, and Ireland HPSC source definitions, crawlers, processors, reporting policies, and regression coverage.',
            zh: '新增奥地利 AGES Radar、德国 RKI SurvStat 和爱尔兰 HPSC 的来源定义、抓取器、处理器、报告策略及回归测试。',
          },
          {
            en: 'Added a public Situation Room with weekly pages, disease summaries, surveillance notes, and dedicated data exports.',
            zh: '新增公开态势中心，提供周度页面、疾病摘要、监测注释和专用数据导出。',
          },
          {
            en: 'Added multilingual Chinese routes, country-disease pages, custom 404 handling, segmented sitemaps, and richer SEO page metadata.',
            zh: '新增中文多语言路由、国家疾病页面、自定义 404、分片站点地图和更完整的 SEO 页面元数据。',
          },
          {
            en: 'Added an AI-assisted disease-mapping workspace with registry, audit, automation, and notification services in the operations dashboard.',
            zh: '在运营控制面板新增 AI 辅助疾病映射工作区，以及注册表、审计、自动化和通知服务。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Expanded country and source policy configuration with revision windows, fill-missing behavior, source aliases, licensing states, and permission-gated release controls.',
            zh: '扩展国家与来源策略配置，支持修订窗口、缺失填补、来源别名、授权状态和权限控制的发布策略。',
          },
          {
            en: 'Reworked the dashboard settings, automation, and source flows to expose operational state and configuration more consistently.',
            zh: '重构控制面板的设置、自动化和来源流程，更一致地展示运营状态与配置。',
          },
          {
            en: 'Improved static-site builds, redirects, analytics configuration, country coverage metadata, and report-page navigation for the expanded public routes.',
            zh: '改进静态站点构建、重定向、分析配置、国家覆盖元数据和扩展公开路由后的报告页导航。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed source-scope canonicalization and legacy-access safeguards so newly ingested categories remain separated from reviewed compatibility projections.',
            zh: '修复来源范围规范化和旧版访问保护，确保新接入类别与经审核的兼容投影保持分离。',
          },
          {
            en: 'Added regression coverage for European source scopes, dynamic month policies, settings behavior, situation pages, disease mappings, and surveillance-note overrides.',
            zh: '新增欧洲来源范围、动态月份策略、设置行为、态势页面、疾病映射和监测注释覆盖规则的回归测试。',
          },
        ],
      },
    ],
  },
  {
    version: '0.5.2',
    date: '2026-08-08',
    titleEn: 'Faster data delivery and incremental exports',
    titleZh: '更快的数据加载与增量导出',
    summaryEn:
      'This release reduces initial country-page work, serves compressed static assets with explicit cache policy, and makes public download exports incremental, atomic, and parallel while retaining the newly added surveillance regions.',
    summaryZh:
      '本次更新降低国家/地区页面的首次加载负担，为静态资源提供压缩与明确缓存策略，并将公开下载导出改为增量、原子和并行处理，同时保留近期新增的监测地区。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added Ontario, Finland, Iceland, Norway, and Sweden to the public surveillance coverage released in the 0.5 series.',
            zh: '将安大略、芬兰、冰岛、挪威和瑞典纳入 0.5 系列已发布的公开监测覆盖范围。',
          },
          {
            en: 'Added a lazy source-series payload for country trend charts, preserving source selection without embedding complete observations in the initial HTML document.',
            zh: '为国家趋势图新增按需加载的来源序列数据，在保留来源选择功能的同时，不再把完整观测值嵌入初始 HTML 文档。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'The static site origin now serves gzip-compressed text assets with cache headers for hashed build files, mutable site data, and HTML documents.',
            zh: '静态站点源站现为文本资源提供 gzip 压缩，并分别为带哈希构建文件、可变站点数据和 HTML 文档设置缓存策略。',
          },
          {
            en: 'Country heatmaps now reuse the precomputed export instead of rebuilding all month-by-disease cells in the browser.',
            zh: '国家热图现直接复用预计算导出结果，不再在浏览器中重建全部“月份 × 疾病”单元格。',
          },
          {
            en: 'Site JSON generation now preserves unchanged files, replaces changed files atomically, and removes stale artifacts only after a successful write pass.',
            zh: '站点 JSON 生成现会保留未变化文件、原子替换已变化文件，并仅在成功写入后清理过期产物。',
          },
          {
            en: 'Changed CSV, JSON, and XLSX download partitions now render in parallel while historical partitions continue to be reused by content hash.',
            zh: '发生变化的 CSV、JSON 和 XLSX 下载分区现可并行生成，而历史分区继续按内容哈希复用。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Deferred below-the-fold disease charts until they approach the viewport, reducing unnecessary JavaScript work during initial navigation.',
            zh: '将疾病页首屏以下的图表延迟至接近视口时加载，减少首次访问时不必要的 JavaScript 工作。',
          },
        ],
      },
    ],
  },
  {
    version: '0.5.1',
    date: '2026-08-07',
    titleEn: 'Five new surveillance regions and clearer source notes',
    titleZh: '新增五个监测地区，并改进来源注释呈现',
    summaryEn:
      'This release expands public surveillance coverage to Ontario, Finland, Iceland, Norway, and Sweden, strengthens disease-source mapping safeguards, and moves complex source notes out of chart legends into a dedicated data-notes area.',
    summaryZh:
      '本次更新将公开监测覆盖扩展至安大略、芬兰、冰岛、挪威和瑞典，加强疾病来源映射保护，并把复杂来源说明从图例中移至专门的数据注释区。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added first-class ingestion, mapping, source-series registration, tests, and public country pages for Ontario, Finland, Iceland, Norway, and Sweden.',
            zh: '为安大略、芬兰、冰岛、挪威和瑞典新增一等接入能力，包括抓取、映射、来源序列注册、测试和公开国家/地区页面。',
          },
          {
            en: 'Added dynamic monthly/current-period controls for supported Nordic sources, including provisional current-month handling and revision-window refresh options.',
            zh: '为支持的北欧来源新增动态月度/当前期间控制，包括临时当前月处理和修订窗口刷新选项。',
          },
          {
            en: 'Added SEO-oriented country, disease, report, sitemap, and structured-data helpers so public pages are easier for search engines to discover.',
            zh: '新增面向 SEO 的国家、疾病、报告、站点地图和结构化数据工具，使公开页面更容易被搜索引擎发现。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Strengthened disease ontology and mapping rules for mixed-grain, non-additive, antimicrobial-resistance, STI, respiratory, and historical workbook series.',
            zh: '加强混合粒度、不可加总、耐药监测、性传播感染、呼吸道监测和历史工作簿序列的疾病本体与映射规则。',
          },
          {
            en: 'Updated the operations dashboard source flow to show source policy, availability, current-period support, revision windows, and Iceland history-source constraints consistently.',
            zh: '更新运营控制面板的来源流程，一致展示来源策略、可用性、当前期间支持、修订窗口和冰岛历史来源限制。',
          },
          {
            en: 'Kept source-series observations authoritative while documenting each remaining legacy compatibility projection in the reviewed access baseline.',
            zh: '保持来源序列观测为权威层，并在已审查访问基线中记录仍需保留的旧版兼容投影。',
          },
          {
            en: 'Moved verbose chart source definitions, reporting basis, availability, and aggregation policy into a bottom data-notes panel instead of overloading the legend.',
            zh: '将冗长的图表来源定义、报告口径、可用状态和聚合策略移入底部数据注释栏，不再挤入图例。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed the epidemic-curve frame so a selector sidebar no longer hides the legend; legends now remain visible in a compact footer when both are present.',
            zh: '修复流行曲线框架中筛选侧栏会隐藏图例的问题；当筛选器与图例同时存在时，图例会以紧凑底栏呈现。',
          },
          {
            en: 'Updated ontology-export tests and legacy-access guard baselines for the expanded registry so full repository validation passes cleanly.',
            zh: '随扩展后的注册表更新本体导出测试和旧版访问保护基线，使完整仓库验证干净通过。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.5',
    date: '2026-08-07',
    titleEn: 'Cleaner charts and smoother time navigation',
    titleZh: '更清爽的图表与更顺畅的时间导航',
    summaryEn:
      'This release streamlines chart presentation, makes epidemic-curve time navigation faster and easier to control, and keeps charts and selectors precisely aligned across standard and full-screen views.',
    summaryZh:
      '本次更新精简图表呈现，提升流行曲线时间范围操作的速度与易用性，并让普通及全屏模式下的图表与筛选器保持精确对齐。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added a dedicated reset control beside the epidemic-curve time slider to restore the complete reporting period in one click.',
            zh: '在流行曲线时间控制条旁新增重置按钮，可一键恢复完整报告周期。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Redesigned epidemic-curve time navigation with clearer drag handles, live visual feedback, smoother state synchronization, range panning, brush selection, and deliberate Ctrl-wheel zooming.',
            zh: '重新设计流行曲线时间导航，提供更清晰的拖拽手柄、实时视觉反馈、更顺畅的状态同步、区间平移、框选以及需按住 Ctrl 的滚轮缩放。',
          },
          {
            en: 'Removed redundant chart instructions and implementation notes while retaining actionable controls, legends, dates, and data summaries.',
            zh: '移除冗余的图表操作说明和实现提示，同时保留可操作控件、图例、日期及数据摘要。',
          },
          {
            en: 'Unified the visual alignment of chart plotting areas, time controls, reset actions, and selector panels in standard and full-screen layouts.',
            zh: '统一普通与全屏布局中绘图区、时间控件、重置操作和筛选面板的视觉对齐。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Removed the extra 60-pixel space below monthly pattern charts that occurred when the year selector made the grid row taller than the chart.',
            zh: '修复年份筛选器将网格行撑高后，月度变化图下方多出 60 像素空白的问题。',
          },
          {
            en: 'Corrected bottom-edge mismatches between standard-view charts, scrollable selectors, and the epidemic-curve time slider.',
            zh: '修复普通模式下图表、可滚动筛选器与流行曲线时间控制条底边不一致的问题。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.4',
    date: '2026-08-06',
    titleEn: 'Accurate reporting periods and stronger source coverage',
    titleZh: '更准确的报告周期与更完善的知识来源',
    summaryEn:
      'This release aligns surveillance records by their real weekly, monthly, or annual reporting period, restores population denominators automatically, and improves discovery of reviewed official disease sources.',
    summaryZh:
      '本次更新按真实的周、月或年报告周期对齐监测记录，自动恢复人口分母数据，并改进经审核官方疾病来源的发现能力。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added a shared reporting-period model that identifies weekly records by ISO week, monthly records by month, and annual records by year.',
            zh: '新增共享报告周期模型，分别使用 ISO 周、月份和年份识别周报、月报与年报记录。',
          },
          {
            en: 'Added configurable, reviewed disease-source hints with aliases and prioritized official URLs.',
            zh: '新增可配置、经审核的疾病来源提示，支持别名与优先官方链接。',
          },
          {
            en: 'Added regression tests for reporting-period alignment, population imports, site projections, and disease knowledge source hints.',
            zh: '新增报告周期对齐、人口数据导入、站点投影和疾病知识来源提示的回归测试。',
          },
          {
            en: 'Added public CSV, JSON, and XLSX downloads partitioned into stable time windows for every country and disease dataset.',
            zh: '为每个国家和疾病数据集新增按稳定时间窗口分块的 CSV、JSON 和 XLSX 公开下载。',
          },
          {
            en: 'Added a versioned download manifest containing record ranges, file sizes, SHA-256 checksums, and direct public URLs.',
            zh: '新增带版本的下载清单，记录数据范围、文件大小、SHA-256 校验值与直接公开链接。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Aligned registry and legacy surveillance layers by source reporting period instead of requiring identical calendar dates.',
            zh: '将新版序列与旧版监测层按来源报告周期对齐，不再要求日历日期完全相同。',
          },
          {
            en: 'Site generation now idempotently restores UN World Population Prospects denominators after database or country rebuilds.',
            zh: '站点生成现会在数据库或国家数据重建后幂等恢复联合国世界人口展望分母数据。',
          },
          {
            en: 'Disease knowledge discovery now uses ontology labels, local source labels and codes, configured aliases, and reviewed official entry pages.',
            zh: '疾病知识来源发现现会结合本体标签、本地来源名称与编码、配置别名及经审核官方入口页。',
          },
          {
            en: 'Redesigned the download interface around time-range cards, format choices, file sizes, source details, and a centered bilingual modal.',
            zh: '重新设计下载界面，通过时间范围卡片展示格式选择、文件大小和来源详情，并提供居中的双语弹窗。',
          },
          {
            en: 'Moved public data publishing to a dedicated repository with incremental synchronization and validation before the site is deployed.',
            zh: '将公开数据发布迁移到独立数据仓库，支持增量同步，并在站点部署前完成验证。',
          },
          {
            en: 'Separated normal Astro builds from data regeneration so interface-only builds no longer rewrite generated datasets.',
            zh: '将普通 Astro 构建与数据重新生成分离，纯界面构建不再重写已生成数据。',
          },
          {
            en: 'Simplified public-facing version and data copy, made the maintenance notice opt-in, and improved translated form placeholders.',
            zh: '简化面向公众的版本与数据文案，将维护提示改为按需显示，并改进表单占位文字的双语切换。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Prevented duplicate or missing points when the same surveillance week is represented by different dates, such as Saturday and Sunday.',
            zh: '修复同一监测周使用不同日期（如周六与周日）表示时可能出现的重复或缺失数据点。',
          },
          {
            en: 'Preserved legacy deaths, recoveries, mortality rates, and coverage gaps without reintroducing duplicate case counts.',
            zh: '在不重复计算病例数的前提下，保留旧版死亡、康复、病死率与覆盖缺口数据。',
          },
          {
            en: 'Prevented incidence calculations from losing population denominators when country identifiers are recreated during rebuilds.',
            zh: '修复重建过程中国家标识重新生成后，发病率计算可能丢失人口分母的问题。',
          },
          {
            en: 'Ensured CSV and JSON actions trigger browser downloads instead of unexpectedly opening raw files in a new tab, with a direct-link fallback.',
            zh: '确保 CSV 和 JSON 操作触发浏览器下载，而不是意外在新标签页打开原始文件，并保留直接链接回退。',
          },
          {
            en: 'Prevented a site deployment from publishing links to data files that failed repository synchronization or integrity checks.',
            zh: '防止站点在数据文件仓库同步失败或完整性校验未通过时发布无效下载链接。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.3',
    date: '2026-08-05',
    titleEn: 'Resilient archives and React 19 readiness',
    titleZh: '更可靠的数据归档与 React 19 兼容',
    summaryEn:
      'This release adds recoverable raw-data archives, completes the public site’s modern frontend migration, and separates core pipelines into smaller, testable modules.',
    summaryZh:
      '本次更新新增可恢复的原始数据归档，完成公开站点的现代前端迁移，并将核心流程拆分为更小、可测试的模块。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added automated raw-data archiving with content-addressed compressed objects, immutable snapshot manifests, and point-in-time restoration.',
            zh: '新增原始数据自动归档，支持内容寻址压缩对象、不可变快照清单与按时点恢复。',
          },
          {
            en: 'Added dedicated validation and tests for archive integrity, interrupted uploads, and first-time publishing.',
            zh: '新增归档完整性、上传中断续传与首次发布的专项验证和测试。',
          },
          {
            en: 'Added focused test suites for site-data generation, country crawl pipelines, agent workflows, and database rebuild planning.',
            zh: '新增站点数据生成、国家采集流程、Agent 工作流与数据库重建计划的专项测试。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Completed the public site migration to React 19, Tailwind CSS 4, and Marked 18 while retaining Astro 7 and ECharts 6.1.',
            zh: '完成公开站点向 React 19、Tailwind CSS 4 与 Marked 18 的迁移，并继续使用 Astro 7 和 ECharts 6.1。',
          },
          {
            en: 'Separated static site exports into query, view-building, file-writing, and series-projection layers for safer maintenance.',
            zh: '将静态站点导出拆分为查询、视图构建、文件写入和时间序列投影层，降低维护风险。',
          },
          {
            en: 'Modularized country crawling, agent workflow helpers, subscription email handling, and database rebuild planning.',
            zh: '对国家数据采集、Agent 工作流辅助逻辑、订阅邮件处理与数据库重建计划进行模块化。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Normalized the ECharts React module export to prevent invalid component errors under React 19 and different Vite bundling paths.',
            zh: '统一 ECharts React 模块导出形式，避免在 React 19 及不同 Vite 打包路径下出现无效组件错误。',
          },
          {
            en: 'Hardened archive resume behavior, chunk verification, and restore paths against incomplete uploads and unsafe symbolic links.',
            zh: '加强归档断点续传、分块校验与恢复路径安全，防止不完整上传和不安全符号链接。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.2',
    date: '2026-08-05',
    titleEn: 'A modernized and reproducible platform',
    titleZh: '现代化且更易复现的平台基础',
    summaryEn:
      'This release modernizes the public site and management dashboard, while making application dependencies more predictable, secure, and easier to maintain.',
    summaryZh:
      '本次更新对公开站点与管理后台进行技术栈升级，同时让应用依赖更可预期、更安全且更易维护。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added an automated Astro type-checking command to catch page and component issues before release.',
            zh: '新增 Astro 类型检查命令，在发布前发现页面与组件问题。',
          },
          {
            en: 'Introduced a direct Python dependency manifest and a fully pinned generated lock file for reproducible environments.',
            zh: '新增 Python 直接依赖清单与完整锁定的生成文件，便于稳定复现运行环境。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Upgraded the public site to Astro 7, the latest React integration, ECharts 6.1, and modern Tailwind/PostCSS configuration.',
            zh: '公开站点升级至 Astro 7、新版 React 集成、ECharts 6.1 与现代化 Tailwind/PostCSS 配置。',
          },
          {
            en: 'Upgraded the management dashboard to Next.js 16.3 and React 19.2.8.',
            zh: '管理后台升级至 Next.js 16.3 与 React 19.2.8。',
          },
          {
            en: 'Replaced the external Tremor dependency with lightweight local UI primitives while preserving cards, badges, grids, buttons, progress indicators, and dark mode.',
            zh: '使用轻量本地 UI 组件替代外部 Tremor 依赖，并保留卡片、标签、网格、按钮、进度显示和深色模式。',
          },
          {
            en: 'Updated and pinned the Python service stack, including current web, data-processing, AI, crawling, and testing libraries.',
            zh: '更新并锁定 Python 服务依赖，覆盖 Web、数据处理、AI、数据采集与测试工具。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened country and report routes when a country code is missing during static generation.',
            zh: '加强静态生成时国家代码缺失情况下的国家与报告路由处理。',
          },
          {
            en: 'Aligned download metadata types with the generated data format and hardened transitive HTTP dependency resolution.',
            zh: '将下载元数据类型与生成数据格式对齐，并加强间接 HTTP 依赖的版本约束。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.1',
    date: '2026-08-04',
    titleEn: 'Corrected Korea surveillance timelines',
    titleZh: '修正韩国疾病监测时间序列',
    summaryEn:
      'This patch corrects Korea KDCA monthly surveillance data and adds safeguards against shifted columns, incomplete batches, and false January spikes.',
    summaryZh:
      '本次修复更正韩国 KDCA 月度监测数据，并增加字段错位、不完整批次和虚假一月高峰的质量保护。',
    sections: [
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Corrected the KDCA EDW field mapping: COLUMN1 is the annual or year-to-date total, while COLUMN2–COLUMN13 represent January through December.',
            zh: '修正 KDCA EDW 字段映射：COLUMN1 为全年或年内累计值，COLUMN2–COLUMN13 才对应一月至十二月。',
          },
          {
            en: 'Rebuilt Korea monthly history from the official source for January 2001 through August 2026, removing the systematic false January peaks.',
            zh: '依据官方来源重建 2001 年 1 月至 2026 年 8 月的韩国月度历史数据，消除系统性的虚假一月高峰。',
          },
          {
            en: 'Added mappings for the current aggregate syphilis and Nipah virus infection categories reported by KDCA.',
            zh: '补充 KDCA 当前上报的梅毒汇总类别和尼帕病毒感染症映射。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Hardened Korea imports with support for named month fields, generic monthly columns, and packed DATAARRTXT values.',
            zh: '增强韩国数据导入，兼容命名月份字段、通用月度列及 DATAARRTXT 压缩值。',
          },
          {
            en: 'Added validation for all-zero batches, missing completed months, annual totals misread as January, and negative missing-value sentinels.',
            zh: '新增全零批次、已完成月份缺失、全年合计误判为一月以及负数缺失哨兵值检查。',
          },
        ],
      },
    ],
  },
  {
    version: '0.4.0',
    date: '2026-08-04',
    titleEn: 'A stronger disease knowledge foundation',
    titleZh: '更完善的疾病知识体系',
    summaryEn:
      'This release strengthens the data and knowledge layers behind GIDS, making disease profiles more consistent, traceable, and ready for broader country coverage.',
    summaryZh:
      '本次更新重点加强 GIDS 的数据与知识底层，让疾病档案更加统一、可追溯，并为覆盖更多国家和地区做好准备。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced a structured disease ontology and evidence-aware knowledge profiles.',
            zh: '引入结构化疾病本体与可追溯证据的疾病知识档案。',
          },
          {
            en: 'Added quality checks for disease mappings, legacy access, and series observations.',
            zh: '新增疾病映射、旧版数据访问和序列观测数据的质量检查。',
          },
          {
            en: 'Expanded automated surveillance preparation for more countries and source formats.',
            zh: '扩展多国家、多数据源格式的自动监测准备流程。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Moved site data generation toward a series-first model for more reliable comparisons.',
            zh: '站点数据生成逐步采用序列优先模型，提升跨地区比较的可靠性。',
          },
          {
            en: 'Refined disease knowledge rendering and test coverage across the public site.',
            zh: '优化公开站点的疾病知识展示，并补充相关测试覆盖。',
          },
        ],
      },
    ],
  },
  {
    version: '0.3.2',
    date: '2026-07-30',
    titleEn: 'More dependable charts and releases',
    titleZh: '更稳定的图表与发布流程',
    summaryEn:
      'Chart comparisons now behave more consistently, with stronger safeguards around automated production releases.',
    summaryZh: '图表比较体验更加稳定，同时加强了自动化生产发布的保护与验证。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Stabilized epidemic curve and monthly comparison state across country reports.',
            zh: '优化国家报告中的流行曲线与月度比较状态管理。',
          },
          {
            en: 'Added regression tests for chart models and monthly chart options.',
            zh: '新增图表模型与月度图表配置的回归测试。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened production publishing and post-release build verification.',
            zh: '加强生产发布及发布后构建验证，降低发布中断风险。',
          },
        ],
      },
    ],
  },
  {
    version: '0.3.1',
    date: '2026-05-24',
    titleEn: 'Subscriptions and service controls',
    titleZh: '订阅服务与管理能力',
    summaryEn:
      'Readers can subscribe to the updates they care about, with clearer service terms and protected management tools.',
    summaryZh: '读者可以订阅自己关注的更新，同时新增更清晰的服务条款和受保护的管理能力。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added email subscriptions with language, frequency, country, and disease preferences.',
            zh: '新增邮件订阅，可选择语言、频率、国家和疾病偏好。',
          },
          {
            en: 'Added subscription confirmation, secure unsubscribe links, and delivery status feedback.',
            zh: '新增订阅确认、安全退订链接和邮件投递状态反馈。',
          },
          {
            en: 'Published bilingual service terms and privacy information.',
            zh: '发布中英双语服务条款与隐私说明。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Added protected subscription management and administrative notification workflows.',
            zh: '补充受保护的订阅管理与管理员通知流程。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.6',
    date: '2026-04-23',
    titleEn: 'A clearer view of how GIDS works',
    titleZh: '更清晰地了解 GIDS 如何运作',
    summaryEn:
      'The public site now explains its live data snapshot, processing pipeline, architecture, and source coverage in one place.',
    summaryZh: '公开站点集中展示实时数据快照、处理流程、系统架构和数据来源覆盖情况。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added a comprehensive About page with live database metrics.',
            zh: '新增完整的“关于”页面，并展示实时数据库指标。',
          },
          {
            en: 'Documented the collection pipeline, system architecture, features, and official sources.',
            zh: '展示数据采集流程、系统架构、主要功能和官方数据来源。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.5',
    date: '2026-04-10',
    titleEn: 'A cleaner project foundation',
    titleZh: '更清晰的项目基础',
    summaryEn:
      'Internal structures were simplified to make the site easier to maintain and extend without changing its core experience.',
    summaryZh: '简化内部结构，让站点更易维护和扩展，同时保持核心使用体验不变。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Refactored site code for clearer responsibilities and easier maintenance.',
            zh: '重构站点代码，明确模块职责并降低维护成本。',
          },
          {
            en: 'Improved data handling consistency across country and disease pages.',
            zh: '提升国家与疾病页面的数据处理一致性。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.4',
    date: '2026-04-06',
    titleEn: 'A refreshed visual system',
    titleZh: '焕新的视觉系统',
    summaryEn:
      'GIDS received a more coherent visual language for navigation, content cards, reports, and data-heavy pages.',
    summaryZh: 'GIDS 更新统一的视觉语言，覆盖导航、内容卡片、报告与数据密集型页面。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Refreshed the public site design and responsive layouts.',
            zh: '更新公开站点设计与响应式布局。',
          },
          {
            en: 'Improved readability across charts, reports, and long-form disease content.',
            zh: '提升图表、报告和疾病长文内容的阅读体验。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.3',
    date: '2026-04-06',
    titleEn: 'Email delivery support',
    titleZh: '邮件投递支持',
    summaryEn:
      'The notification system gained email delivery support and clearer routes for operational update messages.',
    summaryZh: '通知系统新增邮件投递支持，并完善运行更新消息的发送方式。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Added SMTP-based email delivery for automated notifications.',
            zh: '新增基于 SMTP 的自动通知邮件投递。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        items: [
          {
            en: 'Improved routing for data update error notifications.',
            zh: '优化数据更新错误通知的发送方式。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.2',
    date: '2026-03-29',
    titleEn: 'Responsive data exploration',
    titleZh: '响应式数据浏览体验',
    summaryEn:
      'Country and disease data became easier to explore across screen sizes, with more consistent underlying structures.',
    summaryZh: '国家与疾病数据在不同屏幕上更易浏览，底层结构也更加一致。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        items: [
          {
            en: 'Improved chart responsiveness and presentation on smaller screens.',
            zh: '优化图表在小屏设备上的响应式布局与展示。',
          },
          {
            en: 'Standardized disease and country data handling across the static site.',
            zh: '统一静态站点中的疾病与国家数据处理方式。',
          },
          {
            en: 'Added structured metadata, robots directives, and generated sitemaps for discovery.',
            zh: '新增结构化元数据、搜索引擎指令和自动生成的网站地图。',
          },
        ],
      },
    ],
  },
  {
    version: '0.2.0',
    date: '2026-03-29',
    titleEn: 'Publication-ready charts',
    titleZh: '适合发布的图表能力',
    summaryEn:
      'A new chart frame made it easier to switch between visual and tabular views while keeping report context visible.',
    summaryZh: '新的图表框架支持图形与表格视图切换，并保留完整的报告上下文。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced a reusable chart frame with chart and table presentation modes.',
            zh: '新增可复用图表框架，支持图表与表格两种展示模式。',
          },
        ],
      },
    ],
  },
  {
    version: '0.1.0',
    date: '2026-03-16',
    titleEn: 'The first public GIDS site',
    titleZh: 'GIDS 公开站点首个版本',
    summaryEn:
      'The first version established the public home for country surveillance, disease profiles, reports, and bilingual data exploration.',
    summaryZh: '首个版本建立公开站点，提供国家监测、疾病档案、报告和中英双语数据浏览。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        items: [
          {
            en: 'Launched the Astro-based public site with country and disease pages.',
            zh: '上线基于 Astro 的公开站点、国家页面与疾病页面。',
          },
          {
            en: 'Added generated epidemiological reports and interactive data visualizations.',
            zh: '新增自动生成的流行病学报告与交互式数据可视化。',
          },
          {
            en: 'Added bilingual content, local fonts, and automated site data generation.',
            zh: '新增中英双语内容、本地字体和自动站点数据生成。',
          },
        ],
      },
    ],
  },
];
