"""Source-scoped publication admissions requested by the operator.

The operator's 2026-11-16 decision removes the pending-license publication
hold for these nine jurisdictions. It is not a source reuse-license finding.
Existing reviewed mappings, geography and statistical projection contracts
still decide which observations can appear in public datasets.
"""

from __future__ import annotations


OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES = frozenset({"BQ", "ID", "KH", "MW", "MX", "NG", "PE", "TH", "ZA"})
AUTHORIZATION_DATE = "2026-11-16"
REUSE_STATUS = "operator_authorized_public_release"

# Country, scope, source identity and source-native totals are deliberately
# explicit. In particular BQ keeps the NL collector identity and TH's archive
# with unverified week boundaries remains outside this allow-list.
_SOURCES = {
    "BQ": {
        "rivm_notifiable_annual": (
            "SRC_NL_RIVM_NOTIFIABLE", "RIVM Caribbean Netherlands Annual Notifications", "Rijksinstituut voor Volksgezondheid en Milieu (RIVM)",
            "https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen", "annual",
            "Annual mandatory notifications for the Caribbean Netherlands only; source geography BQ is retained separately from the Netherlands national series. Dates follow first onset, otherwise diagnosis or notification, as specified by the source.",
            "荷属加勒比地区年度法定传染病报告数；仅使用来源明确标识的 BQ 地域，与荷兰全国序列分别保留。日期按来源规定优先采用首次发病日期，其次为诊断或报告日期。",
            "Notifications annuelles obligatoires pour les Pays-Bas caribéens uniquement ; la géographie BQ reste distincte de la série nationale néerlandaise. La date suit le début des symptômes, à défaut le diagnostic ou la notification selon la source.",
        ),
    },
    "ID": {
        "id_kemkes_profile": (
            "SRC_ID_KEMKES_PROFILE", "Indonesia Ministry of Health Annual Health Profile", "Ministry of Health of the Republic of Indonesia",
            "https://kemkes.go.id/id/profil-kesehatan-indonesia-2024", "annual",
            "Only explicitly national annual case totals in reviewed health-profile narrative tables are presented. Appendix column identities, rates, registered stock and classification subsets are not substituted for national case totals.",
            "仅展示已审核健康概况正文中明确标为全国的年度病例总数；附表列、率、登记存量及分类分项不替代全国病例总数。",
            "Seuls les totaux annuels nationaux explicites des tableaux narratifs examinés du profil sanitaire sont présentés. Les annexes, taux, stocks enregistrés et sous-catégories ne remplacent pas les totaux nationaux.",
        ),
    },
    "KH": {
        "kh_camewarn_weekly": (
            "SRC_KH_CAMEWARN", "Cambodia CDC CamEWARN Weekly Surveillance", "Cambodia Ministry of Health, CDC Department",
            "https://cdcmoh.gov.kh/surveillance/camewarn", "weekly",
            "CamEWARN disease and syndrome notifications cover reporting health facilities. Only reviewed national weekly numeric matrices with explicit report intervals are presented; chart-only values and absent weeks remain unknown.",
            "CamEWARN 疾病与综合征报告覆盖参与报告的医疗机构；仅展示具有明确报告区间且已审核的全国周度数值矩阵。仅有图表的数值和缺失周保持未知。",
            "Les notifications de maladies et syndromes CamEWARN couvrent les établissements déclarants. Seules les matrices nationales hebdomadaires chiffrées et examinées, avec intervalles explicites, sont présentées ; graphiques seuls et semaines absentes restent inconnus.",
        ),
    },
    "MW": {
        "mw_phim_idsr_weekly": (
            "SRC_MW_PHIM_IDSR", "Malawi PHIM IDSR Weekly Bulletin", "Public Health Institute of Malawi (PHIM)",
            "https://phim.health.gov.mw/", "weekly",
            "Only reviewed Table 1 single-week suspected disease notification counts are presented. Confirmed classifications, cumulative columns and overlapping report sections are not added to these weekly totals.",
            "仅展示已审核 IDSR 周报表 1 的单周疑似病例报告数；确诊分类、累计列及重叠章节不与这些周总数相加。",
            "Seuls les nombres hebdomadaires de notifications de cas suspects du tableau 1 examiné sont présentés. Les cas confirmés, colonnes cumulées et sections qui se recoupent ne sont pas ajoutés à ces totaux.",
        ),
    },
    "MX": {
        "mx_dge_annual": (
            "SRC_MX_DGE_ANNUAL", "Mexico DGE Annual Morbidity", "Secretaría de Salud, Dirección General de Epidemiología (DGE)",
            "https://epidemiologia.salud.gob.mx/anuario/html/index.html", "annual",
            "Reviewed national annual new-case totals include all ages and sexes. ICD revisions and source definitions are retained; rates, age/sex strata and subnational rows do not replace or add to national totals.",
            "展示已审核的全国、全年龄、全性别年度新报告病例总数，保留 ICD 版本及来源定义；率、年龄／性别分层和地方行不替代或累加到全国总数。",
            "Les totaux annuels nationaux examinés couvrent tous les âges et sexes. Les versions de la CIM et définitions sources sont conservées ; taux, strates et lignes infranationales ne remplacent ni ne complètent les totaux nationaux.",
        ),
    },
    "NG": {
        "ncdc_surveillance": (
            "SRC_NG_NCDC", "Nigeria NCDC Weekly Epidemiological Reports", "Nigeria Centre for Disease Control and Prevention (NCDC)",
            "https://ncdc.gov.ng/reports/weekly", "weekly",
            "Only WER single-week suspected case notifications are presented. Situation reports, confirmed counts and cumulative snapshots retain separate identities and are not added to this curve; missing reports remain gaps.",
            "仅展示 WER 单周疑似病例报告数；情况报告、确诊数和累计快照保持独立身份，不与该曲线相加。缺失报告保留为覆盖缺口。",
            "Seules les notifications hebdomadaires de cas suspects du WER sont présentées. Rapports de situation, cas confirmés et cumuls restent distincts et ne sont pas additionnés à cette courbe ; les rapports absents restent des lacunes.",
        ),
    },
    "PE": {
        "pe_cdc_weekly": (
            "SRC_PE_CDC", "Peru CDC Epidemiological Bulletin", "Ministerio de Salud del Perú, CDC Perú / DGE",
            "https://www.dge.gob.pe/portalnuevo/informacion-publica/boletines-epidemiologicos/", "weekly",
            "Only directly reported national single-week case totals from reviewed summary tables and explicit bulletin dates are presented. Classification components, cumulative and comparison columns are not summed; unreadable or missing source values stay unknown.",
            "仅展示已审核汇总表直接列出的全国单周病例总数，采用周报明确日期；分类分项、累计列和对比列不相加，无法读取或缺失的来源数值保持未知。",
            "Seuls les totaux nationaux hebdomadaires directement publiés dans les tableaux récapitulatifs examinés, avec dates explicites, sont présentés. Composantes, cumuls et comparaisons ne sont pas additionnés ; valeurs illisibles ou absentes restent inconnues.",
        ),
    },
    "TH": {
        "th_doe506_monthly": (
            "SRC_TH_DOE506", "Thailand DOE Report 506 Monthly History", "Thailand Ministry of Public Health, Department of Disease Control",
            "https://doe.moph.go.th/surdata/", "monthly",
            "Report 506 national monthly notifications retain the source's mostly suspected case definition. Monthly reporting counts stay separate from DDS onset-week and report-week data; missing source periods remain unknown.",
            "Report 506 全国月度报告数保留来源以疑似病例为主的定义；月度报告数与 DDS 发病周、报告周数据分别保留，缺失期间保持未知。",
            "Les notifications nationales mensuelles du rapport 506 conservent la définition source, principalement des cas suspects. Elles restent distinctes des semaines de début et de notification DDS ; les périodes absentes restent inconnues.",
        ),
        "th_dds_weekly": (
            "SRC_TH_DDS_WEEKLY", "Thailand DDS Weekly Onset Notifications", "Thailand Ministry of Public Health, Department of Disease Control",
            "https://doe.moph.go.th/dds-report/", "weekly",
            "Reviewed national DDS weekly counts use onset date and place. They remain separate from report-date DDS Tableau and Report 506 monthly counts; only the reviewed onset definition is eligible.",
            "已审核全国 DDS 周度计数按发病日期及地点统计，与按报告日期统计的 DDS Tableau 及 Report 506 月度计数分别保留；仅采用已审核的发病口径。",
            "Les nombres hebdomadaires nationaux DDS examinés suivent la date et le lieu de début. Ils restent distincts du Tableau DDS par notification et des comptes mensuels du rapport 506 ; seule la définition examinée est retenue.",
        ),
        "th_dds_tableau": (
            "SRC_TH_DDS_TABLEAU", "Thailand DDS Tableau Weekly Report Notifications", "Thailand Ministry of Public Health, Department of Disease Control",
            "https://doe.moph.go.th/dds-report/", "weekly",
            "Reviewed national DDS Tableau weekly counts use report date and place. They are a distinct source definition from onset-week DDS and monthly Report 506; the series are not combined into an additive total.",
            "已审核全国 DDS Tableau 周度计数按报告日期及地点统计；与按发病周统计的 DDS 和 Report 506 月度数据具有不同来源定义，不合并相加。",
            "Les comptes hebdomadaires nationaux examinés du Tableau DDS suivent la date et le lieu de notification. Leur définition est distincte des semaines de début DDS et du rapport 506 mensuel ; les séries ne sont pas additionnées.",
        ),
    },
    "ZA": {
        "za_nicd_nmc": (
            "SRC_ZA_NICD_NMC", "South Africa NICD NMC", "National Institute for Communicable Diseases (NICD), South Africa",
            "https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/", "monthly",
            "Only reviewed current monthly all-notified national NMC counts are presented, retaining the source notification window. Cohort comparisons and patient-status subsets are excluded; patient death status is not treated as deaths occurring in that month.",
            "仅展示已审核 NMC 当前报告月全国全部通知计数，保留来源通知窗口；排除队列比较和患者状态分项，不将患者死亡状态视作当月发生的死亡。",
            "Seuls les comptes nationaux NMC examinés de toutes les notifications du mois courant sont présentés, avec leur fenêtre source. Comparaisons de cohortes et sous-groupes par statut sont exclus ; le statut de décès ne devient pas un décès survenu ce mois-là.",
        ),
    },
}


def reviewed_national_admission_config(code: str) -> dict:
    """Return a fresh explicit overlay without admitting unreviewed feeds."""
    if code not in OPERATOR_AUTHORIZED_NATIONAL_COUNTRIES:
        return {}
    details = {}
    for scope, (source_id, label, publisher, url, cadence, en, zh, fr) in _SOURCES[code].items():
        details[scope] = {
            "source_id": source_id, "label": label, "publisher": publisher,
            "url": url, "type": "official_reports", "cadence": cadence,
            "license": "Source-specific reuse terms unverified", "license_url": None,
            "required_attribution": f"{publisher}, {label}. Original source: {url}. Source definitions and reporting periods retained.",
            "description": en + " Missing values remain unknown and explicit source zeros are preserved. An open license covering these data has not been verified; third-party reuse should consult the original source terms.",
            "description_zh": zh + " 缺失值保持未知，来源明确的零值予以保留。尚未核实覆盖此数据的开放许可；第三方复用请查阅原始来源条款。",
            "description_fr": fr + " Les valeurs manquantes restent inconnues et les zéros explicites sont conservés. Aucune licence ouverte couvrant ces données n’a été vérifiée ; consulter les conditions de la source avant toute réutilisation par des tiers.",
            "review_status": "source_terms_unverified_semantics_reviewed",
            "source_license_verified": False,
        }
    policies = {
        scope: {
            "public_release_enabled": True,
            "reuse_status": REUSE_STATUS,
            "source_license_verified": False,
            "operator_authorized_public_release": True,
            "publisher": item["publisher"],
            "required_attribution": item["required_attribution"],
        }
        for scope, item in details.items()
    }
    if code == "TH":
        policies["th_id_th_history"] = {"public_release_enabled": False}
    return {
        "public_release_enabled": True, "public_legacy_enabled": False,
        "public_source_systems": [item["source_id"] for item in details.values()],
        "public_source_scopes": list(details), "public_source_details": details,
        "public_source_notes": " ".join(item["description"] for item in details.values()),
        "crawler_config": {
            "reuse_status": REUSE_STATUS,
            "operator_authorized_public_release": True,
            "public_release_authorized_on": AUTHORIZATION_DATE,
            "source_license_verified": False,
            "source_policies": policies,
            **({"public_release_enabled": True} if code == "BQ" else {}),
        },
    }
