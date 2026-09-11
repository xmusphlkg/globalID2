export type ChangeKind = 'new' | 'improved' | 'fixed';

export type ChangelogSection = {
  kind: ChangeKind;
  labelEn: string;
  labelZh: string;
  labelFr?: string;
  items: Array<{
    en: string;
    zh: string;
    fr?: string;
  }>;
};

export type ChangelogRelease = {
  version: string;
  date: string;
  titleEn: string;
  titleZh: string;
  titleFr?: string;
  summaryEn: string;
  summaryZh: string;
  summaryFr?: string;
  sections: ChangelogSection[];
};

export const changelogReleases: ChangelogRelease[] = [
  {
    version: '0.10.1',
    date: '2026-09-11',
    titleEn: 'French interface and internationalized navigation',
    titleZh: '法语界面与国际化导航',
    titleFr: 'Interface française et navigation internationalisée',
    summaryEn:
      'GIDS now offers a French interface for users in France, Canada, and French-speaking communities, with localized navigation, search, reporting controls, formatting, and page metadata.',
    summaryZh:
      'GIDS 现为法国、加拿大及其他法语用户提供完整法语界面，涵盖导航、搜索、报告控件、格式化和页面元数据。',
    summaryFr:
      'GIDS propose désormais une interface française pour les utilisateurs en France, au Canada et dans les communautés francophones, avec navigation, recherche, contrôles de rapports, formats et métadonnées localisés.',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelZh: '新增',
        labelFr: 'Nouveau',
        items: [
          {
            en: 'Added French routes under /fr/ across the public home, country, disease, report, research, situation, download, and policy pages.',
            zh: '新增 /fr/ 法语路由，覆盖首页、国家、疾病、报告、研究、态势、下载和政策页面。',
            fr: 'Ajout de routes françaises sous /fr/ pour l’accueil, les pays, les maladies, les rapports, la recherche, la situation, les téléchargements et les pages de référence.',
          },
          {
            en: 'Added French translations for shared navigation, search, statistics, chart metrics, categories, accessibility labels, and release notes.',
            zh: '为共享导航、搜索、统计、图表指标、分类、无障碍标签和版本记录新增法语翻译。',
            fr: 'Ajout de traductions françaises pour la navigation, la recherche, les statistiques, les indicateurs des graphiques, les catégories, l’accessibilité et les notes de version.',
          },
          {
            en: 'Added a synchronized French disease-name catalogue for directories, cards, search, research views, and epidemiological charts.',
            zh: '新增同步的法语疾病名称目录，覆盖疾病目录、卡片、搜索、研究视图和流行病学图表。',
            fr: 'Ajout d’un catalogue synchronisé des noms de maladies en français pour les répertoires, fiches, recherches, vues de recherche et graphiques épidémiologiques.',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelZh: '优化',
        labelFr: 'Amélioré',
        items: [
          {
            en: 'Language switching now cycles through English, French, and Chinese while preserving the current page and query string.',
            zh: '语言切换现在支持英文、法文和中文，并保留当前页面与查询参数。',
            fr: 'Le sélecteur de langue passe désormais entre l’anglais, le français et le chinois en conservant la page et les paramètres de recherche.',
          },
          {
            en: 'French pages use fr-FR number and date formatting and expose fr-FR document, Open Graph, and structured-data metadata.',
            zh: '法语页面使用 fr-FR 数字和日期格式，并输出 fr-FR 文档、Open Graph 和结构化数据元信息。',
            fr: 'Les pages françaises utilisent les formats de nombres et de dates fr-FR et exposent des métadonnées de document, Open Graph et données structurées en fr-FR.',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelZh: '修复',
        labelFr: 'Corrigé',
        items: [
          {
            en: 'Fixed localized links from charts, country cards, reports, research widgets, breadcrumbs, and the footer so French users remain in the French experience.',
            zh: '修复图表、国家卡片、报告、研究组件、面包屑和页脚中的本地化链接，确保法语用户始终留在法语体验中。',
            fr: 'Correction des liens localisés des graphiques, fiches pays, rapports, modules de recherche, fils d’Ariane et pied de page afin de rester dans l’expérience française.',
          },
          {
            en: 'Added safe English fallback behavior for legacy records that do not yet contain French content.',
            zh: '为尚未包含法语内容的历史记录增加安全的英文回退机制。',
            fr: 'Ajout d’un repli anglais sûr pour les enregistrements historiques qui ne disposent pas encore d’un contenu français.',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.7',
    date: '2026-09-11',
    titleEn: 'Interactive coverage map and refined release sync',
    titleFr: 'Carte de couverture interactive et synchronisation de version affinée',
    titleZh: '互动覆盖地图与发布同步优化',
    summaryEn:
      'GIDS now renders a bolder coverage map for countries and provinces, adds richer map interactions, and keeps weekly literature review records aligned with the latest release state.',
    summaryFr: 'GIDS rend désormais une carte de couverture plus audacieuse pour les pays et les provinces, ajoute des interactions cartographiques plus riches et conserve des enregistrements hebdomadaires d\'examen de la littérature alignés sur l\'état de publication le plus récent.',
    summaryZh:
      'GIDS 现在使用更直观的覆盖地图同时展示国家和省级覆盖，并增强地图交互体验；周报文献人工审核记录与最新发布状态同步。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Implemented a redesigned coverage map with country and subdivision markers, status legends, and interactive hover interactions.',
            fr: 'Mise en œuvre d\'une carte de couverture repensée avec des marqueurs de pays et de subdivision, des légendes de statut et des interactions interactives au survol.',
            zh: '重新实现覆盖地图，支持国家和地区点位、状态图例，以及悬停交互。',
          },
          {
            en: 'Map rendering now uses local GeoJSON and the bundled ISO country catalogue, avoiding dependency on third-party tile services.',
            fr: 'Le rendu cartographique utilise désormais GeoJSON local et le catalogue de pays ISO groupé, évitant ainsi de dépendre de services de tuiles tiers.',
            zh: '地图渲染改用本地 GeoJSON 与内置 ISO 国家目录，不再依赖第三方地图服务。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'The countries page now shows a bilingual coverage summary, clearer map controls, and synchronized map filtering with list filtering states.',
            fr: 'La page des pays affiche désormais un résumé de la couverture bilingue, des contrôles de carte plus clairs et un filtrage de carte synchronisé avec les états de filtrage de liste.',
            zh: '国家页现在包含中英双语覆盖摘要、可视化地图控制项，并将列表筛选与地图联动同步。',
          },
          {
            en: 'Geolocation for markers now combines catalogue names, coverage status, and fallback geometry points for more stable map behavior on small or disputed geographies.',
            fr: 'La géolocalisation des marqueurs combine désormais les noms de catalogue, l\'état de la couverture et les points de géométrie de repli pour un comportement cartographique plus stable sur des zones géographiques petites ou contestées.',
            zh: '地图点位定位结合国家目录名、覆盖状态与回退几何点，提升小区域与争议区域下的展示稳定性。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Resolved inconsistent map overlay updates when clearing filters or switching between supported and planned views.',
            fr: 'Résolution des mises à jour incohérentes de la superposition de cartes lors de l\'effacement des filtres ou du basculement entre les vues prises en charge et planifiées.',
            zh: '修复清空筛选或切换到支持/规划视图时，地图标注层更新不一致的问题。',
          },
          {
            en: 'Updated weekly literature review entries for 2026-W36 and 2026-W37 with refreshed fingerprints and review timestamps.',
            fr: 'Mise à jour des entrées de revue de littérature hebdomadaire pour 2026-S36 et 2026-S37 avec des empreintes digitales actualisées et des horodatages de révision.',
            zh: '更新 2026-W36 与 2026-W37 的周更文献审核记录，补齐新的指纹与审核时间。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.6',
    date: '2026-09-10',
    titleEn: 'Surveillance context and resilient research operations',
    titleFr: 'Contexte de surveillance et opérations de recherche résilientes',
    titleZh: '监测背景与更稳健的研究运营',
    summaryEn:
      'GIDS now connects Research Ask evidence to published surveillance curves, exposes real Model Center route health, and keeps literature and release automation moving through safer retries and recovery controls.',
    summaryFr: 'GIDS RELIE désormais les preuves de Research Ask aux courbes de surveillance publiées, expose la santé réelle de l\'itinéraire du Model Center et permet à la littérature et à l\'automatisation des versions de progresser grâce à des tentatives plus sûres et à des contrôles de récupération.',
    summaryZh:
      'GIDS 现在将研究问答证据连接到已发布的监测曲线，展示模型中心真实路由健康，并通过更安全的重试与恢复控制让文献和发布自动化持续运行。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Research Ask can now show a matched GIDS reported-case curve for the disease and locations found in a published evidence query.',
            fr: 'Research Ask peut maintenant afficher une courbe de cas rapportés GIDS appariée pour la maladie et les emplacements trouvés dans une requête de preuve publiée.',
            zh: '研究问答现在可以针对已发布证据查询中匹配到的疾病和地区，展示对应的 GIDS 报告病例曲线。',
          },
          {
            en: 'The control panel now exposes actual model-route status, recovery state, provider capacity, latency, and failure history.',
            fr: 'Le panneau de commande expose maintenant l\'état réel de la route du modèle, l\'état de récupération, la capacité du fournisseur, la latence et l\'historique des défaillances.',
            zh: '控制面板现在展示模型路由实际状态、恢复状态、提供商容量、延迟和失败历史。',
          },
          {
            en: 'Australia monthly ingestion now labels open provisional months and authoritative revisions explicitly, with completeness-based archive recovery.',
            fr: 'L\'ingestion mensuelle en Australie étiquette désormais explicitement les mois provisoires ouverts et les révisions faisant autorité, avec une récupération d\'archives basée sur l\'exhaustivité.',
            zh: '澳大利亚月度抓取现在明确标记开放暂定月份和权威修订，并在实时结果不完整时按完整度从归档恢复。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Research Radar persistence now uses stable ordering, bounded batches, and deadlock-only retries to keep concurrent collection reliable.',
            fr: 'Research Radar persistence utilise désormais des commandes stables, des lots bornés et des tentatives d\'impasse uniquement pour garantir la fiabilité de la collecte simultanée.',
            zh: '研究雷达持久化现在使用稳定排序、受限批次和仅针对死锁的重试，提升并发采集可靠性。',
          },
          {
            en: 'Model Center routes recover from stale failures through decaying streaks and structured workload probes, while provider and model admission stay independently tuned.',
            fr: 'Les itinéraires du centre de modèles se remettent des défaillances obsolètes grâce à des stries décroissantes et à des sondes de charge de travail structurées, tandis que l\'admission du fournisseur et du modèle reste réglée indépendamment.',
            zh: '模型中心路由会通过失败计数衰减和结构化负载探测从陈旧故障中恢复，同时独立调节提供商与模型准入。',
          },
          {
            en: 'Research Ask adds bilingual surveillance links, curve context, and clearer result metrics without sending the query to a model.',
            fr: 'Research Ask ajoute des liens de surveillance bilingues, un contexte de courbe et des mesures de résultats plus claires sans envoyer la requête à un modèle.',
            zh: '研究问答新增双语监测链接、曲线上下文和更清晰的结果指标，查询仍不会发送给模型。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Unavailable model channels now cool down before retrying, instead of consuming every literature-summary attempt.',
            fr: 'Les canaux de modèle indisponibles se refroidissent maintenant avant de réessayer, au lieu de consommer toutes les tentatives récapitulatives de la littérature.',
            zh: '不可用模型通道现在会先进入冷却，再继续重试，不再消耗文献摘要的全部尝试次数。',
          },
          {
            en: 'Messy fenced or prefixed JSON responses are recovered when they contain a valid literature object.',
            fr: 'Les réponses JSON clôturées ou préfixées désordonnées sont récupérées lorsqu\'elles contiennent un objet de littérature valide.',
            zh: '当带有围栏或前后杂文的模型响应包含有效文献对象时，现在可以恢复解析。',
          },
          {
            en: 'SMTP authentication failures now use a short cooldown, and SEO checks safely handle quoted metadata.',
            fr: 'Les échecs d\'authentification SMTP utilisent maintenant un temps de recharge court, et les contrôles SEO gèrent en toute sécurité les métadonnées citées.',
            zh: 'SMTP 认证失败现在使用短暂冷却，SEO 检查也能安全处理带引号的元数据。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.5',
    date: '2026-09-05',
    titleEn: 'Research Radar publication-boundary repair',
    titleFr: 'Recherche Radar publication-réparation des limites',
    titleZh: '研究雷达公开边界修复',
    summaryEn:
      'GIDS now revalidates autopilot-published Research Radar records against hard public-boundary rules, keeps model summaries flowing when a field is returned as prose, and avoids slow-route/export-memory stalls during publication.',
    summaryFr: 'GIDS REVALIDE désormais les enregistrements de radar de recherche publiés en pilote automatique par rapport aux règles strictes de délimitation publique, maintient les résumés de modèles fluides lorsqu\'un champ est renvoyé en prose et évite les blocages de mémoire de lenteur de route/d\'exportation pendant la publication.',
    summaryZh:
      'GIDS 现在会用硬性公开边界规则重新校验自动发布的研究雷达记录，在模型把字段返回为普通文本时继续保留摘要结果，并避免慢模型路由与导出内存压力拖住发布。',
    sections: [
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Autopilot-managed articles with animal-only, basic-research, or plant-only classification evidence can no longer remain in the public Research Radar catalogue.',
            fr: 'Les articles gérés par pilote automatique avec des preuves de classification réservées aux animaux, à la recherche de base ou aux plantes ne peuvent plus rester dans le catalogue public de Research Radar.',
            zh: '带有动物实验、基础研究或植物研究分类证据的自动发布文章不再能停留在公开研究雷达目录中。',
          },
          {
            en: 'Automatically published summaries now return to review or archive when their parent article is removed from the public boundary.',
            fr: 'Les résumés publiés automatiquement retournent maintenant à la révision ou à l\'archivage lorsque leur article parent est supprimé de la limite publique.',
            zh: '当父文章离开公开边界时，自动发布的摘要现在会回到待审或归档状态。',
          },
          {
            en: 'Summary enrichment now accepts scalar/list model fields as conservative evidence objects, reducing avoidable failures from otherwise usable model responses.',
            fr: 'L\'enrichissement sommaire accepte désormais les champs de modèle scalaire/de liste en tant qu\'objets de preuve conservateurs, réduisant ainsi les échecs évitables des réponses de modèle autrement utilisables.',
            zh: '摘要补全现在会把模型返回的文本或列表字段保守转换为证据对象，减少可用回答因结构轻微偏差而失败。',
          },
          {
            en: 'Chronically failing Model Center routes are no longer used as active summary candidates until a structured health check proves recovery.',
            fr: 'Les itinéraires chroniquement défaillants du Model Center ne sont plus utilisés comme candidats récapitulatifs actifs jusqu\'à ce qu\'un bilan de santé structuré prouve la récupération.',
            zh: '连续生产调用失败的模型中心路由不再进入摘要 active 候选，直到结构化健康检查证明已恢复。',
          },
          {
            en: 'Provider 403/no-access responses, including Chinese no-access messages, now mark the affected model route unavailable instead of retrying it for every summary.',
            fr: 'Les réponses du fournisseur 403/sans accès, y compris les messages chinois sans accès, marquent désormais l\'itinéraire du modèle affecté comme indisponible au lieu de le réessayer pour chaque résumé.',
            zh: 'Provider 返回 403/无权限（包括中文无权访问提示）时，现在会把对应模型路由标记为不可用，不再让每篇摘要重复踩同一个失败路由。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'The repaired Research Radar export passes public-release validation again, with 964 public articles and 334 weekly briefs after recovery.',
            fr: 'L\'exportation de Research Radar réparée passe à nouveau la validation de publication publique, avec 964 articles publics et 334 briefings hebdomadaires après récupération.',
            zh: '修复后的研究雷达导出已重新通过公开发布校验，恢复后包含 964 篇公开文章和 334 份周报。',
          },
          {
            en: 'Site-data export now scales partitioned download workers to the service memory guardrail, preventing releases from stalling under cgroup memory pressure.',
            fr: 'L\'exportation de données de site fait maintenant évoluer les travailleurs de téléchargement partitionnés vers le garde-corps de la mémoire de service, empêchant les rejets de stagner sous la pression de la mémoire cgroup.',
            zh: '站点数据导出现在会按服务内存水位收敛分区下载 worker，避免发布在 cgroup 内存压力下停住。',
          },
          {
            en: 'The dashboard worker now keeps site releases from overlapping with memory-heavy Research Radar and AI jobs.',
            fr: 'Le travailleur du tableau de bord empêche désormais les versions du site de se chevaucher avec les tâches de radar de recherche et d\'IA gourmandes en mémoire.',
            zh: '控制面 worker 现在会让站点发布与高内存的研究雷达/AI 任务错峰运行。',
          },
          {
            en: 'Queued site releases now hold off new Research Radar catch-up work so publication is not starved by continuous summary batches.',
            fr: 'Les versions de sites en file d\'attente retardent maintenant le nouveau travail de rattrapage de Research Radar afin que la publication ne soit pas affamée par des lots récapitulatifs continus.',
            zh: '站点发布排队后会暂停领取新的研究雷达 catch-up 任务，避免发布被连续摘要批次饿住。',
          },
          {
            en: 'Release commands now drain verbose Astro/Node output by byte chunks so builds cannot stall on a full stdout pipe.',
            fr: 'Les commandes de libération drainent maintenant la sortie verbeuse Astro/Node par blocs d\'octets afin que les builds ne puissent pas décrocher sur un tuyau stdout complet.',
            zh: '发布命令现在按字节块消费 Astro/Node 的详细输出，避免构建因 stdout 管道写满而停住。',
          },
        ],
      },
    ],
  },
  {
    version: '0.9.4',
    date: '2026-09-04',
    titleEn: 'Autonomous knowledge repair, PubMed discovery, and richer curve comparison',
    titleFr: 'Réparation autonome des connaissances, découverte de PubMed et comparaison de courbes plus riche',
    titleZh: '知识库自动修复、PubMed 发现与更丰富的曲线比较',
    summaryEn:
      'GIDS now keeps source-first knowledge repairs moving through the control plane, adds PubMed as a bounded Research Radar source, exposes Model Center runtime health, and gives public disease curves safer comparison tools.',
    summaryFr: 'GIDS conserve désormais les réparations de connaissances d\'abord à la source dans le plan de contrôle, ajoute PubMed en tant que source de radar de recherche bornée, expose la santé de l\'exécution du Centre de modèles et donne aux courbes de maladies publiques des outils de comparaison plus sûrs.',
    summaryZh:
      'GIDS 现在通过控制面维持来源优先的知识库修复队列，新增 PubMed 作为受限的研究雷达来源，展示模型中心运行健康，并为公开疾病曲线提供更安全的比较工具。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added a disease-knowledge autopilot that prioritizes real evidence gaps, refreshes targeted sources first, and queues model repair only after the evidence packet is ready.',
            fr: 'Ajout d\'un pilote automatique de connaissance de la maladie qui donne la priorité aux lacunes réelles en matière de preuves, actualise d\'abord les sources ciblées et ne met en file d\'attente la réparation du modèle qu\'une fois que le paquet de preuves est prêt.',
            zh: '新增疾病知识库自动驾驶流程，优先处理真实证据缺口，先刷新定向来源，并且只在证据包就绪后调度模型修复。',
          },
          {
            en: 'Added PubMed E-utilities for bounded biomedical discovery and Crossref fallback in Research Radar.',
            fr: 'Ajout de PubMed E-utilities pour la découverte biomédicale bornée et la solution de repli Crossref dans Research Radar.',
            zh: '新增 PubMed E-utilities，用于研究雷达中的受限生物医学发现和 Crossref 临时不可用时的回退。',
          },
          {
            en: 'Added public curve controls for seasonal anomaly comparison, complete-calendar-year totals, quick date ranges, selected-only filtering, and sortable entity lists.',
            fr: 'Ajout de contrôles de courbe publique pour la comparaison des anomalies saisonnières, les totaux des années civiles complètes, les plages de dates rapides, le filtrage sélectionné uniquement et les listes d\'entités triables.',
            zh: '新增公开曲线控件，支持同期异常比较、完整自然年总量、快捷时间范围、仅看已选对象和实体排序。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Model governance now uses the shared Model Center path, including runtime health, route admission, failover, and provider/model catalogue discovery.',
            fr: 'La gouvernance des modèles utilise désormais le chemin partagé du Centre de modèles, y compris l\'intégrité de l\'exécution, l\'admission des itinéraires, le basculement et la découverte du catalogue fournisseur/modèle.',
            zh: '模型治理现在使用共享模型中心路径，包含运行健康、路由准入、失败切换以及 provider/model 目录发现。',
          },
          {
            en: 'Knowledge profiles now show an automating state while required evidence is still being enriched instead of sending unsupported content to manual review.',
            fr: 'Les profils de connaissances montrent maintenant un état d\'automatisation alors que les preuves requises sont toujours en cours d\'enrichissement au lieu d\'envoyer du contenu non pris en charge à l\'examen manuel.',
            zh: '当必需证据仍在补全时，知识画像现在显示自动处理中状态，而不是把缺乏支撑的内容直接推给人工审核。',
          },
          {
            en: 'Research Radar summary enrichment now includes non-open-access records when a usable abstract is available and spreads concurrent requests across active Model Center route shards.',
            fr: 'L\'enrichissement du résumé de Research Radar inclut désormais des enregistrements non ouverts lorsqu\'un résumé utilisable est disponible et répartit les demandes simultanées sur les fragments de route actifs du Centre de modèles.',
            zh: '研究雷达摘要补全现在会纳入非开放获取但已有可用摘要的记录，并将并发请求分散到活跃的模型中心路由分片。',
          },
          {
            en: 'The Research evidence graph now loads relationships from a dedicated static JSON endpoint, keeping localized HTML pages inside performance budgets as the catalogue grows.',
            fr: 'Le graphique des preuves de recherche charge désormais les relations à partir d\'un point de terminaison JSON statique dédié, en gardant les pages HTML localisées dans les budgets de performance à mesure que le catalogue se développe.',
            zh: '研究证据图谱现在从专用静态 JSON 端点加载关系数据，随着目录增长也能让中英文 HTML 页面保持在性能预算内。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Prevented aggregate or non-public catalogue rows, stale source-gap metadata, adapter timeouts, and repeated overlay notes from destabilizing knowledge publication.',
            fr: 'Prévention des lignes de catalogue agrégées ou non publiques, des métadonnées d\'écart de source périmées, des délais d\'expiration des adaptateurs et des notes de superposition répétées de la publication de connaissances déstabilisantes.',
            zh: '防止聚合或非公开目录项、过期来源缺口元数据、适配器超时和重复覆盖说明影响知识发布稳定性。',
          },
          {
            en: 'Improved task-worker restart behavior with adaptive AI concurrency, per-disease knowledge serialization, model-recovery wakeups, and safer singleton lease handling.',
            fr: 'Amélioration du comportement de redémarrage du travailleur avec la simultanéité de l\'IA adaptative, la sérialisation des connaissances par maladie, les réveils de récupération de modèles et une gestion plus sûre des baux d\'un seul tonneau.',
            zh: '改进任务 worker 重启行为，加入自适应 AI 并发、按疾病串行化知识任务、模型恢复唤醒和更安全的单例租约处理。',
          },
          {
            en: 'Transient model connectivity failures no longer consume Research Radar summary quality-attempt budgets, keeping affected abstracts retryable.',
            fr: 'Les défaillances transitoires de la connectivité du modèle ne consomment plus les budgets récapitulatifs de qualité de Research Radar, ce qui permet de réessayer les résumés affectés.',
            zh: '临时模型连接失败不再消耗研究雷达摘要质量尝试次数，受影响摘要会继续保持可重试。',
          },
          {
            en: 'Research Radar public exports and Astro builds now share a release lock, preventing continuous summary catch-up from changing source data midway through a site build.',
            fr: 'Les exportations publiques de Research Radar et les constructions Astro partagent désormais un verrou de libération, empêchant le rattrapage récapitulatif continu de modifier les données source au milieu d\'une construction de site.',
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
    titleFr: 'Réparation des connaissances fondée sur des preuves et automatisation à la source',
    titleZh: '证据驱动的知识库修复与来源优先自动化',
    summaryEn:
      'GIDS now repairs only evidence-backed knowledge gaps, refreshes targeted sources before new model work, and preserves valid content through a controlled model-center recovery loop.',
    summaryFr: 'GIDS ne répare désormais que les lacunes en matière de connaissances étayées par des preuves, actualise les sources ciblées avant le travail sur un nouveau modèle et préserve le contenu valide grâce à une boucle de récupération contrôlée centrée sur le modèle.',
    summaryZh:
      'GIDS 现在只修复具备明确证据支持的知识库缺口，在新的模型任务前先刷新定向来源，并通过受控的模型中心恢复闭环保护已有有效内容。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added a bounded quality-repair prompt that returns failed fields, source-grounded evidence fragments, and the prior JSON to the model for one constrained repair pass.',
            fr: 'Ajout d\'une invite de réparation de qualité limitée qui renvoie les champs échoués, les fragments de preuves à la source et le JSON précédent au modèle pour une passe de réparation contrainte.',
            zh: '新增受限质量修复提示词，将失败字段、来源支撑的证据片段和上一轮 JSON 一并返回模型，进行一次受控修复。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'New knowledge repairs now refresh and assess targeted sources before scheduling model-center generation, reducing calls that cannot meet publication gates.',
            fr: 'Les nouvelles réparations de connaissances actualisent et évaluent désormais les sources ciblées avant de planifier la génération du centre de modèles, réduisant ainsi les appels qui ne peuvent pas respecter les barrières de publication.',
            zh: '新的知识库修复现在会先刷新并评估定向来源，再调度模型中心生成，减少无法通过发布门槛的调用。',
          },
          {
            en: 'Quality repair locks previously valid fields so the model can change only the rejected evidence-backed sections.',
            fr: 'La réparation de qualité verrouille les champs précédemment valides afin que le modèle puisse modifier uniquement les sections appuyées par des preuves rejetées.',
            zh: '质量修复会锁定此前有效的字段，使模型只能修改被拒绝且有证据支持的章节。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Unsupported fields now remain in review instead of triggering repeated model calls from incomplete evidence.',
            fr: 'Les champs non pris en charge restent maintenant en revue au lieu de déclencher des appels de modèle répétés à partir de preuves incomplètes.',
            zh: '缺乏支撑证据的字段现在会保持待审，不再基于不完整证据反复调用模型。',
          },
          {
            en: 'Worker restart now uses a clean drain-and-lease handoff before the replacement worker resumes recoverable queued work.',
            fr: 'Le redémarrage du travailleur utilise maintenant un transfert de vidange et de location propre avant que le travailleur de remplacement ne reprenne le travail en file d\'attente récupérable.',
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
    titleFr: 'Gouvernance du contenu de l\'IA et automatisation de la réparation des connaissances',
    titleZh: 'AI 内容治理与知识库自动修复',
    summaryEn:
      'GIDS now routes knowledge gaps, Research Radar review queues, source review, and learning suggestions through auditable AI governance loops instead of defaulting to manual intervention.',
    summaryFr: 'GIDS ACHEMINE désormais les lacunes en matière de connaissances, les files d\'attente d\'examen de Research Radar, l\'examen des sources et les suggestions d\'apprentissage via des boucles de gouvernance d\'IA vérifiables au lieu de recourir par défaut à une intervention manuelle.',
    summaryZh:
      'GIDS 现在将知识库缺口、研究雷达审核队列、来源审核和学习建议接入可审计的 AI 治理闭环，不再默认依赖人工干预。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added AI content governance with model-center JSON review prompts, confidence gates, audit metadata, and fallback-to-hold behavior.',
            fr: 'Ajout de la gouvernance du contenu de l\'IA avec des invites d\'examen JSON centrées sur le modèle, des portes de confiance, des métadonnées d\'audit et un comportement de repli.',
            zh: '新增 AI 内容治理服务，包含模型中心 JSON 审核提示词、置信度门槛、审计元数据以及失败时保持待定的保护行为。',
          },
          {
            en: 'Added control-plane endpoints for knowledge repair runs, failed knowledge retries, Research Radar article and summary review, knowledge-source review, and learning-suggestion mapping.',
            fr: 'Ajout de points de terminaison de plan de contrôle pour les cycles de réparation des connaissances, les nouvelles tentatives de connaissances infructueuses, l\'article et l\'examen sommaire de Research Radar, l\'examen des sources de connaissances et la cartographie des suggestions d\'apprentissage.',
            zh: '新增控制面接口，用于知识库修复运行、失败知识任务重试、研究雷达文章和摘要审核、知识来源审核以及学习建议映射。',
          },
          {
            en: 'Added AI-assisted Research Radar article review so high-confidence review-band articles can be published or excluded before summary gates run.',
            fr: 'Ajout de la revue d\'article Research Radar assistée par l\'IA afin que les articles de bande de révision à haute confiance puissent être publiés ou exclus avant que les portes de résumé ne s\'exécutent.',
            zh: '新增 AI 辅助的研究雷达文章审核，使高置信审核带文章可在摘要发布门槛前自动发布或排除。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Knowledge repair now supports language-targeted retry, allowing single-language failures to regenerate only the affected English or Chinese brief.',
            fr: 'La réparation des connaissances prend désormais en charge les nouvelles tentatives ciblées par langue, permettant aux échecs d\'une seule langue de régénérer uniquement le brief anglais ou chinois concerné.',
            zh: '知识库修复现在支持按语言定向重试，单语失败时只重新生成受影响的英文或中文摘要。',
          },
          {
            en: 'Learning suggestions can now resolve obvious placeholders and high-confidence standard disease mappings automatically.',
            fr: 'Les suggestions d\'apprentissage peuvent désormais résoudre automatiquement les espaces réservés évidents et les mappages de maladies standard à haute confiance.',
            zh: '学习建议现在可以自动处理明显占位项和高置信标准病种映射。',
          },
          {
            en: 'Long-running workers now emit less idle log noise and release Python and libc heap memory after task completion.',
            fr: 'Les travailleurs de longue date émettent maintenant moins de bruit de journal inactif et libèrent de la mémoire de tas Python et libc après l\'achèvement de la tâche.',
            zh: '长运行 worker 现在减少空闲日志噪音，并在任务完成后释放 Python 与 libc 堆内存。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Recoverable model-center and partial-language knowledge repair failures can be requeued automatically up to their retry limit.',
            fr: 'Les échecs de réparation des connaissances du centre de modèles récupérables et des langues partielles peuvent être automatiquement mis en file d\'attente jusqu\'à leur limite de réessai.',
            zh: '可恢复的模型中心错误和单语知识库发布门槛失败现在可在重试上限内自动回队列。',
          },
          {
            en: 'Release retry classification now distinguishes transient OpenSSL EOF preflight diagnostics from permanent Cloudflare production-branch configuration blockers.',
            fr: 'La classification de nouvelle tentative de publication distingue désormais les diagnostics de pré-vol transitoires OpenSSL EOF des bloqueurs permanents de configuration de branche de production Cloudflare.',
            zh: '发布重试分类现在可区分临时 OpenSSL EOF 预检诊断与永久性的 Cloudflare 生产分支配置阻断。',
          },
          {
            en: 'Dashboard standalone startup now prunes older retained releases after switching to the active build.',
            fr: 'Le démarrage autonome du tableau de bord élague maintenant les anciennes versions conservées après le passage à la version active.',
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
    titleFr: 'Site public localisé et opérations sensibles à la juridiction',
    titleZh: '公开站点本地化与辖区化运营',
    summaryEn:
      'GIDS now has first-class Chinese public routes, a refreshed static-site experience, jurisdiction-aware Control Center selection, and stronger source-series-first data handling across public APIs and exports.',
    summaryFr: 'GIDS DISPOSE désormais de routes publiques chinoises de première classe, d\'une expérience de site statique rafraîchie, d\'une sélection de centre de contrôle tenant compte de la juridiction et d\'une gestion des données source-série-première plus forte dans les API et les exportations publiques.',
    summaryZh:
      'GIDS 现已提供完整中文公开路由、焕新的静态站点体验、支持辖区选择的控制中心，以及贯穿公开 API 和导出的来源序列优先数据处理。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added Chinese routes for home, countries, diseases, reports, Situation Room, Research Radar, downloads, legal pages, and Changelog with localized canonical navigation.',
            fr: 'Ajout d\'itinéraires chinois pour la maison, les pays, les maladies, les rapports, la salle de situation, le radar de recherche, les téléchargements, les pages juridiques et le changelog avec navigation canonique localisée.',
            zh: '新增首页、国家、病种、报告、态势室、研究雷达、下载、法律页面和更新日志的中文路由，并提供本地化 canonical 导航。',
          },
          {
            en: 'Added a China provincial monthly-source framework with province source configuration, 31 adapter registrations, documentation, tests, and Control Center/public metadata wiring.',
            fr: 'Ajout d\'une structure de source mensuelle provinciale en Chine avec configuration de la source provinciale, 31 enregistrements d\'adaptateurs, documentation, tests et câblage du centre de contrôle/des métadonnées publiques.',
            zh: '新增中国省级月度来源框架，包括省级来源配置、31 个适配器注册、文档、测试，以及控制中心和公开端元数据接入。',
          },
          {
            en: 'Added jurisdiction-aware country and region classifications for coverage, source labels, API metadata, and the Control Center jurisdiction picker.',
            fr: 'Ajout de classifications de pays et de régions tenant compte de la juridiction pour la couverture, les étiquettes de source, les métadonnées d\'API et le sélecteur de juridiction du Centre de contrôle.',
            zh: '新增面向辖区的国家与区域分类，用于覆盖地图、来源标签、API 元数据和控制中心辖区选择器。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Refreshed the public home, country, download, about, copyright, terms, and changelog experiences and synchronized offline country flags for deterministic static builds.',
            fr: 'Actualisation de l\'accueil public, du pays, du téléchargement, des informations, des droits d\'auteur, des conditions et des expériences du journal des modifications et des drapeaux de pays hors ligne synchronisés pour des constructions statiques déterministes.',
            zh: '焕新公开首页、国家、下载、关于、版权、条款和更新日志体验，并同步离线国家旗帜以保证静态构建可复现。',
          },
          {
            en: 'Promoted source-series-first handling through APIs, site generation, charts, downloads, and coverage summaries so canonical projections and source-only series stay distinct.',
            fr: 'Promotion de la gestion des séries de sources en premier par le biais des API, de la génération de sites, des graphiques, des téléchargements et des résumés de couverture afin que les projections canoniques et les séries de sources uniquement restent distinctes.',
            zh: '将来源序列优先处理贯穿 API、站点生成、图表、下载和覆盖摘要，使标准投影与仅来源序列保持清晰区分。',
          },
          {
            en: 'Strengthened reusable ECDC, Singapore CDA, and Canada CNDSS ingestion paths with source-contract review, attribution, scheduled checks, and fail-closed refresh behavior.',
            fr: 'Renforcement des voies d\'ingestion réutilisables de l\'ECDC, de l\'ADC de Singapour et du CNDSS du Canada avec examen du contrat source, attribution, vérifications planifiées et comportement de rafraîchissement fermé.',
            zh: '强化可复用的 ECDC、新加坡 CDA 和加拿大 CNDSS 摄取路径，补充来源契约审核、归属说明、计划检查和失效关闭刷新行为。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened tests for disease-series policy, ontology validation, country coverage, classifications, generated API contracts, and source-series-first site exports.',
            fr: 'Tests renforcés pour la politique de la série de maladies, la validation de l\'ontologie, la couverture du pays, les classifications, les contrats API générés et les exportations de sites en série source.',
            zh: '强化病种序列策略、 ontology 校验、国家覆盖、分类、生成式 API 契约和来源序列优先站点导出的测试。',
          },
          {
            en: 'Improved chart and map behavior for sparse source data, hidden projections, mixed cadence series, and localized country/disease pages.',
            fr: 'Amélioration du comportement des graphiques et des cartes pour des données sources rares, des projections cachées, des séries de cadences mixtes et des pages de pays/maladies localisées.',
            zh: '改进稀疏来源数据、隐藏投影、混合频率序列以及本地化国家/病种页面中的图表和地图表现。',
          },
          {
            en: 'Kept local Playwright run output and generated flag assets out of release commits while preserving stable static-site builds.',
            fr: 'A maintenu la sortie locale de Playwright et généré des actifs de drapeau hors des validations de version tout en préservant les builds de site statique stables.',
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
    titleFr: 'Terminer les données de référence de l\'UE/EEE et l\'histoire nationale du Canada',
    titleZh: '欧盟/欧洲经济区基线全覆盖与加拿大国家历史数据',
    summaryEn:
      'All 30 EU/EEA countries, the historical United Kingdom baseline, and Canada PHAC CNDSS are now fully integrated with reviewed source contracts and public provenance.',
    summaryFr: 'Les 30 pays de l\'UE/EEE, la base de référence historique du Royaume-Uni et le CNDSS de l\'ASPC du Canada sont maintenant entièrement intégrés aux contrats sources examinés et à la provenance publique.',
    summaryZh:
      '全部 30 个欧盟/欧洲经济区国家、英国历史基线及加拿大 PHAC CNDSS 现已通过经审核的来源契约和公开来源说明实现完整接入。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added 23 ECDC countries with 20,334 observations across 1,193 source series with values; the complete 31-country ECDC collection now contains 27,843 source observations.',
            fr: 'Ajout de 23 pays de l\'ECDC avec 20 334 observations dans 1 193 séries de sources avec des valeurs ; la collection complète de 31 pays de l\'ECDC contient maintenant 27 843 observations de sources.',
            zh: '新增 23 个 ECDC 国家、20,334 条观测和 1,193 条有值来源序列；完整的 31 国 ECDC 数据集现包含 27,843 条来源观测。',
          },
          {
            en: 'Added Canada PHAC CNDSS with 3,671 national annual observations across 69 source series from 1924 through 2023 under the Open Government Licence – Canada.',
            fr: 'Ajout du CNDSS de l\'ASPC du Canada avec 3 671 observations annuelles nationales dans 69 séries sources de 1924 à 2023 sous la licence du gouvernement ouvert – Canada.',
            zh: '依据加拿大开放政府许可，新增加拿大 PHAC CNDSS 1924—2023 年 3,671 条全国年度观测，覆盖 69 条来源序列。',
          },
          {
            en: 'Integrated every new country into Control Center, automation, APIs, bilingual country pages, provenance, coverage, and CSV/JSON/XLSX downloads.',
            fr: 'Intégration de chaque nouveau pays dans le centre de contrôle, l\'automatisation, les API, les pages de pays bilingues, la provenance, la couverture et les téléchargements CSV/JSON/XLSX.',
            zh: '将所有新增国家完整接入控制中心、自动化、API、中英文国家页面、来源追溯、覆盖地图以及 CSV/JSON/XLSX 下载。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Added 24 independent daily publication checks and kept regional baselines separate from higher-frequency national feeds, preventing overlapping records from being counted twice.',
            fr: 'Ajout de 24 vérifications de publication quotidiennes indépendantes et maintien de références régionales séparées des flux nationaux à fréquence plus élevée, empêchant les enregistrements qui se chevauchent d\'être comptés deux fois.',
            zh: '新增 24 个独立的每日发布检查，并将区域基线与更高频国家来源保持分离，避免重叠记录被重复计数。',
          },
          {
            en: 'Canonicalized the historical ECDC UK geography to platform code GB while retaining UK in provenance; England-only laboratory notifications remain separate.',
            fr: 'Canonisation de la géographie historique de l\'ECDC au Royaume-Uni en code de plate-forme GB tout en conservant la provenance du Royaume-Uni ; les notifications de laboratoire en Angleterre uniquement restent séparées.',
            zh: '将 ECDC 历史来源代码 UK 规范化为平台代码 GB，同时在来源追溯中保留 UK；英格兰实验室通知继续独立管理。',
          },
          {
            en: 'Clarified that Canada values are PHAC published national aggregates with varying jurisdiction coverage, including 44 disease contracts without Manitoba data in 2023.',
            fr: 'A précisé que les valeurs canadiennes sont des agrégats nationaux publiés par l\'ASPC avec une couverture de juridiction variable, y compris 44 contrats de maladie sans données manitobaines en 2023.',
            zh: '明确加拿大数值是 PHAC 发布的全国汇总，辖区覆盖会随病种和年份变化，其中 2023 年有 44 个病种契约缺少 Manitoba 数据。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Mapped Canadian viral meningitis to active concept D134 and retained historical non-A/non-B hepatitis as a related source-only series rather than using a deprecated concept.',
            fr: 'Mappage de la méningite virale canadienne au concept actif D134 et conservation de l\'hépatite non A/non B historique comme une série liée à la source seulement plutôt que d\'utiliser un concept obsolète.',
            zh: '将加拿大病毒性脑膜炎映射到有效概念 D134，并将历史“非 A 非 B 型肝炎”保留为相关的仅来源序列，不再使用废弃概念。',
          },
          {
            en: 'Made Canada refreshes preserve the full reviewed history and fail closed when the official last year changes; all-null contracts remain pending and 645 explicit zeroes remain observations.',
            fr: 'Les actualisations de Made Canada préservent l\'historique complet examiné et échouent lorsque l\'officiel de l\'année dernière change ; les contrats entièrement nuls restent en attente et 645 zéros explicites restent des observations.',
            zh: '使加拿大刷新保留完整已审核历史，并在官方末年变化时失效关闭；全空契约保持待定，645 个明确零值继续作为观测保留。',
          },
          {
            en: 'Kept Thailand in source research after its public snapshot, login-gated national exports, zero-reporting semantics, and reuse terms failed the publication contract review; no placeholder data were added.',
            fr: 'A maintenu la Thaïlande dans la recherche de sources après que son instantané public, ses exportations nationales connectées, sa sémantique de rapport zéro et ses conditions de réutilisation aient échoué à l\'examen du contrat de publication ; aucune donnée d\'espace réservé n\'a été ajoutée.',
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
    titleFr: 'Élargissement de la base de référence de l\'ECDC',
    titleZh: 'ECDC 年度基线扩展至七个国家',
    summaryEn:
      'Spain, Italy, Portugal, Poland, Czechia, Greece, and Romania are now fully supported using attributed ECDC closed-year surveillance baselines.',
    summaryFr: 'L\'Espagne, l\'Italie, le Portugal, la Pologne, la Tchéquie, la Grèce et la Roumanie sont désormais entièrement soutenus en utilisant les bases de surveillance attribuées à l\'ECDC en année fermée.',
    summaryZh:
      '西班牙、意大利、葡萄牙、波兰、捷克、希腊和罗马尼亚现已通过注明归属的 ECDC 已闭合年度监测基线实现全量支持。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added 6,618 annual observations across 375 source series with values, backed by 385 reviewed country-specific ECDC contracts from 1990 through 2025.',
            fr: 'Ajout de 6 618 observations annuelles dans 375 séries sources avec des valeurs, soutenues par 385 contrats ECDC spécifiques à chaque pays examinés de 1990 à 2025.',
            zh: '新增 1990—2025 年 6,618 条年度观测，覆盖 375 条有值来源序列，并由 385 条经审核的国家级 ECDC 契约支撑。',
          },
          {
            en: 'Integrated all seven countries into Control Center, APIs, coverage, bilingual country pages, provenance, and CSV/JSON/XLSX downloads.',
            fr: 'Intégration des sept pays dans le centre de contrôle, les API, la couverture, les pages de pays bilingues, la provenance et les téléchargements CSV/JSON/XLSX.',
            zh: '将七国全部接入控制中心、API、覆盖地图、中英文国家页面、来源追溯以及 CSV/JSON/XLSX 下载。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Added staggered local-time daily publication checks and normalized the ECDC EL source code to the canonical GR identity for Greece.',
            fr: 'Ajout de contrôles de publication quotidiens échelonnés en heure locale et normalisation du code source ECDC EL à l\'identité canonique GR pour la Grèce.',
            zh: '新增按各国本地时间错峰执行的每日发布检查，并将 ECDC 的希腊来源代码 EL 规范化为平台标准身份 GR。',
          },
          {
            en: 'Preserved asynchronous disease coverage, source gaps as unknown, explicit zeroes, and source-only composite categories without double counting.',
            fr: 'Préservation de la couverture des maladies asynchrones, des lacunes de source comme inconnues, des zéros explicites et des catégories composites de source uniquement sans double comptage.',
            zh: '保留各病种异步覆盖、来源断点的未知语义、明确零值及仅来源层复合类别，避免重复计数。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Withheld in-progress current-year Atlas cells so partial Czechia 2026 values cannot appear as completed annual totals.',
            fr: 'Retrait des cellules Atlas en cours de l\'année en cours, de sorte que les valeurs partielles de la Tchéquie 2026 ne peuvent pas apparaître comme des totaux annuels achevés.',
            zh: '暂不发布尚未闭合的当年 Atlas 单元，避免捷克 2026 年部分累计值被误呈现为完整年度总量。',
          },
          {
            en: 'Made refreshed ECDC windows authoritative in CSV and database storage so withdrawn cells become unknown instead of remaining stale.',
            fr: 'Fenêtres ECDC actualisées faisant autorité en CSV et en stockage de base de données afin que les cellules retirées deviennent inconnues au lieu de rester obsolètes.',
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
    titleFr: 'France Base de référence annuelle de l\'ECDC et adaptateur régional réutilisable',
    titleZh: '法国 ECDC 年度基线与可复用区域适配器',
    summaryEn:
      'France is now publicly supported across ingestion, Control Center, APIs, downloads, provenance, and country pages using attributed ECDC annual Member-State surveillance data.',
    summaryFr: 'La France bénéficie désormais d\'un soutien public sur les pages Ingestion, Control Center, API, téléchargements, provenance et pays à l\'aide des données de surveillance annuelles de l\'ECDC dans les États membres.',
    summaryZh:
      '法国现已通过注明归属的 ECDC 成员国年度监测数据，完整接入摄取、控制中心、API、下载、来源说明和国家页面。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added 891 France annual observations across 51 published series from 1990 through 2025, backed by 55 reviewed ECDC source contracts.',
            fr: 'Ajout de 891 observations annuelles de la France dans 51 séries publiées de 1990 à 2025, étayées par 55 contrats sources ECDC examinés.',
            zh: '新增法国 1990—2025 年 891 条年度观测，覆盖 51 条已发布序列，并由 55 条经审核的 ECDC 来源合同支撑。',
          },
          {
            en: 'Integrated France into Control Center source status and automation, public APIs, coverage, downloads, bilingual source metadata, and static generation.',
            fr: 'Intégration de la France dans l\'état et l\'automatisation des sources du Control Center, les API publiques, la couverture, les téléchargements, les métadonnées sources bilingues et la génération statique.',
            zh: '将法国接入控制中心来源状态与自动化、公开 API、覆盖地图、下载、双语来源元数据和静态生成。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Added a reusable ECDC Atlas adapter that discovers current measure IDs while failing closed on disease labels, population definitions, geography, frequency, and count semantics.',
            fr: 'Ajout d\'un adaptateur Atlas ECDC réutilisable qui découvre les ID de mesure actuels tout en ne fermant pas les étiquettes de maladie, les définitions de population, la géographie, la fréquence et la sémantique de comptage.',
            zh: '新增可复用的 ECDC Atlas 适配器：动态发现当前指标 ID，同时对病种标签、人群定义、地理层级、频率和病例计数语义实行失效关闭。',
          },
          {
            en: 'Enabled a daily 09:50 Europe/Paris publication check for annual source revisions.',
            fr: 'Activation d\'une vérification quotidienne de la publication 09:50 Europe/Paris pour les révisions annuelles des sources.',
            zh: '启用每日 09:50（Europe/Paris）的发布检查，以发现年度来源修订。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Governance',
        labelFr: 'Gouvernance',
        labelZh: '治理',
        items: [
          {
            en: 'Missing country/year cells stay unknown, explicit zeroes are preserved, composite categories remain source-only, and rate-only hepatitis B totals are not converted into case counts.',
            fr: 'Les cellules de pays/année manquantes restent inconnues, les zéros explicites sont préservés, les catégories composites restent à la source seulement et les totaux de l\'hépatite B à taux seulement ne sont pas convertis en nombres de cas.',
            zh: '缺失的国家/年份单元保持未知，明确零值予以保留，复合类别仅保留来源序列，且不会把仅有比率的乙肝总量换算成病例数。',
          },
          {
            en: 'Every France record carries the required ECDC and reporting Member-State attribution.',
            fr: 'Chaque enregistrement en France porte l\'attribution requise de l\'ECDC et de l\'État membre déclarant.',
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
    titleFr: 'Surveillance hebdomadaire à Singapour et pleine intégration publique',
    titleZh: '新加坡周度监测与公开端全量接入',
    summaryEn:
      'Singapore is now supported across ingestion, Control Center, public APIs, downloads, provenance pages, and the country experience, with official CSV history joined safely to current CDA workbooks.',
    summaryFr: 'Singapour est désormais pris en charge par ingestion, Control Center, les API publiques, les téléchargements, les pages de provenance et l\'expérience du pays, l\'historique CSV officiel étant joint en toute sécurité aux classeurs CDA actuels.',
    summaryZh:
      '新加坡现已接入数据摄取、控制中心、公开 API、下载、来源说明和国家页面，并将官方 CSV 历史与当前 CDA 工作簿安全衔接。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added Singapore national weekly notifications from the official 2012–2022 data.gov.sg CSV and 2023+ CDA annual workbooks, with 2023 weekly PDFs as a fail-closed fallback.',
            fr: 'Ajout des notifications hebdomadaires nationales de Singapour à partir des classeurs officiels CSV data.gov.sg 2012–2022 et des classeurs annuels CDA 2023+, avec les PDF hebdomadaires 2023 comme solution de repli à défaut.',
            zh: '新增新加坡全国周度病例通报：历史采用 data.gov.sg 2012—2022 年官方 CSV，2023 年起采用 CDA 年度工作簿，并以 2023 年周报 PDF 作为失效关闭的回退来源。',
          },
          {
            en: 'Registered 76 historical and current source series across 39 source categories, with complete source URLs, hashes, definition versions, and missing-value semantics.',
            fr: 'Enregistré 76 séries de sources historiques et actuelles dans 39 catégories de sources, avec des URL de source complètes, des hachages, des versions de définition et une sémantique des valeurs manquantes.',
            zh: '为 39 个来源分类注册 76 条历史与当前来源序列，并完整保留来源 URL、哈希、定义版本和缺失值语义。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Added a strict temporal-handoff projection so equivalent historical and successor series appear as one longitudinal curve only when their validity windows do not overlap.',
            fr: 'Ajout d\'une projection de transfert temporel stricte afin que les séries historiques et suivantes équivalentes n\'apparaissent comme une courbe longitudinale que lorsque leurs fenêtres de validité ne se chevauchent pas.',
            zh: '新增严格的时间接力投影：仅当历史与后继序列口径一致且有效期不重叠时，才将其呈现为一条纵向曲线。',
          },
          {
            en: 'Integrated Singapore into Control Center source status, public country coverage, About metadata, bilingual labels, downloads, and static-site generation.',
            fr: 'Intégration de Singapour dans le statut source du Control Center, la couverture publique du pays, à propos des métadonnées, des étiquettes bilingues, des téléchargements et de la génération de sites statiques.',
            zh: '将新加坡完整接入控制中心来源状态、公开国家覆盖、About 元数据、双语标签、下载和静态站点生成。',
          },
          {
            en: 'Enabled a daily 09:30 Asia/Singapore Control Center publication check to discover each new weekly CDA workbook and bounded revisions automatically.',
            fr: 'Activation d\'une vérification quotidienne de la publication du Centre de contrôle Asie/Singapour à 9h30 pour découvrir automatiquement chaque nouveau classeur hebdomadaire CDA et les révisions limitées.',
            zh: '在控制中心启用每日 09:30（Asia/Singapore）的发布检查，自动发现新的 CDA 周度工作簿及有界修订。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Governance',
        labelFr: 'Gouvernance',
        labelZh: '治理',
        items: [
          {
            en: 'Public release is explicitly operator-authorized while provenance continues to distinguish the Singapore Open Data Licence for history from CDA terms requiring written permission for current publications.',
            fr: 'La publication est explicitement autorisée par l\'opérateur, tandis que la provenance continue de distinguer la licence de données ouvertes de Singapour pour l\'historique des conditions CDA nécessitant une autorisation écrite pour les publications actuelles.',
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
    titleFr: 'Gouvernance des données auditable et fiabilité du plan de contrôle',
    titleZh: '可审计数据治理与控制面可靠性',
    summaryEn:
      'This release closes the operational loop from source ingestion through evidence review and publication: every enabled country schedule was exercised through Control Center, Research Radar gains fail-closed AI and backlog governance, and background tasks now recover safely without hiding provider failures.',
    summaryFr: 'Cette version ferme la boucle opérationnelle de l\'ingestion de la source à l\'examen et à la publication des preuves : chaque programme de pays activé a été exercé par le biais du Centre de contrôle, les gains du radar de recherche ont fermé l\'IA et la gouvernance du backlog, et les tâches de fond se rétablissent maintenant en toute sécurité sans cacher les défaillances des fournisseurs.',
    summaryZh:
      '本次更新打通从来源摄取、证据审核到数据发布的运维闭环：通过控制中心实际运行全部已启用国家调度，为研究雷达加入失效关闭的 AI 审核和积压治理，并使后台任务可安全恢复且不再掩盖来源失败。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added evidence-bounded AI review for weekly Research Radar briefs, with deterministic preflight checks, strict schemas, safe model aliases, fingerprint invalidation, concurrency protection, and explicit separation from editorial approval.',
            fr: 'Ajout d\'un examen de l\'IA fondé sur des preuves pour les notes hebdomadaires de Research Radar, avec des vérifications déterministes avant le vol, des schémas stricts, des alias de modèles sûrs, l\'invalidation des empreintes digitales, la protection de la concurrence et la séparation explicite de l\'approbation éditoriale.',
            zh: '新增周报证据边界 AI 审核，包含确定性预检、严格结构、安全模型别名、指纹失效、并发保护，并与编辑签审明确分离。',
          },
          {
            en: 'Added dry-run-first governance and recovery tools for editorial backlog, metadata coverage, stale ingest runs, and abandoned background tasks, with bounded plans and explicit apply gates.',
            fr: 'Ajout d\'outils de gouvernance et de récupération à exécution à sec pour le backlog éditorial, la couverture des métadonnées, les exécutions d\'ingestion obsolètes et les tâches de fond abandonnées, avec des plans délimités et des portes d\'application explicites.',
            zh: '新增默认预演的治理与恢复工具，覆盖编辑积压、元数据覆盖、超时摄取运行和遗留后台任务，并提供有界计划与显式写入门。',
          },
          {
            en: 'Added optional Springer Nature, Elsevier, bioRxiv/medRxiv, and publisher RSS connectors with bounded pagination, resumable checkpoints, source isolation, credential gates, and preprint review controls; paid publisher sources remain off by default.',
            fr: 'Ajout de connecteurs RSS Springer Nature, Elsevier, bioRxiv/medRxiv et éditeur optionnels avec pagination délimitée, points de contrôle de reprise, isolation de source, portes d\'identification et contrôles de révision de préimpression ; les sources d\'éditeurs payantes restent désactivées par défaut.',
            zh: '新增可选的 Springer Nature、Elsevier、bioRxiv/medRxiv 和出版商 RSS 连接器，支持有界分页、断点续运、来源隔离、凭据门和预印本审核；付费出版商来源仍默认关闭。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Hardened worker and scheduler ownership with Redis singleton leases, task heartbeats, stale-task recovery, worker-readiness watchdogs, richer health evidence, and safer systemd ordering.',
            fr: 'Propriété renforcée des travailleurs et des planificateurs avec les baux Redis singleton, les battements de cœur des tâches, la récupération des tâches périmées, les chiens de garde de préparation des travailleurs, des preuves de santé plus riches et des commandes systémiques plus sûres.',
            zh: '通过 Redis 单例租约、任务心跳、超时任务恢复、worker 就绪看门狗、更完整的健康证据与更安全的 systemd 顺序，强化 worker 和 scheduler 所有权。',
          },
          {
            en: 'Made source health reflect completed per-provider result contracts instead of configured intent, and exposed actionable catch-up state, resume thresholds, queue age, and worker/scheduler readiness.',
            fr: 'La santé de la source reflète les contrats de résultat par fournisseur terminés au lieu de l\'intention configurée, et l\'état de rattrapage exploitable exposé, les seuils de reprise, l\'âge de la file d\'attente et la préparation des travailleurs/planificateurs.',
            zh: '来源健康现在依据已完成的逐提供商结果契约，而不是配置意图；同时展示可执行的追赶状态、恢复阈值、队列年龄以及 worker/scheduler 就绪度。',
          },
          {
            en: 'Improved public release quality with stricter canonical and hreflang policy, real 301 legacy redirects, compact graph rendering, scalable HTML budgets, clearer copyright and provenance, and email-delivery feedback handling.',
            fr: 'Amélioration de la qualité de la publication publique avec une politique canonique et hreflang plus stricte, de véritables redirections 301 héritées, un rendu graphique compact, des budgets HTML évolutifs, des droits d\'auteur et une provenance plus clairs et un traitement des commentaires par courrier électronique.',
            zh: '通过更严格的 canonical/hreflang 策略、真实 301 旧链接跳转、紧凑图谱渲染、可扩展 HTML 预算、更清晰的版权与溯源信息，以及邮件送达反馈处理，提升公开发布质量。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Verified all 21 enabled country and regional schedules through Control Center and fixed CDC NHSS HIV ingestion by using an honest provider-specific crawler identity; the acceptance run completed without source errors.',
            fr: 'Vérification des 21 calendriers nationaux et régionaux activés via le Centre de contrôle et correction de l\'ingestion du VIH par le NHSS des CDC en utilisant une identité de robot d\'exploration honnête spécifique au fournisseur ; l\'exécution de l\'acceptation s\'est terminée sans erreurs de source.',
            zh: '通过控制中心验证全部 21 个已启用国家和地区调度，并通过透明的提供商专用爬虫标识修复 CDC NHSS HIV 摄取；验收运行无来源错误完成。',
          },
          {
            en: 'Fixed exact-limit OAI checkpoint rollover, optional-provider checkpoint loss, metadata backfill selection and completion boundaries, model bootstrap query storms, and read-path mutation of overdue catch-up schedules.',
            fr: 'Correction de la limite exacte du roulement des points de contrôle OAI, de la perte des points de contrôle des fournisseurs optionnels, des limites de sélection et d\'achèvement des remblais de métadonnées, des tempêtes de requêtes bootstrap du modèle et de la mutation du chemin de lecture des calendriers de rattrapage en retard.',
            zh: '修复 OAI 精确上限时的检查点翻页、可选来源检查点丢失、元数据回填选批与完成边界、模型初始化查询风暴，以及读路径修改逾期追赶调度的问题。',
          },
          {
            en: 'Made bilingual research summaries use the English evidence set as a canonical semantic contract so Chinese output cannot add, omit, or strengthen claims independently.',
            fr: 'Les résumés de recherche bilingues utilisent l\'ensemble des preuves anglaises comme un contrat sémantique canonique afin que la production chinoise ne puisse pas ajouter, omettre ou renforcer les revendications de manière indépendante.',
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
    titleFr: 'Télécharge l\'ergonomie du catalogue et la récupération du pipeline de publication',
    titleZh: '下载目录体验与发布流水线恢复能力改进',
    summaryEn:
      'This release turns Downloads into a more practical data catalogue with shareable filters, sorting, sticky table context, and mobile cards, while improving Taiwan monthly recovery and hardening automated publication checks.',
    summaryFr: 'Cette version transforme les téléchargements en un catalogue de données plus pratique avec des filtres partageables, un tri, un contexte de table collante et des cartes mobiles, tout en améliorant la récupération mensuelle de Taiwan et en durcissant les contrôles de publication automatisés.',
    summaryZh:
      '本次更新将 Downloads 打磨为更实用的数据目录，新增可分享筛选、排序、吸顶表头和移动端卡片，同时改进台湾月度数据恢复并强化自动发布校验。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added dataset sorting on Downloads by featured order, name, record volume, and dataset type so large catalogues are easier to scan.',
            fr: 'Ajout du tri des jeux de données sur les téléchargements par ordre de mise en avant, nom, volume d\'enregistrement et type de jeu de données afin que les grands catalogues soient plus faciles à numériser.',
            zh: 'Downloads 新增按默认顺序、名称、记录量和数据集类型排序，便于浏览大型数据目录。',
          },
          {
            en: 'Added shareable Downloads state in the URL for search text, dataset type, and sort mode, with direct links restoring the same catalogue view.',
            fr: 'Ajout de l\'état Téléchargements partageables dans l\'URL pour le texte de recherche, le type de jeu de données et le mode de tri, avec des liens directs rétablissant la même vue du catalogue.',
            zh: 'Downloads 的搜索词、数据集类型和排序方式会写入 URL，直接打开链接即可恢复相同目录视图。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Reworked the Downloads table into a stable data-tool layout with fixed format columns, a sticky header on desktop, and a card layout on mobile to avoid horizontal scrolling.',
            fr: 'Retravaillé le tableau Téléchargements dans une mise en page d\'outil de données stable avec des colonnes de format fixe, un en-tête collant sur le bureau et une mise en page de carte sur mobile pour éviter le défilement horizontal.',
            zh: '重构 Downloads 表格为更稳定的数据工具布局：桌面端固定格式列并吸顶表头，移动端改为卡片布局以避免横向滚动。',
          },
          {
            en: 'Raised the public-site HTML performance budget to match the expanded Research graph while keeping the full production build inside the total gzip budget.',
            fr: 'Augmentation du budget de performance HTML du site public pour correspondre au graphique de recherche élargi tout en maintenant la production complète à l\'intérieur du budget total de gzip.',
            zh: '根据扩展后的 Research graph 调整公开站点 HTML 性能预算，同时保持完整生产构建仍在总 gzip 预算内。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed misaligned Downloads rows by restoring native table-cell layout and constraining CSV, JSON, XLSX, and source-note columns.',
            fr: 'Correction des lignes de téléchargement mal alignées en restaurant la disposition native des cellules du tableau et en contraignant les colonnes CSV, JSON, XLSX et source-note.',
            zh: '修复 Downloads 行列错位：恢复原生 table-cell 布局，并约束 CSV、JSON、XLSX 与来源说明列宽。',
          },
          {
            en: 'Recovered Taiwan NIDSS national monthly updates from cached raw monthly files when live CSV endpoints are temporarily unavailable.',
            fr: 'Mises à jour mensuelles nationales NIDSS de Taïwan récupérées à partir de fichiers mensuels bruts mis en cache lorsque les points de terminaison CSV en direct sont temporairement indisponibles.',
            zh: '当台湾 NIDSS 在线 CSV 端点暂时不可用时，可从本地 raw 月度缓存恢复全国月度数据更新。',
          },
          {
            en: 'Made GitHub direct-download verification retry after publication so transient Contents API visibility delays no longer fail an otherwise successful release.',
            fr: 'A fait en sorte que la vérification de téléchargement direct de GitHub soit réessayée après la publication afin que les retards transitoires de visibilité de l\'API de contenu n\'échouent plus à une version par ailleurs réussie.',
            zh: '为 GitHub 直接下载仓库的发布后校验加入重试，避免 Contents API 短暂不可见导致已成功的发布误失败。',
          },
          {
            en: 'Hardened China registry coverage policy and Research export fallback text for edge cases with incomplete disease metadata.',
            fr: 'Politique de couverture du registre de la Chine durcie et texte de repli de l\'exportation de la recherche pour les cas marginaux avec des métadonnées de maladie incomplètes.',
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
    titleFr: 'Contrôles étalonnés de la salle de situation et fiabilité du site public',
    titleZh: '态势室校准控制与公开站点可靠性改进',
    summaryEn:
      'This maintenance release advances Situation Room to a calibrated, multi-horizon v3.2 workflow and hardens the public GIDS experience with offline country flags, release-linked versioning, and broader browser regression checks.',
    summaryFr: 'Cette version de maintenance fait progresser Situation Room vers un flux de travail calibré et multi-horizon v3.2 et renforce l\'expérience GIDS publique avec des indicateurs de pays hors ligne, un contrôle de version lié à la version et des contrôles de régression du navigateur plus larges.',
    summaryZh:
      '本次维护版本将态势室推进到经校准的多窗口 v3.2 工作流，并通过本地国旗资源、关联更新记录的版本信息和更广泛的浏览器回归检查，增强 GIDS 公开站点的可靠性。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added the Situation Room v3.2 multi-horizon Gamma-Poisson detector, which evaluates weekly and monthly count horizons through one correlated omnibus test and applies cadence- and expected-count-specific effect gates.',
            fr: 'Ajout du détecteur Gamma-Poisson multi-horizon Situation Room v3.2, qui évalue les horizons de comptage hebdomadaires et mensuels grâce à un test omnibus corrélé et applique des portes d\'effet spécifiques à la cadence et au comptage attendu.',
            zh: '新增态势室 v3.2 多窗口 Gamma-Poisson 检测器：通过一个保留相关性的综合检验评估周度和月度计数窗口，并按频率和预期计数应用效应门槛。',
          },
          {
            en: 'Added durable event labels, calibration artifacts, and per-signal publication-policy decisions, with database migration, operator APIs, and scripts for auditable calibration registration.',
            fr: 'Ajout d\'étiquettes d\'événements durables, d\'artefacts d\'étalonnage et de décisions de politique de publication par signal, avec migration de base de données, API d\'opérateur et scripts pour un enregistrement d\'étalonnage vérifiable.',
            zh: '新增可持久化的事件标签、校准制品和逐信号发布策略决策，并提供数据库迁移、运营 API 与脚本，以支持可审计的校准登记。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Made automatic Situation verification fail closed by source and cadence group until a registered calibration meets null-family safety, sensitivity, detection-delay, and optional official-corroboration requirements.',
            fr: 'La vérification automatique de la situation est fermée par groupe de source et de cadence jusqu\'à ce qu\'un étalonnage enregistré réponde aux exigences de sécurité de la famille nulle, de sensibilité, de délai de détection et de correction officielle facultative.',
            zh: '按来源和频率分组强化态势自动核验的失效关闭机制；只有已登记校准满足零假设族安全性、灵敏度、检出延迟及可选官方佐证要求后才可启用。',
          },
          {
            en: 'Expanded public-site and dashboard regression coverage for accessibility, responsive navigation, release provenance, and the new Situation calibration and policy-decision surfaces.',
            fr: 'Élargissement de la couverture de régression des sites publics et des tableaux de bord pour l\'accessibilité, la navigation réactive, la provenance des versions et les nouvelles surfaces d\'étalonnage de la situation et de décision des politiques.',
            zh: '扩展公众站点与控制台的回归覆盖，涵盖无障碍、响应式导航、发布溯源，以及新增的态势校准和策略决策界面。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Restored country flags in Data Coverage map labels and hover tooltips with bundled local SVG assets, including every country and region in the coverage roadmap.',
            fr: 'Restauration des indicateurs de pays dans les étiquettes de carte de couverture des données et les info-bulles de survol avec les actifs SVG locaux regroupés, y compris chaque pays et région de la feuille de route de couverture.',
            zh: '使用随站点打包的本地 SVG 资源恢复 Data Coverage 地图标签和悬浮提示中的国旗，并覆盖路线图中的全部国家和地区。',
          },
          {
            en: 'Made the footer version derive from the application package and link to the localized Changelog, preventing version text from drifting from the release manifest.',
            fr: 'A fait en sorte que la version du pied de page dérive du package de l\'application et soit liée au Changelog localisé, empêchant ainsi le texte de la version de dériver du manifeste de publication.',
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
    titleFr: 'La marque, l\'expérience bilingue et la confiance en soi reconstruisent',
    titleZh: '品牌、双语体验与发布可信度重构',
    summaryEn:
      'This release rebuilds the public GIDS experience around a scientific editorial brand, completes the first full pass of bilingual public navigation and research routes, and hardens accessibility, search, performance, and deployment verification.',
    summaryFr: 'Cette version reconstruit l\'expérience GIDS publique autour d\'une marque éditoriale scientifique, complète le premier passage complet de la navigation publique bilingue et des itinéraires de recherche, et renforce l\'accessibilité, la recherche, les performances et la vérification du déploiement.',
    summaryZh:
      '本次更新围绕科学编辑部式品牌重构 GIDS 公众体验，完成第一轮完整双语导航与研究路由覆盖，并强化无障碍、搜索、性能和部署一致性校验。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced the refreshed GIDS identity, local brand assets, a unified self-hosted interface typeface, and a separate Control Center sub-brand.',
            fr: 'Présentation de l\'identité GIDS actualisée, des atouts de la marque locale, d\'une police d\'interface auto-hébergée unifiée et d\'une sous-marque distincte du Centre de contrôle.',
            zh: '推出更新后的 GIDS 品牌系统、本地品牌资源、统一的自托管界面字体，以及独立的 Control Center 子品牌。',
          },
          {
            en: 'Added a compact static search index at `/site-data/search-index.json`, a noindex `/search/` experience, localized `/zh/search/`, and a Cmd/Ctrl+K quick search panel grouped across countries, diseases, Situation, reports, research, and pages.',
            fr: 'Ajout d\'un index de recherche statique compact à `/site-data/search-index.json`, d\'un noindex `/search/` experience, localised `/zh/search/` et d\'un panneau de recherche rapide Cmd/Ctrl+K regroupés selon les pays, les maladies, la situation, les rapports, la recherche et les pages.',
            zh: '新增紧凑静态搜索索引 `/site-data/search-index.json`、noindex 的 `/search/` 搜索页、本地化 `/zh/search/`，以及按国家、疾病、态势、报告、研究和页面分组的 Cmd/Ctrl+K 快速搜索面板。',
          },
          {
            en: 'Expanded Chinese public coverage with locale-aware Research routes, matching canonical and hreflang metadata, and sitemap entries for Research index, articles, topics, countries, diseases, integrity, preprints, graph, and weekly pages.',
            fr: 'Couverture publique chinoise étendue avec des itinéraires de recherche tenant compte des paramètres régionaux, des métadonnées canoniques et hreflang correspondantes et des entrées de sitemap pour l\'index de recherche, les articles, les sujets, les pays, les maladies, l\'intégrité, les préimpressions, les graphiques et les pages hebdomadaires.',
            zh: '扩展中文公众覆盖，新增 locale-aware 的 Research 路由、对应 canonical 与 hreflang 元数据，并将研究首页、文章、主题、国家、疾病、完整性、预印本、图谱和周报纳入 sitemap。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Restructured the public home, header, footer, country pages, disease pages, Situation pages, report pages, and downloads entry around current status, source trust, analysis pathways, and clearer professional navigation.',
            fr: 'Restructuration de l\'accueil public, de l\'en-tête, du pied de page, des pages de pays, des pages de maladie, des pages de situation, des pages de rapport et des téléchargements autour de l\'état actuel, de la confiance dans les sources, des chemins d\'analyse et d\'une navigation professionnelle plus claire.',
            zh: '围绕当前态势、来源可信度、分析入口和更清晰的专业导航，重组公众首页、页眉、页脚、国家页、疾病页、态势页、报告页和下载入口。',
          },
          {
            en: 'Improved mobile usability by removing the oversized mobile hero, tightening the menu and Control Center shell, preserving keyboard focus, supporting Escape dismissal, and preventing horizontal overflow across tested viewports.',
            fr: 'Amélioration de la convivialité mobile en supprimant le héros mobile surdimensionné, en resserrant le menu et le shell du Centre de contrôle, en préservant la mise au point du clavier, en prenant en charge le rejet d\'échappement et en empêchant le débordement horizontal entre les fenêtres testées.',
            zh: '优化移动端体验，移除过高的移动首屏英雄区，收紧菜单和 Control Center 外壳，保留键盘焦点，支持 Escape 关闭，并在测试视口中防止横向溢出。',
          },
          {
            en: 'Hardened accessibility and data controls with stable id/name attributes, labels, help-text associations, clearer CTA copy, stronger contrast, chart/table alternatives, and WCAG checks across public and Control Center routes.',
            fr: 'Contrôles d\'accessibilité et de données renforcés avec des attributs d\'ID/nom stables, des étiquettes, des associations de texte d\'aide, une copie CTA plus claire, un contraste plus fort, des alternatives de graphiques/tableaux et des vérifications WCAG sur les itinéraires publics et du centre de contrôle.',
            zh: '强化无障碍和数据控件，为表单补齐稳定 id/name、标签与帮助文本关联，优化 CTA 文案和对比度，并为图表/表格替代视图及公众端、Control Center 路由加入 WCAG 检查。',
          },
          {
            en: 'Rebalanced public performance budgets for JavaScript chunks, route assets, HTML gzip size, and font payloads, while keeping chart rendering lazy and replacing runtime FlagCDN requests with local region markers.',
            fr: 'Rééquilibrage des budgets de performance publics pour les morceaux JavaScript, les ressources de routage, la taille HTML gzip et les charges utiles de police, tout en gardant le rendu du graphique paresseux et en remplaçant les requêtes FlagCDN d\'exécution par des marqueurs de région locale.',
            zh: '重新校准公众端 JavaScript chunk、路由资源、HTML gzip 和字体体积预算，同时保留图表懒加载，并以本地区域标记替代运行时 FlagCDN 请求。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Changed language switching from DOM-only text replacement to locale URL navigation so the visible URL, page title, document language, canonical URL, and alternate links stay synchronized.',
            fr: 'Changement de langue de remplacement de texte DOM uniquement à la navigation URL locale afin que l\'URL visible, le titre de la page, la langue du document, l\'URL canonique et les liens alternatifs restent synchronisés.',
            zh: '将语言切换从仅替换 DOM 文本改为跳转对应 locale URL，使可见地址、页面标题、文档语言、canonical 和 alternate 链接保持同步。',
          },
          {
            en: 'Added a post-deployment source-commit verifier so production HTML must expose the expected `gids-source-commit` before a release is accepted.',
            fr: 'Ajout d\'un vérificateur de source-commit post-déploiement, de sorte que le HTML de production doit exposer le « gids-source-commit » attendu avant qu\'une version ne soit acceptée.',
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
    titleFr: 'Salle de situation v3, alertes vérifiées et radar de recherche de qualité probante',
    titleZh: '态势室 v3、已核验提醒与证据级研究雷达',
    summaryEn:
      'This release moves Situation Room onto a versioned, auditable v3 contract, expands Research Radar into a searchable evidence product, and adds safer subscription, alerting, release, and quality-gate automation.',
    summaryFr: 'Cette version déplace Situation Room sur un contrat v3 versionnable et auditable, étend Research Radar en un produit de preuves consultable et ajoute une automatisation plus sûre des abonnements, des alertes, des versions et de la qualité.',
    summaryZh:
      '本次更新将态势室迁移到可审计的 v3 版本化契约，扩展研究雷达为可检索的证据产品，并新增更安全的订阅、提醒、发布和质量门控自动化。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced Situation Room v3 with Pydantic-owned contracts, immutable daily, weekly, and monthly reports, versioned public JSON, source-readiness ledgers, and dedicated public archive routes.',
            fr: 'Introduction de la salle de situation v3 avec des contrats appartenant à Pydantic, des rapports quotidiens, hebdomadaires et mensuels immuables, des versions JSON publiques, des registres de préparation des sources et des itinéraires d\'archives publiques dédiés.',
            zh: '推出态势室 v3，使用 Pydantic 作为契约源，支持不可变的日、周、月报告、版本化公开 JSON、来源就绪度台账和专用公开归档路由。',
          },
          {
            en: 'Added a Situation v3 operations API and dashboard workspace for runs, signals, source health, event clusters, reports, audited review decisions, publish actions, and rollback.',
            fr: 'Ajout d\'une API d\'opérations Situation v3 et d\'un espace de travail de tableau de bord pour les exécutions, les signaux, la santé de la source, les clusters d\'événements, les rapports, les décisions d\'examen auditées, les actions de publication et la restauration.',
            zh: '新增态势 v3 运营 API 与控制台工作区，覆盖运行、信号、来源健康、事件聚类、报告、审计化复核决策、发布操作和回滚。',
          },
          {
            en: 'Expanded Research Radar with Ask GIDS Research, an evidence graph, topic and country collections, preprint and integrity registers, scoped RSS feeds, social cards, and a richer public catalogue.',
            fr: 'Radar de recherche étendu avec Ask GIDS Research, un graphique de preuves, des collections de sujets et de pays, des registres de préimpression et d\'intégrité, des flux RSS étendus, des cartes sociales et un catalogue public plus riche.',
            zh: '扩展研究雷达，新增“问研究雷达”、证据图谱、主题与国家集合、预印本与完整性登记、分范围 RSS、社交分享卡片和更丰富的公开目录。',
          },
          {
            en: 'Added subscription support for weekly Research Radar digests and verified Situation alerts, including D1 migrations, preference filters, idempotent campaigns, an alert outbox, and optional Cloudflare Queue fan-out.',
            fr: 'Ajout de la prise en charge des abonnements pour les résumés hebdomadaires de Research Radar et les alertes de situation vérifiées, y compris les migrations D1, les filtres de préférences, les campagnes idempotentes, une boîte d\'envoi d\'alertes et la sortie optionnelle de la file d\'attente Cloudflare.',
            zh: '新增研究雷达周报和已核验态势提醒的订阅支持，包括 D1 迁移、偏好筛选、幂等 campaign、提醒 outbox 和可选 Cloudflare Queue 分发。',
          },
          {
            en: 'Added production-oriented GitHub workflows for Situation Room release gates, exact artifact deployment verification, reviewed-alert dispatch, full project quality checks, and PostgreSQL migration smoke tests.',
            fr: 'Ajout de flux de travail GitHub axés sur la production pour les portes de libération de la salle de situation, la vérification exacte du déploiement des artefacts, la répartition des alertes révisées, les contrôles de qualité complets du projet et les tests de fumée de migration PostgreSQL.',
            zh: '新增面向生产的 GitHub 工作流，支持态势室发布门控、精确制品部署验证、已复核提醒分发、全项目质量检查和 PostgreSQL 迁移冒烟测试。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Strengthened Situation analysis with source-cadence maturity windows, deterministic geography identity, bounded concurrent adapters, robust quasi-Poisson modeling, rare-count tail correction, and detector-tier FDR families.',
            fr: 'Analyse de situation renforcée avec des fenêtres de maturité source-cadence, une identité géographique déterministe, des adaptateurs concurrents bornés, une modélisation quasi-Poisson robuste, une correction de queue rare et des familles FDR de niveau détecteur.',
            zh: '加强态势分析，加入来源频率成熟窗口、确定性地理身份、有界并发适配器、稳健 quasi-Poisson 建模、稀有计数尾部修正和按检测层级划分的 FDR 检验族。',
          },
          {
            en: 'Made Situation publication fail closed with immutable history storage, quality-gated pointer advancement, calibrated backtesting, guarded automation diagnostics, and analyst-review-only production alert dispatch.',
            fr: 'La publication Made Situation échoue avec le stockage de l\'historique immuable, l\'avancement des pointeurs à qualité contrôlée, le backtesting calibré, les diagnostics d\'automatisation protégés et l\'envoi d\'alertes de production uniquement par les analystes.',
            zh: '让态势发布默认失败关闭，支持不可变历史存储、质量门控后的指针推进、校准回测、受控自动化诊断，以及生产环境仅分发人工复核提醒。',
          },
          {
            en: 'Upgraded Research Radar ingestion with controlled discovery, publisher RSS, WHO IRIS guidance metadata, OpenAlex and Unpaywall enrichment, resumable metadata backfill, version-5 classification, and privacy-safe health checks.',
            fr: 'Mise à niveau de l\'ingestion du radar de recherche avec découverte contrôlée, RSS de l\'éditeur, métadonnées de guidage de l\'IRIS de l\'OMS, enrichissement OpenAlex et Unpaywall, remplissage des métadonnées reprise, classification de la version 5 et contrôles de santé sans danger pour la vie privée.',
            zh: '升级研究雷达接入，支持受控发现、出版社 RSS、WHO IRIS 指南元数据、OpenAlex 与 Unpaywall 增强、可恢复元数据回填、v5 分类和隐私安全健康检查。',
          },
          {
            en: 'Improved static-site reliability and performance with deterministic build fixtures, research release validation, ECharts bundle splitting, world-map optimization, font/logo assets, sitemap coverage, and route-level performance budgets.',
            fr: 'Amélioration de la fiabilité et des performances du site statique avec des fixations de construction déterministes, la validation de la publication de la recherche, le fractionnement du faisceau ECharts, l\'optimisation de la carte du monde, les actifs de police/logo, la couverture du plan du site et les budgets de performance au niveau de l\'itinéraire.',
            zh: '提升静态站点可靠性与性能，加入确定性构建夹具、研究发布验证、ECharts 拆包、世界地图优化、字体与标志资源、站点地图覆盖和路由级性能预算。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed unattended ingestion and data-release recovery so transient scheduled failures park in retrying, requeue atomically, preserve their task identity, and keep crawl-run audit rows from remaining indefinitely active.',
            fr: 'Correction de l\'ingestion sans surveillance et de la récupération de la libération des données de sorte que les défaillances programmées transitoires se garent dans la réessai, la mise en file d\'attente atomique, préservent leur identité de tâche et empêchent les lignes d\'audit d\'exploration de rester indéfiniment actives.',
            zh: '修复无人值守接入与数据发布恢复流程，使计划任务的暂态失败进入 retrying、到期后原子化重入队列、保留原任务身份，并避免抓取运行审计行永久停留在活跃状态。',
          },
          {
            en: 'Tightened public evidence boundaries so raw abstracts, provider payloads, PDFs, unreviewed summaries, stale contracts, unverified automated alerts, and invalid research or Situation artifacts fail before publication.',
            fr: 'A resserré les limites des preuves publiques de sorte que les résumés bruts, les charges utiles des fournisseurs, les PDF, les résumés non examinés, les contrats périmés, les alertes automatisées non vérifiées et les recherches invalides ou les artefacts de situation échouent avant la publication.',
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
    titleFr: 'Research Radar, Situation Room v2 et des workflows d\'analyse plus forts',
    titleZh: '研究雷达、态势室 v2 与更强的分析工作流',
    summaryEn:
      'This release introduces public literature intelligence, upgrades the Situation Room into a reviewable signal system, and gives operators safer automation, release, and disease-mapping controls.',
    summaryFr: 'Cette version présente les informations de la littérature publique, met à niveau la salle de situation en un système de signalisation révisable et offre aux opérateurs des contrôles plus sûrs en matière d\'automatisation, de libération et de cartographie des maladies.',
    summaryZh:
      '本次更新上线公开文献情报能力，将态势室升级为可复核的信号系统，并为运营人员提供更安全的自动化、发布和疾病映射控制。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added Research Radar with public literature pages for articles, disease evidence hubs, country collections, topic collections, weekly briefs, catalogue JSON, RSS, and curated historical baselines.',
            fr: 'Ajout d\'un radar de recherche avec des pages de littérature publique pour les articles, les centres de données sur les maladies, les collections par pays, les collections thématiques, les notes hebdomadaires, le catalogue JSON, le RSS et les bases historiques organisées.',
            zh: '新增研究雷达，提供公开文献文章页、疾病证据中心、国家集合、主题集合、周报、目录 JSON、RSS 以及经策展的历史基线文献。',
          },
          {
            en: 'Added a literature operations workspace with Crossref and Europe PMC synchronization, editorial review, evidence-gap discovery, autopilot policy gates, and model-enriched bilingual summaries.',
            fr: 'Ajout d\'un espace de travail des opérations de documentation avec la synchronisation Crossref et Europe PMC, la révision éditoriale, la découverte des lacunes dans les preuves, les portes de politique de pilote automatique et les résumés bilingues enrichis de modèles.',
            zh: '新增文献运营工作区，支持 Crossref 与 Europe PMC 同步、编辑复核、证据缺口发现、自动策略门控以及模型增强的中英文摘要。',
          },
          {
            en: 'Introduced Situation Room v2 with daily, weekly, and monthly snapshots, a dedicated history database, methodology pages, public/shadow preview controls, and richer signal detail pages.',
            fr: 'Présentation de Situation Room v2 avec des instantanés quotidiens, hebdomadaires et mensuels, une base de données d\'historique dédiée, des pages de méthodologie, des contrôles d\'aperçu public/fantôme et des pages de détails de signal plus riches.',
            zh: '推出态势室 v2，支持日、周、月快照、专用历史数据库、方法页、公开/影子预览控制以及更丰富的信号详情页面。',
          },
          {
            en: 'Added Austria and Germany source mappings, reviewed mapping registries, expanded provisional-source fixtures, and migrations for situation history, source tasks, and literature evidence gaps.',
            fr: 'Ajout de mappages de sources en Autriche et en Allemagne, examen des registres de cartographie, extension des installations de sources provisoires et migrations pour l\'historique de la situation, les tâches à la source et les lacunes dans les preuves documentaires.',
            zh: '新增奥地利和德国来源映射、已复核映射注册表、扩展的临时来源测试夹具，并加入态势历史、来源任务和文献证据缺口迁移。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Upgraded epidemic curves with monitor, compare, and outbreak analysis modes, comparability safeguards, stable series colors, historical reference bands, event markers, and provisional-period treatment.',
            fr: 'Amélioration des courbes épidémiques avec des modes de surveillance, de comparaison et d\'analyse des épidémies, des garanties de comparabilité, des couleurs de séries stables, des bandes de référence historiques, des marqueurs d\'événements et un traitement provisoire.',
            zh: '升级流行曲线，新增监测、比较和暴发分析模式，并加入可比性保护、稳定序列配色、历史参考带、事件标记和临时数据期间处理。',
          },
          {
            en: 'Expanded Situation Room scoring with respiratory, increasing, emerging, and unusual sections, priority queues, source freshness checks, quality gates, and SEO-safe public publication rules.',
            fr: 'Score de salle de situation élargi avec des sections respiratoires, croissantes, émergentes et inhabituelles, des files d\'attente prioritaires, des contrôles de fraîcheur à la source, des barrières de qualité et des règles de publication publique sécurisées pour le référencement.',
            zh: '扩展态势室评分，覆盖呼吸道、上升、新发和异常栏目，并加入优先队列、来源新鲜度检查、质量门控和 SEO 安全的公开发布规则。',
          },
          {
            en: 'Strengthened disease mapping automation with retry windows, provider cooldowns, digest notifications, source-category reconciliation, and safer AI-assisted review workflows.',
            fr: 'Automatisation renforcée de la cartographie des maladies avec des fenêtres de nouvelle tentative, des temps de recharge des fournisseurs, des notifications de résumé, une réconciliation des catégories de sources et des flux de travail d\'examen assistés par l\'IA plus sûrs.',
            zh: '加强疾病映射自动化，支持重试窗口、供应商冷却、摘要通知、来源类别对账以及更安全的 AI 辅助复核流程。',
          },
          {
            en: 'Improved data release publishing with parallel raw/archive publishers, resumable GitHub pushes, SSH-over-443 fallback, atomic site-data writes, and expanded repository-boundary documentation.',
            fr: 'Amélioration de la publication des données avec des éditeurs RAW/archive parallèles, des pushs GitHub pouvant être repris, un repli SSH-over-443, des écritures atomiques de données de site et une documentation de limite de référentiel étendue.',
            zh: '优化数据发布，支持原始归档与下载发布器并行、可恢复的 GitHub 推送、SSH 443 端口回退、原子化站点数据写入以及更完整的仓库边界文档。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Kept Research Radar public JSON limited to published, integrity-safe metadata and GIDS-authored summaries; raw abstracts and provider payloads stay outside the static site.',
            fr: 'Gardé le JSON public de Research Radar limité aux métadonnées publiées et sûres pour l\'intégrité et aux résumés rédigés par GIDS ; les résumés bruts et les charges utiles des fournisseurs restent en dehors du site statique.',
            zh: '确保研究雷达公开 JSON 只包含已发布且完整性安全的元数据与 GIDS 自有摘要，原始摘要和供应商响应不会进入静态站点。',
          },
          {
            en: 'Added regression coverage for Situation Room v2 statistics, history services, provisional ingestion policies, literature radar flows, task-log compaction, sitemap entries, and expanded source processors.',
            fr: 'Ajout d\'une couverture de régression pour les statistiques de la salle de situation v2, les services d\'historique, les politiques d\'ingestion provisoires, les flux radar de la littérature, le compactage des journaux de tâches, les entrées de sitemap et les processeurs source étendus.',
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
    titleFr: 'Une couverture de surveillance plus large et une expérience plus claire des données publiques',
    titleZh: '扩展监测覆盖，并改进公开数据体验',
    summaryEn:
      'This release adds new European source pipelines, expands public discovery with situation pages and multilingual routes, and gives operators stronger source, settings, and disease-mapping workflows.',
    summaryFr: 'Cette version ajoute de nouveaux pipelines de sources européens, étend la découverte publique avec des pages de situation et des itinéraires multilingues, et donne aux opérateurs des flux de travail de source, de paramètres et de cartographie des maladies plus solides.',
    summaryZh:
      '本次更新新增欧洲来源接入，利用态势页面和多语言路由扩展公开数据发现能力，并为运营人员提供更完善的来源、设置和疾病映射工作流。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added Austria AGES Radar, Germany RKI SurvStat, and Ireland HPSC source definitions, crawlers, processors, reporting policies, and regression coverage.',
            fr: 'Ajout des définitions de sources, des robots d\'exploration, des processeurs, des politiques de reporting et de la couverture de régression Autriche AGES Radar, Allemagne RKI SurvStat et Irlande HPSC.',
            zh: '新增奥地利 AGES Radar、德国 RKI SurvStat 和爱尔兰 HPSC 的来源定义、抓取器、处理器、报告策略及回归测试。',
          },
          {
            en: 'Added a public Situation Room with weekly pages, disease summaries, surveillance notes, and dedicated data exports.',
            fr: 'Ajout d\'une salle de situation publique avec des pages hebdomadaires, des résumés de maladies, des notes de surveillance et des exportations de données dédiées.',
            zh: '新增公开态势中心，提供周度页面、疾病摘要、监测注释和专用数据导出。',
          },
          {
            en: 'Added multilingual Chinese routes, country-disease pages, custom 404 handling, segmented sitemaps, and richer SEO page metadata.',
            fr: 'Ajout d\'itinéraires chinois multilingues, de pages pays-maladie, d\'une gestion 404 personnalisée, de plans de site segmentés et de métadonnées de page SEO plus riches.',
            zh: '新增中文多语言路由、国家疾病页面、自定义 404、分片站点地图和更完整的 SEO 页面元数据。',
          },
          {
            en: 'Added an AI-assisted disease-mapping workspace with registry, audit, automation, and notification services in the operations dashboard.',
            fr: 'Ajout d\'un espace de travail de cartographie des maladies assistée par IA avec des services de registre, d\'audit, d\'automatisation et de notification dans le tableau de bord des opérations.',
            zh: '在运营控制面板新增 AI 辅助疾病映射工作区，以及注册表、审计、自动化和通知服务。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Expanded country and source policy configuration with revision windows, fill-missing behavior, source aliases, licensing states, and permission-gated release controls.',
            fr: 'Configuration étendue de la stratégie de pays et de la stratégie source avec des fenêtres de révision, un comportement de remplissage manquant, des alias de source, des états de licence et des contrôles de publication avec autorisation.',
            zh: '扩展国家与来源策略配置，支持修订窗口、缺失填补、来源别名、授权状态和权限控制的发布策略。',
          },
          {
            en: 'Reworked the dashboard settings, automation, and source flows to expose operational state and configuration more consistently.',
            fr: 'A retravaillé les paramètres du tableau de bord, l\'automatisation et les flux source pour exposer l\'état opérationnel et la configuration de manière plus cohérente.',
            zh: '重构控制面板的设置、自动化和来源流程，更一致地展示运营状态与配置。',
          },
          {
            en: 'Improved static-site builds, redirects, analytics configuration, country coverage metadata, and report-page navigation for the expanded public routes.',
            fr: 'Amélioration des constructions de sites statiques, des redirections, de la configuration des analyses, des métadonnées de couverture des pays et de la navigation des pages de rapport pour les itinéraires publics étendus.',
            zh: '改进静态站点构建、重定向、分析配置、国家覆盖元数据和扩展公开路由后的报告页导航。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed source-scope canonicalization and legacy-access safeguards so newly ingested categories remain separated from reviewed compatibility projections.',
            fr: 'Correction de la canonisation de la portée de la source et des garanties d\'accès hérité afin que les catégories nouvellement ingérées restent séparées des projections de compatibilité examinées.',
            zh: '修复来源范围规范化和旧版访问保护，确保新接入类别与经审核的兼容投影保持分离。',
          },
          {
            en: 'Added regression coverage for European source scopes, dynamic month policies, settings behavior, situation pages, disease mappings, and surveillance-note overrides.',
            fr: 'Ajout d\'une couverture de régression pour les périmètres sources européens, les politiques de mois dynamiques, le comportement des paramètres, les pages de situation, les mappages de maladies et les remplacements de notes de surveillance.',
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
    titleFr: 'Livraison plus rapide des données et exportations incrémentielles',
    titleZh: '更快的数据加载与增量导出',
    summaryEn:
      'This release reduces initial country-page work, serves compressed static assets with explicit cache policy, and makes public download exports incremental, atomic, and parallel while retaining the newly added surveillance regions.',
    summaryFr: 'Cette version réduit le travail initial de la page de pays, sert les actifs statiques compressés avec une politique de cache explicite et rend les exportations de téléchargement publiques incrémentielles, atomiques et parallèles tout en conservant les régions de surveillance nouvellement ajoutées.',
    summaryZh:
      '本次更新降低国家/地区页面的首次加载负担，为静态资源提供压缩与明确缓存策略，并将公开下载导出改为增量、原子和并行处理，同时保留近期新增的监测地区。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added Ontario, Finland, Iceland, Norway, and Sweden to the public surveillance coverage released in the 0.5 series.',
            fr: 'Ajout de l\'Ontario, de la Finlande, de l\'Islande, de la Norvège et de la Suède à la couverture de surveillance publique publiée dans la série 0.5.',
            zh: '将安大略、芬兰、冰岛、挪威和瑞典纳入 0.5 系列已发布的公开监测覆盖范围。',
          },
          {
            en: 'Added a lazy source-series payload for country trend charts, preserving source selection without embedding complete observations in the initial HTML document.',
            fr: 'Ajout d\'une charge utile de série source paresseuse pour les graphiques de tendances par pays, préservant la sélection des sources sans intégrer des observations complètes dans le document HTML initial.',
            zh: '为国家趋势图新增按需加载的来源序列数据，在保留来源选择功能的同时，不再把完整观测值嵌入初始 HTML 文档。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'The static site origin now serves gzip-compressed text assets with cache headers for hashed build files, mutable site data, and HTML documents.',
            fr: 'L\'origine du site statique sert désormais des ressources textuelles compressées gzip avec des en-têtes de cache pour les fichiers de compilation hachés, les données de site mutables et les documents HTML.',
            zh: '静态站点源站现为文本资源提供 gzip 压缩，并分别为带哈希构建文件、可变站点数据和 HTML 文档设置缓存策略。',
          },
          {
            en: 'Country heatmaps now reuse the precomputed export instead of rebuilding all month-by-disease cells in the browser.',
            fr: 'Les cartes thermiques de pays réutilisent désormais l\'exportation précalculée au lieu de reconstruire toutes les cellules mois par maladie dans le navigateur.',
            zh: '国家热图现直接复用预计算导出结果，不再在浏览器中重建全部“月份 × 疾病”单元格。',
          },
          {
            en: 'Site JSON generation now preserves unchanged files, replaces changed files atomically, and removes stale artifacts only after a successful write pass.',
            fr: 'La génération JSON du site conserve désormais les fichiers inchangés, remplace atomiquement les fichiers modifiés et supprime les artefacts obsolètes uniquement après une écriture réussie.',
            zh: '站点 JSON 生成现会保留未变化文件、原子替换已变化文件，并仅在成功写入后清理过期产物。',
          },
          {
            en: 'Changed CSV, JSON, and XLSX download partitions now render in parallel while historical partitions continue to be reused by content hash.',
            fr: 'Les partitions de téléchargement CSV, JSON et XLSX modifiées sont désormais rendues en parallèle tandis que les partitions historiques continuent d\'être réutilisées par le hachage de contenu.',
            zh: '发生变化的 CSV、JSON 和 XLSX 下载分区现可并行生成，而历史分区继续按内容哈希复用。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Deferred below-the-fold disease charts until they approach the viewport, reducing unnecessary JavaScript work during initial navigation.',
            fr: 'Différé les graphiques de maladies sous le pli jusqu\'à ce qu\'ils s\'approchent de la fenêtre d\'affichage, réduisant ainsi le travail JavaScript inutile lors de la navigation initiale.',
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
    titleFr: 'Cinq nouvelles régions de surveillance et des notes sources plus claires',
    titleZh: '新增五个监测地区，并改进来源注释呈现',
    summaryEn:
      'This release expands public surveillance coverage to Ontario, Finland, Iceland, Norway, and Sweden, strengthens disease-source mapping safeguards, and moves complex source notes out of chart legends into a dedicated data-notes area.',
    summaryFr: 'Cette version étend la couverture de la surveillance publique à l\'Ontario, à la Finlande, à l\'Islande, à la Norvège et à la Suède, renforce les garanties de cartographie des sources de maladies et déplace les notes sources complexes des légendes des graphiques vers une zone dédiée aux notes de données.',
    summaryZh:
      '本次更新将公开监测覆盖扩展至安大略、芬兰、冰岛、挪威和瑞典，加强疾病来源映射保护，并把复杂来源说明从图例中移至专门的数据注释区。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added first-class ingestion, mapping, source-series registration, tests, and public country pages for Ontario, Finland, Iceland, Norway, and Sweden.',
            fr: 'Ajout de pages publiques sur l\'ingestion, la cartographie, l\'enregistrement des séries sources, les tests et les pays pour l\'Ontario, la Finlande, l\'Islande, la Norvège et la Suède.',
            zh: '为安大略、芬兰、冰岛、挪威和瑞典新增一等接入能力，包括抓取、映射、来源序列注册、测试和公开国家/地区页面。',
          },
          {
            en: 'Added dynamic monthly/current-period controls for supported Nordic sources, including provisional current-month handling and revision-window refresh options.',
            fr: 'Ajout de contrôles dynamiques mensuels/de la période en cours pour les sources nordiques prises en charge, y compris les options provisoires de traitement du mois en cours et d\'actualisation de la fenêtre de révision.',
            zh: '为支持的北欧来源新增动态月度/当前期间控制，包括临时当前月处理和修订窗口刷新选项。',
          },
          {
            en: 'Added SEO-oriented country, disease, report, sitemap, and structured-data helpers so public pages are easier for search engines to discover.',
            fr: 'Ajout d\'aides orientées SEO pour les pays, les maladies, les rapports, les sitemaps et les données structurées afin que les pages publiques soient plus faciles à découvrir pour les moteurs de recherche.',
            zh: '新增面向 SEO 的国家、疾病、报告、站点地图和结构化数据工具，使公开页面更容易被搜索引擎发现。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Strengthened disease ontology and mapping rules for mixed-grain, non-additive, antimicrobial-resistance, STI, respiratory, and historical workbook series.',
            fr: 'Renforcement de l\'ontologie des maladies et des règles de cartographie pour les séries de cahiers d\'exercices mixtes, non additifs, résistants aux antimicrobiens, aux IST, respiratoires et historiques.',
            zh: '加强混合粒度、不可加总、耐药监测、性传播感染、呼吸道监测和历史工作簿序列的疾病本体与映射规则。',
          },
          {
            en: 'Updated the operations dashboard source flow to show source policy, availability, current-period support, revision windows, and Iceland history-source constraints consistently.',
            fr: 'Mise à jour du flux source du tableau de bord des opérations pour afficher de manière cohérente la politique source, la disponibilité, la prise en charge de la période en cours, les fenêtres de révision et les contraintes histoire-source de l\'Islande.',
            zh: '更新运营控制面板的来源流程，一致展示来源策略、可用性、当前期间支持、修订窗口和冰岛历史来源限制。',
          },
          {
            en: 'Kept source-series observations authoritative while documenting each remaining legacy compatibility projection in the reviewed access baseline.',
            fr: 'A conservé les observations de la série source faisant autorité tout en documentant chaque projection de compatibilité héritée restante dans la base de référence d\'accès révisée.',
            zh: '保持来源序列观测为权威层，并在已审查访问基线中记录仍需保留的旧版兼容投影。',
          },
          {
            en: 'Moved verbose chart source definitions, reporting basis, availability, and aggregation policy into a bottom data-notes panel instead of overloading the legend.',
            fr: 'Déplacement des définitions de source de graphique verbeux, de la base de rapport, de la disponibilité et de la politique d\'agrégation dans un panneau de notes de données du bas au lieu de surcharger la légende.',
            zh: '将冗长的图表来源定义、报告口径、可用状态和聚合策略移入底部数据注释栏，不再挤入图例。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Fixed the epidemic-curve frame so a selector sidebar no longer hides the legend; legends now remain visible in a compact footer when both are present.',
            fr: 'Correction du cadre de la courbe épidémique de sorte qu\'une barre latérale de sélection ne cache plus la légende ; les légendes restent désormais visibles dans un pied de page compact lorsque les deux sont présentes.',
            zh: '修复流行曲线框架中筛选侧栏会隐藏图例的问题；当筛选器与图例同时存在时，图例会以紧凑底栏呈现。',
          },
          {
            en: 'Updated ontology-export tests and legacy-access guard baselines for the expanded registry so full repository validation passes cleanly.',
            fr: 'Mise à jour des tests d\'exportation d\'ontologie et des lignes de base de garde d\'accès hérité pour le registre étendu afin que la validation complète du référentiel passe proprement.',
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
    titleFr: 'Graphiques plus propres et navigation plus fluide dans le temps',
    titleZh: '更清爽的图表与更顺畅的时间导航',
    summaryEn:
      'This release streamlines chart presentation, makes epidemic-curve time navigation faster and easier to control, and keeps charts and selectors precisely aligned across standard and full-screen views.',
    summaryFr: 'Cette version rationalise la présentation des graphiques, rend la navigation dans le temps des courbes épidémiques plus rapide et plus facile à contrôler, et maintient les graphiques et les sélecteurs alignés avec précision sur les vues standard et plein écran.',
    summaryZh:
      '本次更新精简图表呈现，提升流行曲线时间范围操作的速度与易用性，并让普通及全屏模式下的图表与筛选器保持精确对齐。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added a dedicated reset control beside the epidemic-curve time slider to restore the complete reporting period in one click.',
            fr: 'Ajout d\'un contrôle de réinitialisation dédié à côté du curseur de temps de la courbe épidémique pour restaurer la période de rapport complète en un clic.',
            zh: '在流行曲线时间控制条旁新增重置按钮，可一键恢复完整报告周期。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Redesigned epidemic-curve time navigation with clearer drag handles, live visual feedback, smoother state synchronization, range panning, brush selection, and deliberate Ctrl-wheel zooming.',
            fr: 'Navigation dans le temps des courbes épidémiques repensée avec des poignées de glissement plus claires, un retour visuel en direct, une synchronisation d\'état plus fluide, un panoramique de portée, une sélection au pinceau et un zoom délibéré de la molette Ctrl.',
            zh: '重新设计流行曲线时间导航，提供更清晰的拖拽手柄、实时视觉反馈、更顺畅的状态同步、区间平移、框选以及需按住 Ctrl 的滚轮缩放。',
          },
          {
            en: 'Removed redundant chart instructions and implementation notes while retaining actionable controls, legends, dates, and data summaries.',
            fr: 'Suppression des instructions redondantes du graphique et des notes de mise en œuvre tout en conservant les contrôles, légendes, dates et résumés de données exploitables.',
            zh: '移除冗余的图表操作说明和实现提示，同时保留可操作控件、图例、日期及数据摘要。',
          },
          {
            en: 'Unified the visual alignment of chart plotting areas, time controls, reset actions, and selector panels in standard and full-screen layouts.',
            fr: 'Unification de l\'alignement visuel des zones de traçage des graphiques, des contrôles de temps, des actions de réinitialisation et des panneaux de sélection dans les mises en page standard et plein écran.',
            zh: '统一普通与全屏布局中绘图区、时间控件、重置操作和筛选面板的视觉对齐。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Removed the extra 60-pixel space below monthly pattern charts that occurred when the year selector made the grid row taller than the chart.',
            fr: 'Suppression de l\'espace supplémentaire de 60 pixels sous les graphiques mensuels qui s\'est produit lorsque le sélecteur d\'année a rendu la ligne de grille plus haute que le graphique.',
            zh: '修复年份筛选器将网格行撑高后，月度变化图下方多出 60 像素空白的问题。',
          },
          {
            en: 'Corrected bottom-edge mismatches between standard-view charts, scrollable selectors, and the epidemic-curve time slider.',
            fr: 'Correction des décalages de bord inférieur entre les graphiques à affichage standard, les sélecteurs déroulants et le curseur temporel de la courbe épidémique.',
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
    titleFr: 'Périodes de déclaration précises et couverture des sources plus forte',
    titleZh: '更准确的报告周期与更完善的知识来源',
    summaryEn:
      'This release aligns surveillance records by their real weekly, monthly, or annual reporting period, restores population denominators automatically, and improves discovery of reviewed official disease sources.',
    summaryFr: 'Cette version aligne les dossiers de surveillance sur leur période de déclaration hebdomadaire, mensuelle ou annuelle réelle, rétablit automatiquement les dénominateurs de population et améliore la découverte des sources officielles de maladies examinées.',
    summaryZh:
      '本次更新按真实的周、月或年报告周期对齐监测记录，自动恢复人口分母数据，并改进经审核官方疾病来源的发现能力。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added a shared reporting-period model that identifies weekly records by ISO week, monthly records by month, and annual records by year.',
            fr: 'Ajout d\'un modèle de période de rapport partagé qui identifie les enregistrements hebdomadaires par semaine ISO, les enregistrements mensuels par mois et les enregistrements annuels par année.',
            zh: '新增共享报告周期模型，分别使用 ISO 周、月份和年份识别周报、月报与年报记录。',
          },
          {
            en: 'Added configurable, reviewed disease-source hints with aliases and prioritized official URLs.',
            fr: 'Ajout d\'indices configurables et revus sur la source de la maladie avec des alias et des URL officielles classées par ordre de priorité.',
            zh: '新增可配置、经审核的疾病来源提示，支持别名与优先官方链接。',
          },
          {
            en: 'Added regression tests for reporting-period alignment, population imports, site projections, and disease knowledge source hints.',
            fr: 'Ajout de tests de régression pour l\'alignement de la période de reporting, les importations de population, les projections de site et les indices de source de connaissances sur les maladies.',
            zh: '新增报告周期对齐、人口数据导入、站点投影和疾病知识来源提示的回归测试。',
          },
          {
            en: 'Added public CSV, JSON, and XLSX downloads partitioned into stable time windows for every country and disease dataset.',
            fr: 'Ajout de téléchargements publics CSV, JSON et XLSX partitionnés en fenêtres temporelles stables pour chaque pays et ensemble de données sur les maladies.',
            zh: '为每个国家和疾病数据集新增按稳定时间窗口分块的 CSV、JSON 和 XLSX 公开下载。',
          },
          {
            en: 'Added a versioned download manifest containing record ranges, file sizes, SHA-256 checksums, and direct public URLs.',
            fr: 'Ajout d\'un manifeste de téléchargement versionné contenant des plages d\'enregistrements, des tailles de fichiers, des sommes de contrôle SHA-256 et des URL publiques directes.',
            zh: '新增带版本的下载清单，记录数据范围、文件大小、SHA-256 校验值与直接公开链接。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Aligned registry and legacy surveillance layers by source reporting period instead of requiring identical calendar dates.',
            fr: 'Alignement des couches de surveillance du registre et de l\'héritage par période de rapport source au lieu d\'exiger des dates calendaires identiques.',
            zh: '将新版序列与旧版监测层按来源报告周期对齐，不再要求日历日期完全相同。',
          },
          {
            en: 'Site generation now idempotently restores UN World Population Prospects denominators after database or country rebuilds.',
            fr: 'La génération de sites restaure maintenant de manière idempotente les dénominateurs des Perspectives de la population mondiale des Nations Unies après la reconstruction de la base de données ou du pays.',
            zh: '站点生成现会在数据库或国家数据重建后幂等恢复联合国世界人口展望分母数据。',
          },
          {
            en: 'Disease knowledge discovery now uses ontology labels, local source labels and codes, configured aliases, and reviewed official entry pages.',
            fr: 'La découverte des connaissances sur les maladies utilise désormais des étiquettes d\'ontologie, des étiquettes et des codes de sources locales, des alias configurés et des pages d\'entrée officielles révisées.',
            zh: '疾病知识来源发现现会结合本体标签、本地来源名称与编码、配置别名及经审核官方入口页。',
          },
          {
            en: 'Redesigned the download interface around time-range cards, format choices, file sizes, source details, and a centered bilingual modal.',
            fr: 'Repensé l\'interface de téléchargement autour de cartes de plage horaire, de choix de format, de tailles de fichiers, de détails sur les sources et d\'un modal bilingue centré.',
            zh: '重新设计下载界面，通过时间范围卡片展示格式选择、文件大小和来源详情，并提供居中的双语弹窗。',
          },
          {
            en: 'Moved public data publishing to a dedicated repository with incremental synchronization and validation before the site is deployed.',
            fr: 'Déplacement de la publication des données publiques vers un référentiel dédié avec synchronisation et validation incrémentielles avant le déploiement du site.',
            zh: '将公开数据发布迁移到独立数据仓库，支持增量同步，并在站点部署前完成验证。',
          },
          {
            en: 'Separated normal Astro builds from data regeneration so interface-only builds no longer rewrite generated datasets.',
            fr: 'Les constructions Astro normales séparées de la régénération des données, de sorte que les constructions à interface seule ne réécrivent plus les ensembles de données générés.',
            zh: '将普通 Astro 构建与数据重新生成分离，纯界面构建不再重写已生成数据。',
          },
          {
            en: 'Simplified public-facing version and data copy, made the maintenance notice opt-in, and improved translated form placeholders.',
            fr: 'Version et copie des données publiques simplifiées, activation de l\'avis de maintenance et amélioration des espaces réservés pour les formulaires traduits.',
            zh: '简化面向公众的版本与数据文案，将维护提示改为按需显示，并改进表单占位文字的双语切换。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Prevented duplicate or missing points when the same surveillance week is represented by different dates, such as Saturday and Sunday.',
            fr: 'Prévention des doublons ou des points manquants lorsqu\'une même semaine de surveillance est représentée par des dates différentes, comme le samedi et le dimanche.',
            zh: '修复同一监测周使用不同日期（如周六与周日）表示时可能出现的重复或缺失数据点。',
          },
          {
            en: 'Preserved legacy deaths, recoveries, mortality rates, and coverage gaps without reintroducing duplicate case counts.',
            fr: 'Préservation des décès hérités, des recouvrements, des taux de mortalité et des lacunes de couverture sans réintroduire le nombre de cas en double.',
            zh: '在不重复计算病例数的前提下，保留旧版死亡、康复、病死率与覆盖缺口数据。',
          },
          {
            en: 'Prevented incidence calculations from losing population denominators when country identifiers are recreated during rebuilds.',
            fr: 'Empêché les calculs d\'incidence de perdre des dénominateurs de population lorsque les identificateurs de pays sont recréés pendant les reconstructions.',
            zh: '修复重建过程中国家标识重新生成后，发病率计算可能丢失人口分母的问题。',
          },
          {
            en: 'Ensured CSV and JSON actions trigger browser downloads instead of unexpectedly opening raw files in a new tab, with a direct-link fallback.',
            fr: 'Les actions CSV et JSON déclenchent les téléchargements du navigateur au lieu d\'ouvrir de manière inattendue des fichiers bruts dans un nouvel onglet, avec un repli de lien direct.',
            zh: '确保 CSV 和 JSON 操作触发浏览器下载，而不是意外在新标签页打开原始文件，并保留直接链接回退。',
          },
          {
            en: 'Prevented a site deployment from publishing links to data files that failed repository synchronization or integrity checks.',
            fr: 'Empêché un déploiement de site de publier des liens vers des fichiers de données qui ont échoué à la synchronisation du référentiel ou aux vérifications d\'intégrité.',
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
    titleFr: 'Archives résilientes et préparation à React 19',
    titleZh: '更可靠的数据归档与 React 19 兼容',
    summaryEn:
      'This release adds recoverable raw-data archives, completes the public site’s modern frontend migration, and separates core pipelines into smaller, testable modules.',
    summaryFr: 'Cette version ajoute des archives de données brutes récupérables, complète la migration frontale moderne du site public et sépare les pipelines de base en modules plus petits et testables.',
    summaryZh:
      '本次更新新增可恢复的原始数据归档，完成公开站点的现代前端迁移，并将核心流程拆分为更小、可测试的模块。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added automated raw-data archiving with content-addressed compressed objects, immutable snapshot manifests, and point-in-time restoration.',
            fr: 'Ajout de l\'archivage automatisé des données brutes avec des objets compressés adressés par contenu, des manifestes d\'instantanés immuables et une restauration ponctuelle.',
            zh: '新增原始数据自动归档，支持内容寻址压缩对象、不可变快照清单与按时点恢复。',
          },
          {
            en: 'Added dedicated validation and tests for archive integrity, interrupted uploads, and first-time publishing.',
            fr: 'Ajout d\'une validation et de tests dédiés pour l\'intégrité des archives, les téléchargements interrompus et la première publication.',
            zh: '新增归档完整性、上传中断续传与首次发布的专项验证和测试。',
          },
          {
            en: 'Added focused test suites for site-data generation, country crawl pipelines, agent workflows, and database rebuild planning.',
            fr: 'Ajout de suites de tests ciblées pour la génération de données de site, les pipelines d\'exploration de pays, les flux de travail des agents et la planification de la reconstruction de la base de données.',
            zh: '新增站点数据生成、国家采集流程、Agent 工作流与数据库重建计划的专项测试。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Completed the public site migration to React 19, Tailwind CSS 4, and Marked 18 while retaining Astro 7 and ECharts 6.1.',
            fr: 'Achèvement de la migration du site public vers React 19, Tailwind CSS 4 et Marked 18 tout en conservant Astro 7 et ECharts 6.1.',
            zh: '完成公开站点向 React 19、Tailwind CSS 4 与 Marked 18 的迁移，并继续使用 Astro 7 和 ECharts 6.1。',
          },
          {
            en: 'Separated static site exports into query, view-building, file-writing, and series-projection layers for safer maintenance.',
            fr: 'Exportations de sites statiques séparées dans les couches de requête, de création de vues, d\'écriture de fichiers et de projection en série pour une maintenance plus sûre.',
            zh: '将静态站点导出拆分为查询、视图构建、文件写入和时间序列投影层，降低维护风险。',
          },
          {
            en: 'Modularized country crawling, agent workflow helpers, subscription email handling, and database rebuild planning.',
            fr: 'Exploration modulaire des pays, assistants de flux de travail des agents, gestion des e-mails d\'abonnement et planification de la reconstruction de la base de données.',
            zh: '对国家数据采集、Agent 工作流辅助逻辑、订阅邮件处理与数据库重建计划进行模块化。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Normalized the ECharts React module export to prevent invalid component errors under React 19 and different Vite bundling paths.',
            fr: 'Normalisation de l\'exportation du module ECharts React pour éviter les erreurs de composants invalides sous React 19 et différents chemins de regroupement Vite.',
            zh: '统一 ECharts React 模块导出形式，避免在 React 19 及不同 Vite 打包路径下出现无效组件错误。',
          },
          {
            en: 'Hardened archive resume behavior, chunk verification, and restore paths against incomplete uploads and unsafe symbolic links.',
            fr: 'Comportement de reprise des archives durci, vérification des blocs et restauration des chemins contre les téléchargements incomplets et les liens symboliques dangereux.',
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
    titleFr: 'Une plateforme modernisée et reproductible',
    titleZh: '现代化且更易复现的平台基础',
    summaryEn:
      'This release modernizes the public site and management dashboard, while making application dependencies more predictable, secure, and easier to maintain.',
    summaryFr: 'Cette version modernise le site public et le tableau de bord de gestion, tout en rendant les dépendances des applications plus prévisibles, sécurisées et plus faciles à maintenir.',
    summaryZh:
      '本次更新对公开站点与管理后台进行技术栈升级，同时让应用依赖更可预期、更安全且更易维护。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added an automated Astro type-checking command to catch page and component issues before release.',
            fr: 'Ajout d\'une commande de vérification de type Astro automatisée pour détecter les problèmes de page et de composant avant la publication.',
            zh: '新增 Astro 类型检查命令，在发布前发现页面与组件问题。',
          },
          {
            en: 'Introduced a direct Python dependency manifest and a fully pinned generated lock file for reproducible environments.',
            fr: 'Introduction d\'un manifeste de dépendance directe à Python et d\'un fichier de verrouillage généré entièrement épinglé pour les environnements reproductibles.',
            zh: '新增 Python 直接依赖清单与完整锁定的生成文件，便于稳定复现运行环境。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Upgraded the public site to Astro 7, the latest React integration, ECharts 6.1, and modern Tailwind/PostCSS configuration.',
            fr: 'Mise à niveau du site public vers Astro 7, la dernière intégration React, ECharts 6.1 et la configuration moderne Tailwind/PostCSS.',
            zh: '公开站点升级至 Astro 7、新版 React 集成、ECharts 6.1 与现代化 Tailwind/PostCSS 配置。',
          },
          {
            en: 'Upgraded the management dashboard to Next.js 16.3 and React 19.2.8.',
            fr: 'Mise à niveau du tableau de bord de gestion vers Next.js 16.3 et React 19.2.8.',
            zh: '管理后台升级至 Next.js 16.3 与 React 19.2.8。',
          },
          {
            en: 'Replaced the external Tremor dependency with lightweight local UI primitives while preserving cards, badges, grids, buttons, progress indicators, and dark mode.',
            fr: 'Remplacement de la dépendance Tremor externe par des primitives d\'interface utilisateur locales légères tout en préservant les cartes, les badges, les grilles, les boutons, les indicateurs de progression et le mode sombre.',
            zh: '使用轻量本地 UI 组件替代外部 Tremor 依赖，并保留卡片、标签、网格、按钮、进度显示和深色模式。',
          },
          {
            en: 'Updated and pinned the Python service stack, including current web, data-processing, AI, crawling, and testing libraries.',
            fr: 'Mise à jour et épinglage de la pile de services Python, y compris les bibliothèques Web, de traitement de données, d\'IA, d\'exploration et de test actuelles.',
            zh: '更新并锁定 Python 服务依赖，覆盖 Web、数据处理、AI、数据采集与测试工具。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened country and report routes when a country code is missing during static generation.',
            fr: 'Pays durci et itinéraires de rapport lorsqu\'un code de pays est manquant lors de la génération statique.',
            zh: '加强静态生成时国家代码缺失情况下的国家与报告路由处理。',
          },
          {
            en: 'Aligned download metadata types with the generated data format and hardened transitive HTTP dependency resolution.',
            fr: 'Alignement des types de métadonnées de téléchargement avec le format de données généré et la résolution de dépendance HTTP transitive renforcée.',
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
    titleFr: 'Correction des délais de surveillance en Corée',
    titleZh: '修正韩国疾病监测时间序列',
    summaryEn:
      'This patch corrects Korea KDCA monthly surveillance data and adds safeguards against shifted columns, incomplete batches, and false January spikes.',
    summaryFr: 'Ce correctif corrige les données de surveillance mensuelles de la KDCA en Corée et ajoute des garanties contre les colonnes décalées, les lots incomplets et les faux pics de janvier.',
    summaryZh:
      '本次修复更正韩国 KDCA 月度监测数据，并增加字段错位、不完整批次和虚假一月高峰的质量保护。',
    sections: [
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Corrected the KDCA EDW field mapping: COLUMN1 is the annual or year-to-date total, while COLUMN2–COLUMN13 represent January through December.',
            fr: 'Correction de la cartographie de terrain EDW de KDCA : la COLONNE1 est le total annuel ou cumulatif, tandis que COLUMN2-COLUMN13 représente janvier à décembre.',
            zh: '修正 KDCA EDW 字段映射：COLUMN1 为全年或年内累计值，COLUMN2–COLUMN13 才对应一月至十二月。',
          },
          {
            en: 'Rebuilt Korea monthly history from the official source for January 2001 through August 2026, removing the systematic false January peaks.',
            fr: 'Reconstruire l\'histoire mensuelle de la Corée à partir de la source officielle de janvier 2001 à août 2026, en supprimant les faux pics systématiques de janvier.',
            zh: '依据官方来源重建 2001 年 1 月至 2026 年 8 月的韩国月度历史数据，消除系统性的虚假一月高峰。',
          },
          {
            en: 'Added mappings for the current aggregate syphilis and Nipah virus infection categories reported by KDCA.',
            fr: 'Ajout de mappages pour les catégories d\'infection agrégées actuelles par la syphilis et le virus Nipah signalées par le KDCA.',
            zh: '补充 KDCA 当前上报的梅毒汇总类别和尼帕病毒感染症映射。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Hardened Korea imports with support for named month fields, generic monthly columns, and packed DATAARRTXT values.',
            fr: 'Importations de Corée durcies avec prise en charge des champs de mois nommés, des colonnes mensuelles génériques et des valeurs DATAARRTXT emballées.',
            zh: '增强韩国数据导入，兼容命名月份字段、通用月度列及 DATAARRTXT 压缩值。',
          },
          {
            en: 'Added validation for all-zero batches, missing completed months, annual totals misread as January, and negative missing-value sentinels.',
            fr: 'Ajout de la validation pour les lots à zéro, les mois terminés manquants, les totaux annuels mal lus en janvier et les sentinelles à valeur manquante négative.',
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
    titleFr: 'Une base de connaissances plus solide sur les maladies',
    titleZh: '更完善的疾病知识体系',
    summaryEn:
      'This release strengthens the data and knowledge layers behind GIDS, making disease profiles more consistent, traceable, and ready for broader country coverage.',
    summaryFr: 'Cette version renforce les couches de données et de connaissances derrière GIDS, rendant les profils de maladies plus cohérents, traçables et prêts pour une couverture nationale plus large.',
    summaryZh:
      '本次更新重点加强 GIDS 的数据与知识底层，让疾病档案更加统一、可追溯，并为覆盖更多国家和地区做好准备。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced a structured disease ontology and evidence-aware knowledge profiles.',
            fr: 'Introduction d\'une ontologie structurée des maladies et de profils de connaissances fondés sur des données probantes.',
            zh: '引入结构化疾病本体与可追溯证据的疾病知识档案。',
          },
          {
            en: 'Added quality checks for disease mappings, legacy access, and series observations.',
            fr: 'Ajout de contrôles de qualité pour les mappages de maladies, l\'accès hérité et les observations de séries.',
            zh: '新增疾病映射、旧版数据访问和序列观测数据的质量检查。',
          },
          {
            en: 'Expanded automated surveillance preparation for more countries and source formats.',
            fr: 'Élargissement de la préparation de la surveillance automatisée pour un plus grand nombre de pays et de formats sources.',
            zh: '扩展多国家、多数据源格式的自动监测准备流程。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Moved site data generation toward a series-first model for more reliable comparisons.',
            fr: 'Déplacement de la génération de données de site vers un modèle en série pour des comparaisons plus fiables.',
            zh: '站点数据生成逐步采用序列优先模型，提升跨地区比较的可靠性。',
          },
          {
            en: 'Refined disease knowledge rendering and test coverage across the public site.',
            fr: 'Rendu des connaissances sur les maladies et couverture des tests affinés sur l\'ensemble du site public.',
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
    titleFr: 'Graphiques et versions plus fiables',
    titleZh: '更稳定的图表与发布流程',
    summaryEn:
      'Chart comparisons now behave more consistently, with stronger safeguards around automated production releases.',
    summaryFr: 'Les comparaisons de graphiques se comportent désormais de manière plus cohérente, avec des garanties plus fortes concernant les versions de production automatisées.',
    summaryZh: '图表比较体验更加稳定，同时加强了自动化生产发布的保护与验证。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Stabilized epidemic curve and monthly comparison state across country reports.',
            fr: 'Courbe épidémique stabilisée et état comparatif mensuel dans les rapports nationaux.',
            zh: '优化国家报告中的流行曲线与月度比较状态管理。',
          },
          {
            en: 'Added regression tests for chart models and monthly chart options.',
            fr: 'Ajout de tests de régression pour les modèles de graphiques et les options de graphiques mensuels.',
            zh: '新增图表模型与月度图表配置的回归测试。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Hardened production publishing and post-release build verification.',
            fr: 'Édition de production durcie et vérification de la construction après la sortie.',
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
    titleFr: 'Contrôles des abonnements et des services',
    titleZh: '订阅服务与管理能力',
    summaryEn:
      'Readers can subscribe to the updates they care about, with clearer service terms and protected management tools.',
    summaryFr: 'Les lecteurs peuvent s\'abonner aux mises à jour qui les intéressent, avec des conditions de service plus claires et des outils de gestion protégés.',
    summaryZh: '读者可以订阅自己关注的更新，同时新增更清晰的服务条款和受保护的管理能力。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added email subscriptions with language, frequency, country, and disease preferences.',
            fr: 'Ajout d\'abonnements par e-mail avec les préférences en matière de langue, de fréquence, de pays et de maladie.',
            zh: '新增邮件订阅，可选择语言、频率、国家和疾病偏好。',
          },
          {
            en: 'Added subscription confirmation, secure unsubscribe links, and delivery status feedback.',
            fr: 'Ajout d\'une confirmation d\'abonnement, de liens de désabonnement sécurisés et de commentaires sur le statut de la livraison.',
            zh: '新增订阅确认、安全退订链接和邮件投递状态反馈。',
          },
          {
            en: 'Published bilingual service terms and privacy information.',
            fr: 'Publication des conditions de service bilingues et des informations de confidentialité.',
            zh: '发布中英双语服务条款与隐私说明。',
          },
        ],
      },
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Added protected subscription management and administrative notification workflows.',
            fr: 'Ajout de flux de travail protégés de gestion des abonnements et de notification administrative.',
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
    titleFr: 'Une vision plus claire du fonctionnement de GIDS',
    titleZh: '更清晰地了解 GIDS 如何运作',
    summaryEn:
      'The public site now explains its live data snapshot, processing pipeline, architecture, and source coverage in one place.',
    summaryFr: 'Le site public explique maintenant son instantané de données en direct, son pipeline de traitement, son architecture et sa couverture source en un seul endroit.',
    summaryZh: '公开站点集中展示实时数据快照、处理流程、系统架构和数据来源覆盖情况。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added a comprehensive About page with live database metrics.',
            fr: 'Ajout d\'une page À propos complète avec des métriques de base de données en direct.',
            zh: '新增完整的“关于”页面，并展示实时数据库指标。',
          },
          {
            en: 'Documented the collection pipeline, system architecture, features, and official sources.',
            fr: 'Documentation du pipeline de collecte, de l\'architecture du système, des fonctionnalités et des sources officielles.',
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
    titleFr: 'Une fondation de projet plus propre',
    titleZh: '更清晰的项目基础',
    summaryEn:
      'Internal structures were simplified to make the site easier to maintain and extend without changing its core experience.',
    summaryFr: 'Les structures internes ont été simplifiées pour faciliter la maintenance et l\'extension du site sans modifier son expérience de base.',
    summaryZh: '简化内部结构，让站点更易维护和扩展，同时保持核心使用体验不变。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Refactored site code for clearer responsibilities and easier maintenance.',
            fr: 'Code de site refactorisé pour des responsabilités plus claires et une maintenance plus facile.',
            zh: '重构站点代码，明确模块职责并降低维护成本。',
          },
          {
            en: 'Improved data handling consistency across country and disease pages.',
            fr: 'Amélioration de la cohérence de la gestion des données dans les pages de pays et de maladies.',
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
    titleFr: 'Un système visuel rafraîchi',
    titleZh: '焕新的视觉系统',
    summaryEn:
      'GIDS received a more coherent visual language for navigation, content cards, reports, and data-heavy pages.',
    summaryFr: 'GIDS a reçu un langage visuel plus cohérent pour la navigation, les cartes de contenu, les rapports et les pages riches en données.',
    summaryZh: 'GIDS 更新统一的视觉语言，覆盖导航、内容卡片、报告与数据密集型页面。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Refreshed the public site design and responsive layouts.',
            fr: 'Mise à jour de la conception du site public et des mises en page réactives.',
            zh: '更新公开站点设计与响应式布局。',
          },
          {
            en: 'Improved readability across charts, reports, and long-form disease content.',
            fr: 'Amélioration de la lisibilité des graphiques, des rapports et du contenu détaillé des maladies.',
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
    titleFr: 'Assistance pour la livraison d\'e-mails',
    titleZh: '邮件投递支持',
    summaryEn:
      'The notification system gained email delivery support and clearer routes for operational update messages.',
    summaryFr: 'Le système de notification a obtenu une assistance pour la livraison des e-mails et des itinéraires plus clairs pour les messages de mise à jour opérationnelle.',
    summaryZh: '通知系统新增邮件投递支持，并完善运行更新消息的发送方式。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Added SMTP-based email delivery for automated notifications.',
            fr: 'Ajout de la livraison d\'e-mails basée sur SMTP pour les notifications automatisées.',
            zh: '新增基于 SMTP 的自动通知邮件投递。',
          },
        ],
      },
      {
        kind: 'fixed',
        labelEn: 'Fixed',
        labelFr: 'Fixe',
        labelZh: '修复',
        items: [
          {
            en: 'Improved routing for data update error notifications.',
            fr: 'Routage amélioré pour les notifications d\'erreur de mise à jour des données.',
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
    titleFr: 'Exploration réactive des données',
    titleZh: '响应式数据浏览体验',
    summaryEn:
      'Country and disease data became easier to explore across screen sizes, with more consistent underlying structures.',
    summaryFr: 'Les données sur les pays et les maladies sont devenues plus faciles à explorer à travers les tailles d\'écran, avec des structures sous-jacentes plus cohérentes.',
    summaryZh: '国家与疾病数据在不同屏幕上更易浏览，底层结构也更加一致。',
    sections: [
      {
        kind: 'improved',
        labelEn: 'Improved',
        labelFr: 'Amélioré ',
        labelZh: '优化',
        items: [
          {
            en: 'Improved chart responsiveness and presentation on smaller screens.',
            fr: 'Amélioration de la réactivité et de la présentation des graphiques sur des écrans plus petits.',
            zh: '优化图表在小屏设备上的响应式布局与展示。',
          },
          {
            en: 'Standardized disease and country data handling across the static site.',
            fr: 'Traitement standardisé des données sur les maladies et les pays sur l\'ensemble du site statique.',
            zh: '统一静态站点中的疾病与国家数据处理方式。',
          },
          {
            en: 'Added structured metadata, robots directives, and generated sitemaps for discovery.',
            fr: 'Ajout de métadonnées structurées, de directives robots et de plans de site générés pour la découverte.',
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
    titleFr: 'Graphiques prêts à être publiés',
    titleZh: '适合发布的图表能力',
    summaryEn:
      'A new chart frame made it easier to switch between visual and tabular views while keeping report context visible.',
    summaryFr: 'Un nouveau cadre de graphique a facilité le basculement entre les vues visuelles et tabulaires tout en gardant le contexte du rapport visible.',
    summaryZh: '新的图表框架支持图形与表格视图切换，并保留完整的报告上下文。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Introduced a reusable chart frame with chart and table presentation modes.',
            fr: 'Introduction d\'un cadre de graphique réutilisable avec des modes de présentation de graphique et de tableau.',
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
    titleFr: 'Le premier site public GIDS',
    titleZh: 'GIDS 公开站点首个版本',
    summaryEn:
      'The first version established the public home for country surveillance, disease profiles, reports, and bilingual data exploration.',
    summaryFr: 'La première version a créé le foyer public pour la surveillance des pays, les profils de maladies, les rapports et l\'exploration de données bilingues.',
    summaryZh: '首个版本建立公开站点，提供国家监测、疾病档案、报告和中英双语数据浏览。',
    sections: [
      {
        kind: 'new',
        labelEn: 'New',
        labelFr: 'Nouveau',
        labelZh: '新增',
        items: [
          {
            en: 'Launched the Astro-based public site with country and disease pages.',
            fr: 'Lancement du site public basé sur Astro avec des pages sur les pays et les maladies.',
            zh: '上线基于 Astro 的公开站点、国家页面与疾病页面。',
          },
          {
            en: 'Added generated epidemiological reports and interactive data visualizations.',
            fr: 'Ajout de rapports épidémiologiques générés et de visualisations de données interactives.',
            zh: '新增自动生成的流行病学报告与交互式数据可视化。',
          },
          {
            en: 'Added bilingual content, local fonts, and automated site data generation.',
            fr: 'Ajout de contenu bilingue, de polices locales et de génération automatisée de données de site.',
            zh: '新增中英双语内容、本地字体和自动站点数据生成。',
          },
        ],
      },
    ],
  },
];
