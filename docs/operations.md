# 配置与维护

## 推荐规则

`config/custom.yaml` 是完整示例。`topics.description` 决定任务语义，`screening.profile` 决定入排和 core/cross/explore 分组；更换课题时两处一起改。收藏语义占 20%，课题描述占 80%。没有可用收藏摘要时以主题作参考。

每次推送上限由使用者填写 `executor.max_paper_num`，没有固定篇数。`group_weights: null` 不预留各组比例；若填写自己的权重，允许合格论文补位。篇数为上限，不凑数。只推首次公开近 30 天文献，近 7 天优先；期内未发候选可顺延。DOI、PMID、去版本 arXiv ID 精确去重，不按相似标题合并。预印本与正式版缺少明确关联时仍可能分别出现。

主题与期刊监控同时运行。需要 Crossref 补充时，填写 `source.journal.journals` 的期刊名和 ISSN，并把 `journal` 加入 `executor.source`；元数据缺摘要时保留等待，不能只根据标题编阅读卡。其他原有预印本来源可在 `config/base.yaml` 查看配置。

JCR/IF 可选接入 EasyScholar。默认仅展示，`filter_by_jcr=true` 才启用 Q1/Q2 优先、Q3 仅核心高度相关、Q4 排除、未知核心/重点刊例外；预印本不受分区限制。年份、学科口径未核实会明确标记，不把 ESCI 原始值冒充已核实 JCR。期刊名别称与接口覆盖可能导致未知；引用数来自 Crossref，可缺失，新论文不因引用少被排除。

推送周期、星期和时间由使用者添加工作流定时规则，模板默认仅手动运行。模型和第三方 API 可用性、费用及配额由各部署者维护。首次下载/计算可能明显慢于日常运行，不承诺按设定时刻准点到信。

## 本地运行

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，在项目根目录复制 `.env.example` 为 `.env` 并填写自己的值；不要复制维护者的私钥文件。随后：

```sh
uv sync --locked
uv run --locked src/zotero_arxiv_daily/main.py executor.dry_run=true
```

本地真实运行需显式设 `executor.dry_run=false`。GitHub 手动入口默认预览，定时任务启用后才真实发信。

## 失败和恢复

- 来源分页或保存失败不推进该查询检查点，下次重叠检索补回；已经保存的候选仍可参与推荐。
- 摘要筛选/阅读卡失败的候选保留重试，不把系统错误记成“不感兴趣”。
- GET/HEAD 不执行收藏或拒绝；确认 POST 有来源、CSRF 与一次性业务状态校验。链接有效期 90 天，持有者可操作，不能把邮件转发给不可信的人。
- 收藏失败可重新打开同一链接确认。预分配的 Zotero item key 和写入前去重用于处理响应丢失，避免重复条目；Zotero 要求退避时等待后再试。
- 负反馈目前只排除该论文，不训练模型或惩罚整个主题。未操作的旧推荐保持未处理，但不反复推送。

发信前批次置为 `sending`，成功后置为 `sent`。网络断开可能无法判断是否到达，此时不会自动重发。先看收件箱与 SMTP 记录，再检查：

```sh
uv run --locked python -m zotero_arxiv_daily.ops batches
```

**只有确认未送达**才执行（替换批次 ID）：

```sh
uv run --locked python -m zotero_arxiv_daily.ops release --batch BATCH_ID --confirm-not-delivered
```

释放会撤销原链接，使未处理候选可再次推荐；已到达的邮件不要释放。SMTP 不提供严格的一次投递保证。

## 备份

备份含个人记录与操作状态，必须私密保存，不提交仓库：

```sh
mkdir outputs
cd feedback
npx wrangler d1 export zetro-daily-state --remote --output=../outputs/state-backup.sql
```

在隔离环境验证恢复，不要直接导入正在使用的数据库。删除数据库会丢失去重历史。

## 修改和分享

保留 LICENSE/NOTICE 与上游署名，记录修改文件和日期。公开部署时 `feedback/wrangler.toml` 的 `SOURCE_URL` 应指向实际运行版本的对应源码；修改版本应及时提交到所提供的源码位置。源码应包括必需的部署/构建配置，但不需要包含真实密钥、个人数据或私有数据库。若给他人提供服务，需按 AGPL-3.0 履行源码提供义务。
