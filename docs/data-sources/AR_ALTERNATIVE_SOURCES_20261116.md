# 阿根廷其他官方数据入口核查（2026-11-16）

**卫生部 HIV 年度通知数据已独立接入并通过本地公开导出验收：原件直接提供全国、男女合计的 2014–2023 年 10 个数值，无需累计差分或省级求和。** 来源为 `SRC_AR_MOH_HIV_ANNUAL`，数据库原件复核、映射和三语页面均已通过，详见[正式来源合同与验收](AR_HIV_ANNUAL_INTEGRATION.md)。本文件保留此前其他入口的调查结果；登革热与呼吸道仍是待审候选，BEN 累计口径限制保持独立。尚无本轮线上部署收据。

## 已核实入口

| 入口 | 官方目录与原件 | 本轮原件核查 | 接入判断 |
| --- | --- | --- | --- |
| HIV 年度通知 | [国家目录 API](https://datos.gob.ar/api/3/action/package_show?id=ed365e37-bac7-580f-911e-17c90ef73324)；卫生部原始数据集 `1f70534d-f5f0-41f7-bc9a-b9809c39a505`；[CSV](https://datos.salud.gob.ar/dataset/1f70534d-f5f0-41f7-bc9a-b9809c39a505/resource/747d2078-9d51-40d4-ae59-ced43225459f/download/casos_prov_vih_2014-2023_1.csv)；[XLSX](https://datos.salud.gob.ar/dataset/1f70534d-f5f0-41f7-bc9a-b9809c39a505/resource/cdc2fdf3-9637-4fbc-9cf0-d05bdaa00e3f/download/casos_prov_vih_2014-2023_3.xlsx) | 两个原件均 HTTP 200，CSV 的 750 行与 XLSX 全行一致；含全国、各省、三种性别维度；年度 2014–2023 | 最优入口。选择来源明确给出的 ARG、男女合计，不把省、性别重复相加。指标是通知数，不是感染发生率、患病存量或 AIDS。 |
| 登革热／Zika 监测 | [国家目录 API](https://datos.gob.ar/api/3/action/package_show?id=b16ebe9d-8ef8-5a02-9b32-a0e0f2af1919)；卫生部原始数据集 `ceaa8e87-297e-4348-84b8-5c643e172500`；[2025 CSV](https://datos.salud.gob.ar/dataset/ceaa8e87-297e-4348-84b8-5c643e172500/resource/937f34bc-dc3d-4fa1-bb0b-09a749a69d23/download/informacion-publica-dengue-zika-nacional-se-1-a-38-de-2025-2025-09-29.csv) | HTTP 200，3,076 行，按居住地部门、省、年龄组、年和周分组；此文件只有 Dengue 与 Dengue durante la gestación，未见 Zika 行 | 有真实机器可读入口，但须先解决原件重复与目录范围差异，并核实期间及汇总规则，不能直接替代全国单周总量。 |
| 急性呼吸道感染 | [国家目录 API](https://datos.gob.ar/api/3/action/package_show?id=570637ed-baaf-5050-8631-a12b51096006)；卫生部原始数据集 `c553d917-36f4-4063-ac02-1686a9120e1c`；[2025-09 XLSX](https://datos.salud.gob.ar/dataset/c553d917-36f4-4063-ac02-1686a9120e1c/resource/1dc6b4f6-35bc-4e83-aade-d5d1c98b153b/download/informacion-publica-respiratorias-nacional-hasta-20250904.xlsx) | HTTP 200，423,743 行；2024 年第 1–52 周、2025 年第 1–35 周；24 个省级编码，年龄细分，7 类事件；全部维度键未发现重复 | 可作为第二阶段来源，先核实地区／年龄可加性、病例定义、周历和修订。ETI、肺炎、细支气管炎及门诊／住院分类不能混合。 |

国家目录在这些资源级元数据中标有 `CC-BY-4.0`，仅如实记录该具体资源的声明。本轮不以许可调查阻止用户已授权的其他主体公开，也没有将这些候选标成已完成生产验收。

## HIV 可直接选择的全国年度原始值

来源列名为 `id_sexo;sexo;id_jurisdiccion;jurisdiccion;anio;casos_vih`。需注意原件的地理列内容与列名直觉相反：`id_jurisdiccion` 存 `ARG`／省名，`jurisdiccion` 存 `200`／省编码，CSV 和 XLSX 都是如此，适配器不能按列名猜测交换。

精确选择条件：`id_sexo=3`、`sexo=Ambos sexos`、`id_jurisdiccion=ARG`、`jurisdiccion=200`。以下都是来源直接报告的单个年度全国值，不是本项目汇总或差分的结果。

| 年份 `anio` | HIV 通知数 `casos_vih` |
| --- | ---: |
| 2014 | 6,654 |
| 2015 | 6,747 |
| 2016 | 6,375 |
| 2017 | 5,218 |
| 2018 | 6,027 |
| 2019 | 6,311 |
| 2020 | 3,991 |
| 2021 | 5,772 |
| 2022 | 6,168 |
| 2023 | 6,588 |

CSV 用分号分隔，以 CP850 解码后与 XLSX 的 750 行全部字段一致；不可用 UTF-8 丢弃错误或用乱码省名作为身份。目录包的顶层时间描述落后于具体资源（仍有 2010–2019／2022 文字），应以所选 2014–2023 资源原件及版本证据为准。本轮没有发现该资源中的 2024–2026 年值，不能声称是当前周实时数据。

后续接入已独立建立“Ministerio de Salud HIV annual notifications”来源与定义，保留资源 ID、字段、年度和原件哈希，没有复用 BEN 的累计系列身份。正常三阶段导入、疾病映射、年度日期合同、修订台账和生成快照验收已完成；三语页面本地验收通过，线上部署另行记录。

## 登革热与呼吸道的具体局限

登革热资源标题与文件名写“第 1–38 周”，实际 `anio_min=2025`、`sepi_min` 范围为 1–51；因此不能仅按标题定义数据窗口。相同维度还出现一组冲突：未知省 `99`、未知部门 `999`、Dengue、年龄组 `9`、2025 第 9 周有 `cantidad=5` 和 `1` 两行。不可静默合并、任选一行或把它当独立病例计数。

`Dengue durante la gestación` 是独立事件，未经源定义确认不能与 Dengue 相加。`sepi_min` 的具体日期角色、是否为期间数而非累计数、未知居住地的处理和地区覆盖仍待原始数据字典验证。不存在的 Zika 行也不能补成零。

呼吸道原件字段为 `departamento_id, departamento_nombre, provincia_id, provincia_nombre, año, semanas_epidemiologicas, evento_nombre, grupo_edad_id, grupo_edad_desc, cantidad_casos`。7 类事件包括 ETI、未细分肺炎／细支气管炎以及门诊和住院细分；这些定义随来源版本应保留独立身份，不能通过所有事件相加创建“呼吸道感染总病例”。

## 下载、TLS 与可复核证据

卫生部下载站未发送完整中间证书，默认 Requests／curl 报 `unable to get local issuer certificate`。本轮从服务器叶证书 AIA 所指的 [Sectigo 中间证书](http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt)取得缺失的 `Sectigo Public Server Authentication CA DV R36`，补链后下载成功。没有关闭 TLS 验证，也没有新增受信任根证书；中间证书须最终锚定环境已有的 CA。

复核流程：将下载的 DER 中间证书转换为 PEM，用既有 `requests.certs.where()` 根证书包和该中间证书组成 CA 文件，再使用 `requests.get(url, verify=ca_file)`。独立的 `openssl verify -verify_hostname datos.salud.gob.ar -CAfile <既有根证书包> -untrusted intermediate.pem leaf.pem` 返回 `OK`，信任链落到环境已有的 Sectigo Root R46。只补该中间证书后再次下载 HIV CSV 也是 200 且哈希一致。此为服务器缺链的取证修复，不是降低生产 TLS 验证要求。

| 下载原件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| HIV CSV | 24,779 | `b16a6341c22f6c6583d0e3440a2e9d521e7efa405017d82da5cda0c15a665be3` |
| HIV XLSX | 28,971 | `ea24bf663b734549664a1d5a54d64c8bf0f6368a4308cbd5f8bf3823ebf1e96e` |
| 登革热 2025 CSV | 196,727 | `21c4a554a6b420fa61940a5ec28ac5417cd3ece833245bc9fc831a56b1f34fce` |
| 呼吸道 2025 XLSX | 16,820,426 | `e4c2665da29605162cc72d6056381a2103d108aea8448bd364fb9f913e48f33b` |

小型证据索引见 [JSON](evidence/ar-alternative-sources-20261116.json)。完整目录响应、下载原件、字段统计及 TLS 叶证书／中间证书／验证记录存于本地 `data/current/ar/alternative_source_discovery/`，并由索引记录 URL、状态及哈希。旧 SISA COVID 门诊 ZIP 本轮返回 404，未将其列为已验证可用的数据入口。
