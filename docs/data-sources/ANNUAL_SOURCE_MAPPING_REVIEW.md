# WHO／SPF 年度来源映射审查

日期：2026-11-16。范围为既有 15 国 WHO 年度基线和 3 个法国海外地区 SPF
年度数据。此文记录映射及源文件语义验收；数据库映射激活、公开快照和部署分别验收。

## 已完成的映射

- WHO：ET、KE、UG、GH、TZ、PH、VN、LA、MM、BN、TL、PY、EC、CR、PA。
  每国 18 个已审核指标，共 270 条正式 reviewed CSV 合同；当前实际有 222 个
  来源类别。没有观测的指标仅保留合同，不生成病例零值或空观测。
- SPF：GP、MQ、GF 每地 48 个来源类别，共 144 条合同。每地 6 条原生总计
  类别可用于标准疾病曲线（TB 两种地理定义分别计数）；42 条年龄／血清群类别
  使用 `discovery_only`，不进入地区总病例曲线。
- 正式输入是 `configs/mapping/reviewed/<cc>.csv`；由既有
  `load_reviewed_source_category_mappings` 和注册表导入路径消费。
  `configs/onboarding/annual_source_mappings.json` 补充每个身份对应的
  measure、frequency、geography、reporting basis、总计 dimensions 和来源允许列表。
  该 JSON 不替代注册表断言与版本化发布。

WHO 中先天性风疹综合征和风疹、新生儿破伤风和总破伤风分别映射；脑膜炎映射
全部报告病因概念 D235，不能缩窄为细菌性或脑膜炎球菌病。新麻风病例、新发／复发
结核及确诊疟疾以 `narrower` 保留其统计范围。死亡指标保留原 metric，不因疾病概念
相同就自动挂接病例死亡列。所有合同均 `conditional`、`non_additive`。

SPF 麻疹采用皮疹年份。结核 2018 年及以前使用报告地、2019 年起使用居住地；
两段定义版本独立，不作无标识拼接。年龄及血清群总计不与分项相加，法国海外
地区数据不自动上卷法国全国。

## 总计维度

动态 holding 观测保留的 `dimension_key` 是哈希，不能仅因不是 `all` 就把原生
总计当成人口分层。公开解释仍需同时验证来源、代码、定义版本、度量、年度和地理：

- WHO：精确 `{"who_indicator": "<reviewed indicator>"}`。
- SPF 无分层病种：精确 `{"dataset": "<reviewed dataset>"}`。
- SPF 麻疹：额外要求 `mdo_cl_age_rougeole = Tous âges`。
- SPF 脑膜炎球菌病：同时要求 `classe_age = Tous âges` 和
  `meningocoque = Tous types`。

`src/services/annual_source_mapping_review.py` 对源代码、标签、定义版本及 SPF
分层哈希执行严格核对。原始观测和 dimensions 保留；总计识别是发布解释规则。

## 使用条款

实际读取的 [WHO 数据集条款](https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions)
允许公共卫生用途下使用、复制、提取、分发和展示，要求 WHO、数据集名称、年份、
访问日期及报告国家署名。不得商业推广、暗示 WHO 背书或歪曲数据。其他数据集
修改须事先书面授权，第三方署名材料另行审查。本次映射作为独立解释元数据，
保留源数值、原类别名称、来源链接及缺失语义；不将 WHO 原数据改写为新数据集。
不能把该条款标为无限制 CC BY 4.0，也不能用一般 WHO 出版物许可替代数据集条款。

2026-11-16 实际 HTTP 200 响应 SHA-256：
`03b28f1ca91c134ac48d3a11fa465c170b351bb4d117faff6bc38c5b409967ab`。
原页和收据在 `reports/onboarding/annual_mapping_licensing/`。
SPF 五套数据的既有真实元数据均声明 Licence ouverte 2.0，要求保留
Santé publique France、数据集及提取日期署名；本次 Etalab 许可主页读取出现 TLS
EOF，不把该访问失败说成新一次许可页抓取成功。

## 验证与边界

```bash
venv/bin/python -m pytest tests/unit/test_annual_source_mapping_review.py -q
venv/bin/python scripts/verify_annual_source_mappings.py \
  --output reports/onboarding/annual_mapping_semantic_replay.json
```

保留的 18 国／地区源 CSV 共 **10,849** 条观测已逐行通过：映射身份、病例／死亡
指标、年度闭区间、国家／地区、总计维度、未知值、唯一身份及结核地理定义变化。
验证脚本只读源文件，不写库、不改原观测、不启用发布门禁，也不把此通过结果当作
数据库对账、公开快照或线上部署收据。

CR 卫生部专项及 LA／VN WPRO 登革热是独立来源，未因 WHO 年度映射而自动
取得准入；它们仍需各自的合同、映射与允许列表。

2026-11-16 的公开准入配置仅允许上述 18 个主体的已审查 WHO／SPF 来源，以及
智利全国确诊年度汇总、马来西亚 MOH 年度 STD 与 WHO 年度指标，共 20 个主体、
21 个来源。`public_source_systems` 与 `public_source_scopes` 明确限定来源，旧式
观测发布关闭。MY iDengue／FluNet、CR MOH 与 LA／VN WPRO 不在公开允许列表。
`build_country_source_info()` 将署名、许可链接、数据范围和定义变化写入公开
`source_info`，即使数据库中仍保存旧的采集配置也保留上述说明。公开快照和实际
发布结果以本轮独立验收收据为准。
