# 国家数据写入并发

## CPU 密集型来源整理

BR SINAN 的全国和省州 `organize` 阶段使用独立 `spawn` 进程，避免 DBF 逐条解析
与主 worker 的发布校验、冻结候选、日志消费和事件循环争抢 Python GIL。
`GIDS_INGESTION_PROCESS_WORKERS` 限制每个主 worker 同时运行的整理进程数，默认 2，
有效范围 1–4；单个 BR 整理进程使用一个解析线程。

父进程只向子进程传递已持久化 artifact 的路径、校验信息和来源选项。子进程再次
验证原始文件大小与 SHA-256，只返回聚合结果；正式入库、质量校验和 dirty 标记
仍由原有父进程事务完成。整理期间每两秒通过原有任务接口检查面板取消标记；
每次查询结束立即释放连接。取消任务会终止并回收解析进程，再删除父进程持有的
临时目录，避免后台解析继续占用 CPU 或遗留解压后的大型 DBF 文件。

此修复不会中断已在旧解释器线程中运行的解析。部署后需要按任务恢复收据替换
加载旧代码的 worker；仅取消旧任务的 asyncio await 无法停止已运行的 DBF 线程。

## 写入分区与维护边界

正式采集使用两级 PostgreSQL 事务 advisory lock：

1. 对原有 `globalid:disease_data_mutation:v1` 获取共享锁，进入正常写入通道。
2. 对国家父级获取独占分区锁。例如 `US`、`US-AK`、`US-CA` 都使用 `US` 分区。
3. 在同一数据库事务中完成 legacy 投影、source category discovery、holding series、观测、修订与 `publish_dirty`。
4. 由提交、回滚或连接关闭释放全部事务锁。

`US` 与 `SE` 可以同时提交；同一国家的不同来源、国家和省州任务仍然串行。
国家级来源会生成多个地区的 shared series，所以当前分区不能直接细化成省州。
此次改造保持原有事务原子性和质量校验边界。

跨国家 ontology 变更、restore 和多国家重建继续调用
`acquire_disease_data_mutation_lock()`，对原 key 获取独占锁。
它等待已进入的国家写入提交，并排斥新的国家写入。
单国家 source-series 回填经 store 的国家分区运行；需要跨国家修改的维护命令必须在
第一笔写入前获取全局独占锁。`rebuild_source_series_components.py` 和 Iceland
历史维护导入已明确在入口获取维护锁。

锁顺序固定为“全局闸门 → 国家分区”。普通写入不能先持有共享闸门、再升级为全局
独占锁；两个事务同时升级可能互相等待。维护命令先持有全局独占锁，再调用国家
store 是安全的，同事务重入由 PostgreSQL 支持。

## 已消除的重复数据读取

质量校验历史查询不再读取历史 `raw_data`。现有校验只需要观测身份、时间、值、
suppression、quality 与 frequency 元数据；authoritative revision 许可检查作用于
本次 incoming 行。原始 provenance 仍保存在观测和修订表中，导出和审计仍可读取。
这减少大型历史质量校验的 JSON 传输、解析和内存，不改变接受或隔离的判定。

## 可观测性与限制

国家分区获取耗时达到一秒时记录 `country_mutation_lock_wait`，包含
`country_scope` 和 `elapsed_seconds`。该时间同时包括维护闸门与国家分区等待；
它不是事务执行时间。后续需要将来源下载、规范化、registry、quality、revision
和 commit 分开计时，才能量化大型美国批次内部的剩余成本。

这项改造消除了不相关国家之间的全局排队，仍然保留：

- 同一国家的原子双写和来源窗口替换等待。
- 大型历史批次的 revision 对比和写入成本。
- 共享源 category 的普通 PostgreSQL 行锁；来源身份必须遵守现有 country/source 定义。
- 全局维护期间所有正式数据写入等待。

不能把此变更当作“美国大批次已经变快”的实测结论；它改善的是其他国家不再被该
大批次全局阻塞。缩小批次必须与历史校验覆盖、来源窗口替换、回滚语义一起设计。

## 验证与上线

`tests/integration/test_postgres_country_mutation_lock.py` 在显式指定的独立 PostgreSQL
连接上使用唯一 advisory key，不写业务表。真实数据库测试覆盖不同国家并行、
父子国家互斥、维护双向互斥、取消后的回滚释放、维护事务调用 store 的重入。

运行方式：

```sh
TEST_MUTATION_DATABASE_URL=postgresql+asyncpg://... \
  venv/bin/python -m pytest -q tests/integration/test_postgres_country_mutation_lock.py
```

本次在独立 PostgreSQL 15 容器通过上述测试，另有观测存储、质量校验、双写、窗口
替换、legacy 持久化、restore 和重建回归验证。单元测试不是并发证明；并发证明由
实际 PostgreSQL 的 `pg_blocking_pids()` 和事务释放行为提供。

上线时排空并重启所有加载旧 Python 模块的 worker/API 和长期运行的导入进程，
按现有运行时重启流程恢复服务。旧版本继续使用原全局独占 key，与新共享闸门
兼容，混跑期间会保持较保守的全局串行，因此并发收益要等旧事务结束后出现。
不要在持有旧连接/事务时热替换锁策略。此改造无需数据库迁移。
