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
