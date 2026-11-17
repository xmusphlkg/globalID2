# 欧洲周/月数据缺口核对（2026-11-17）

核对线上发布 `data-20261116T154800Z-reviewed-countries` 的32个欧洲国家/地区，结合源原生观测只读汇总和官方HTTP/PDF/CSV样本。**25个主体默认公开曲线只有年度数据；7个已有周或月数据。** 只有年度不等于源站没有周/月数据。此次没有新增连接器、改数据库或发布网站。

已核验的遗漏优先级：捷克、爱沙尼亚有全国多病种单月病例原表；奥地利已入库月源未公开；荷兰已入库周源但周界未验证；欧洲30个EU/EEA主体均可在ECDC官方呼吸道实验室周CSV找到真实数值。英国应按England等实际地理单独建源。

## 统一的欧洲周频补充源

[ECDC官方数据仓库](https://github.com/EU-ECDC/Respiratory_viruses_weekly_data)的 `nonSentinelTestsDetections.csv` 实际下载8,223,780字节，SHA256为 `83fba8715f4989ee1c1d0a8bbef4ac66812477f50de21753ef0cb8bc3ed0bf80`。解析确认30个EU/EEA主体都有有限数值，指标为检测数、阳性检出数，病原为流感、RSV、SARS-CoV-2（列支敦士登本次未见RSV）。源周期为明确的 `yearweek`。另取 `ILIARIRates.csv`，可补哨点咨询率，但不能与通知病例合并。

这是一组呼吸道专项候选，不能替代全病种法定报告。不得累加病毒亚型、检测数和阳性数，也不能统一不同国家咨询率的分母。README仍载有2026年迁移后暂停GitHub更新的声明，而真实文件包含11月周数据；因此保留原始说明，并以实际文件及各国、各指标最后观测周分别判断覆盖。意大利实验室数据仅到2026-W09，法国到W43，立陶宛到W39，不能把仓库更新时间当各国最新数据。

## 逐国结果

“ECDC实值”列是上述CSV实测数值行数/最后周，不是公开网站新增点数，不是完整病例数。

| 代码 | 国家/地区 | 当前线上默认粒度 | ECDC实值/最后周 | 官方来源及处理结论 |
| --- | --- | --- | --- | --- |
| AT | 奥地利 | 年 | 2,958 / 2026-W45 | AGES 月度已入库441条，另189条粒度unknown；线上仍只发布ECDC年度。应先完成现有月源审核和公开投影。 |
| BE | 比利时 | 年 | 3,318 / 2026-W45 | Sciensano Epistat可达；尚未从国家页面验证连续数值下载合同。ECDC实验室周CSV有实值。 |
| BG | 保加利亚 | 年 | 2,180 / 2026-W45 | NCIPD入口TLS失败；ECDC实验室周CSV有实值，不能因国家站失败推断没有周数据。 |
| CH | 瑞士 | 未知、周、年、月 | 未在本次CSV出现 | 默认曲线已有2周、1月、11年、14unknown疾病；source-series明细仅unknown/annual，且默认28个疾病依赖legacy。单列粒度/旧回退问题，不计整体无周月。 |
| CY | 塞浦路斯 | 年 | 3,300 / 2026-W44 | 卫生部入口HTTP500；ECDC实验室周CSV有实值。ILI咨询率分母是每100次就诊，不能当每10万人。 |
| CZ | 捷克 | 年 | 4,102 / 2026-W45 | SZÚ已下载2026年10月全国单月病例PDF，另有1–10月累计和各州表；单月与累计必须分开。 |
| DE | 德国 | 年、未知、周 | 3,020 / 2026-W40 | 线上已公开RKI周源51,528点，默认7个周粒度疾病，另55个unknown；不能说整个德国没有周数据。 |
| DK | 丹麦 | 年 | 4,768 / 2026-W45 | SSI明确2026-W40至2027-W20每周三更新流感阳性和住院监测；ECDC另有周实值。 |
| EE | 爱沙尼亚 | 年 | 3,995 / 2026-W45 | Terviseamet已下载2026年10月病例PDF，表内同时含年初累计、当月总数和县数据；月总数可直接保留。 |
| ES | 西班牙 | 年 | 2,498 / 2026-W45 | BES虽然名称含Semanal，但官网明确当前为季度科学期刊，不是现行数值周报；ECDC周CSV可作呼吸道专项补充。 |
| FI | 芬兰 | 年、月 | 1,867 / 2026-W45 | 当前已有月数据，不能列整体周/月缺失；ECDC可补呼吸道周专项。 |
| FR | 法国 | 年 | 3,093 / 2026-W43 | SPF ODISSÉ实测IRA RENAL接口有2026-S45数值及地区字段reg；地区记录不可直接冒充全国，检测/阳性/死亡需分源分指标。 |
| GB | 英国 | 年 | 未在本次CSV出现 | UKHSA NOIDs首页明确常规周报自2025-04-03暂停；2026 causative agents报告仍发布。其主要地理为England，不能直接标GB全国。ECDC本次CSV未含GB。 |
| GR | 希腊 | 年 | 3,209 / 2026-W45 | EODY国家页HTTP403；ECDC实验室周CSV已取得实值。 |
| HR | 克罗地亚 | 年 | 4,186 / 2026-W45 | HZJZ实际2026-W45流感报告及W40–W44目录可达；这是流感专项，不能代表全部传染病。 |
| HU | 匈牙利 | 年 | 2,788 / 2026-W45 | 尝试的NNGYK专业页404，路径需重新发现；ECDC实验室周CSV有实值。 |
| IE | 爱尔兰 | 年、周、未知 | 4,297 / 2026-W45 | 当前已有周数据；不列整体周/月缺失。其最新观测与报告档案仍应独立核对。 |
| IS | 冰岛 | 年、周、月 | 4,155 / 2026-W45 | 当前已有周和月数据，不列整体缺失。 |
| IT | 意大利 | 年 | 2,877 / 2026-W09 | ISS RespiVirNet官方周报页可达；ECDC两份CSV的意大利最后周仅2026-W09，不能将文件更新时间当意大利最新周。 |
| LI | 列支敦士登 | 年 | 2,503 / 2026-W44 | LLV国家页HTTP403；ECDC实验室周CSV确有列支敦士登数值，病原为Influenza/SARS-CoV-2，本次未见RSV。 |
| LT | 立陶宛 | 年 | 3,172 / 2026-W39 | NVSC国家页受403挑战；ECDC实验室最后周W39，但ILI咨询率CSV到W45，指标不能混为同一时间覆盖。 |
| LU | 卢森堡 | 年 | 3,953 / 2026-W45 | 所尝试国家周报路径404；ECDC实验室周CSV有实值。ILI咨询率为每100次就诊，不能使用统一人口分母。 |
| LV | 拉脱维亚 | 年 | 2,726 / 2026-W45 | SPKC目录两次超时；ECDC实验室周CSV有实值。 |
| MT | 马耳他 | 年 | 4,473 / 2026-W45 | HPDP疾病介绍页不构成数值归档；ECDC实验室周CSV有实值。ILI咨询率为每100次就诊。 |
| NL | 荷兰 | 年 | 2,721 / 2026-W45 | RIVM两个周源已入库4,632条（4,356有值），但源周界未验证、时间戳是年锚且公开关闭，线上仅年度。不能直接将1月1日画成周日期。 |
| NO | 挪威 | 年、月 | 4,776 / 2026-W45 | 已有月数据，不列整体周/月缺失。 |
| PL | 波兰 | 年 | 3,498 / 2026-W45 | 已下载PZH 2026-10/B四页PDF，是1月1日至10月31日累计病例/率，每半月发布；不能当单月新增或自行相减。ECDC周源是另一独立专项。 |
| PT | 葡萄牙 | 年 | 4,052 / 2026-W44 | DGS已下载报告205，明确2026-W45（11月2–8日）；含呼吸道/医疗服务/死亡监测。ECDC实验室到W44；ILI CSV仅到2023-W09。 |
| RO | 罗马尼亚 | 年 | 3,289 / 2026-W45 | 旧CNSCBT路径404、新INSP路径断连；ECDC实验室周CSV有实值。 |
| SE | 瑞典 | 年、月 | 4,527 / 2026-W45 | 已有月数据，不列整体周/月缺失。 |
| SI | 斯洛文尼亚 | 年 | 4,406 / 2026-W45 | NIJZ目录和具体周报路径均断连；ECDC实验室周CSV有实值。 |
| SK | 斯洛伐克 | 年 | 4,027 / 2026-W45 | UVZ页面HTTP401；ECDC实验室周CSV有实值。 |

## 可直接复核的国家原表

- [捷克2026年10月全国单月病例PDF](https://szu.gov.cz/wp-content/uploads/2026/11/Tabulka_rijen_2026.pdf)：实际下载2页、254,985字节；表头明确October 2026，保留与2017–2025同月比较及暂定数据说明。2026年10月沙门菌1,198例，属于单月列，不能与累计表混用。
- [爱沙尼亚2026年10月全国/县病例PDF](https://www.terviseamet.ee/sites/default/files/documents/2026-11/NH_haigestumine_Eestis_maakondade%20kaupa_10_2026.pdf)：表内单列年初至今、当月总数和县。例：沙门菌年初至今115、当月9；正确月数据来自当月列。
- [波兰2026年10/B累计PDF](https://wwwold.pzh.gov.pl/oldpage/epimeld/2026/INF_26_10B.pdf)：实际下载4页、139,383字节；表头明确2026年1月1日至10月31日，说明发布频率不能替代观测周期。
- [葡萄牙DGS第205号周报PDF](https://www.dgs.pt/em-destaque/relatorio-n-205-da-resposta-sazonal-em-saude-vigilancia-e-monitorizacao-pdf.aspx)：实际下载21页、4,551,853字节，明确2026-W45/11月2–8日。医疗服务比例、实验室检出、死亡等须分指标。
- [丹麦SSI流感监测](https://www.ssi.dk/sygdomme-beredskab-og-forskning/sygdomsovervaagning/i/influenza-ugens-opgoerelse)：明确每周病例和住院，2026-W40至2027-W20每周三更新。
- [克罗地亚2026-W45流感报告](https://www.hzjz.hr/aktualnosti/gripa-u-hrvatskoj-u-sezoni-2026-2027-45-tjedan-2026/)及[意大利RespiVirNet周报页](https://www.epicentro.iss.it/sorveglianza-infezioni-respiratorie-acute/rapporto)：官方专项入口已验证，整档数值合同尚未完成。
- [法国ODISSÉ IRA RENAL数据](https://odisse.santepubliquefrance.fr/explore/dataset/ira-virologie-renal/)：实际API取得2026-S45有限数值，但样本 `reg=24` 是地区记录，不能无条件标作全国；需核国家行及网络覆盖。
- [英国NOIDs官方目录](https://www.gov.uk/government/collections/notifications-of-infectious-diseases-noids)：明确常规周报暂停，且[2026实验室致病原报告](https://www.gov.uk/government/publications/notifiable-diseases-causative-agents-reports-for-2026)继续发布。英国国家曲线不能直接用England数据替代。
- [西班牙BES官网](https://revista.isciii.es/index.php/bes)：明确目前季度出版、每年4期；名字里的“Semanal”不足以证明现行数值周报。

## 边界与后续

HTTP 403/401、TLS失败、超时、404只证明本次访问或路径有问题，不能证明国家没有高频数据。表中已用ECDC实际数值补充候选依据；国家本地完整档案仍需逐一建立采集合同。现有周/月主体也可能缺病种或缺历史周，此轮国家级频率筛查不构成每个病种每期完整性验收。

优先顺序：先修AT现有月源和NL周日历/公开投影，再接CZ/EE全国多病种单月表；同步评估ECDC呼吸道周补充源、FR官方周API、PT/DK国家专项；GB按实际辖区处理。所有新源保留来源身份、真实周期、数值/未知/累计口径，避免年度基线和新源相加。

机器证据：[frequency-audit-europe-20261117.json](evidence/frequency-audit-europe-20261117.json)。其中包含32个主体的线上粒度、源原生观测汇总、30个国家逐项CSV实值覆盖、HTTP状态及文件SHA256。
