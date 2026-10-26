# 部署指南

准备项目首页列出的账号和 Key。首次部署建议让 AI 协助执行；日常使用只需收邮件、点击并确认。

## 1. 建立自己的仓库

从本项目 GitHub 页面 **Fork**，或选择 **Use this template** 创建副本；需要保密研究方向时，使用模板创建私有仓库。启用 Actions。不要直接使用维护者的部署资源。

编辑 `config/custom.yaml`：

- `topics`：自己的主题名称、研究描述、arXiv/PubMed 检索式。
- `screening.profile`：哪些直接有用、哪些可借鉴、哪些不要；这是入排标准。
- `source.pubmed.journals`：关注期刊，主题检索与期刊检索同时存在；不需要时用 `[]`。
- `zotero.include_path`：提供兴趣参考的 Collection 路径；也可设 `null` 使用全库。
- `email.smtp_port/tls_mode`：默认 SSL 465；要求 STARTTLS 时改为 587 / `starttls`。

示例课题可以改成其他学科；PubMed 覆盖生物医学，其他学科主要使用 arXiv。禁用不需要的来源时修改 `executor.source`，原有其他来源仍保留代码与基础配置。

## 2. 准备 Zotero

1. 在个人文献库新建 `AI Paper Recommendations` Collection（也可自定义名称），同步到 Zotero 云端。
2. 在 [Zotero Keys 页面](https://www.zotero.org/settings/keys) 获取数字 **User ID**，创建只读 Key，以及单独的文献库读写 Key。不要开启无关组库权限。
3. 打开该 Collection 的 Zotero 网页，地址形如 `.../collections/ABCDEFGH`，末尾八位为 **Collection key**。不能用名称代替 key。

本版 Python 兴趣读取针对个人库，Worker 也设置 `ZOTERO_LIBRARY_TYPE="user"`。库权限不能细分到单个 Collection，应用在服务端限制目标 Collection。

## 3. 部署 Worker 和 D1

在自己的电脑安装 [Node.js 24+](https://nodejs.org/en/download)、[Git](https://git-scm.com/downloads)，下载自己的仓库：

```sh
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY/feedback
npm ci
npx wrangler login
npx wrangler d1 create zetro-daily-state
```

登录自己的 Cloudflare。将命令返回的数据库 ID 写入 `feedback/wrangler.toml`，同时替换：

| 字段 | 填什么 |
|---|---|
| `ZOTERO_LIBRARY_ID` | Zotero 数字 User ID |
| `ZOTERO_COLLECTION` | 八位 Collection key |
| `database_id` | 刚创建的 D1 ID，不能保留全零示例 |
| `SOURCE_URL` | 自己部署版本的源码仓库地址；应能让使用者获取对应源码 |

不要在文件中填写 Key。创建表，生成一个至少 32 字符的随机 `FEEDBACK_API_TOKEN`（例如 `openssl rand -hex 32`；也可让 AI 在本地生成），然后执行：

```sh
npx wrangler d1 execute zetro-daily-state --remote --file=schema.sql
npx wrangler secret put FEEDBACK_API_TOKEN
npx wrangler secret put ZOTERO_WRITE_KEY
npx wrangler deploy
```

两次 `secret put` 分别粘贴随机令牌和独立 Zotero 读写 Key。保存部署输出的 `https://...workers.dev` 地址；**`FEEDBACK_BASE_URL` 使用这个根地址，不带 `/confirm`**。打开 `/source` 应能访问你指定的源码。

这里仅首次部署需要本地命令。电脑以后不必常开；不要将 `test/local-entry.js` 测试入口部署到云端。

## 4. 配置 GitHub

在自己的仓库 **Settings → Secrets and variables → Actions** 中添加：

**Secrets（加密密钥与邮箱配置）：**

| 名称 | 值 |
|---|---|
| `ZOTERO_ID` | Zotero User ID |
| `ZOTERO_KEY` | Zotero 只读 Key |
| `SENDER`、`RECEIVER` | 发件和收件邮箱；可以相同 |
| `SMTP_SERVER` | 邮箱供应商的 SMTP 主机，例如 `smtp.163.com` |
| `SENDER_PASSWORD` | SMTP 授权码或供应商要求的凭据 |
| `OPENAI_API_KEY` | 标准 AI API Key，不限定供应商 |
| `OPENAI_API_BASE` | 供应商 SDK 兼容地址，按其文档确定是否包含 `/v1` |
| `FEEDBACK_API_TOKEN` | 与 Worker 中完全相同的随机令牌 |
| `EASYSCHOLAR_SECRET_KEY`（可选） | EasyScholar Key；没有则显示指标未知 |
| `NCBI_EMAIL`、`NCBI_API_KEY`（可选） | NCBI 联系邮箱和 API Key |

**Variables（普通配置）：**

| 名称 | 值 |
|---|---|
| `FEEDBACK_BASE_URL` | 自己部署的 Worker HTTPS 根地址 |
| `LLM_MODEL` | 供应商实际可调用的模型名称 |
| `ENABLE_SCHEDULE` | 先填 `false`，全部验证后改为 `true` |
| `CUSTOM_CONFIG`（可选） | 完整 YAML；未填写时直接使用仓库 `config/custom.yaml` |

AI API 需要兼容 Chat Completions；如有额外生成参数，修改 `llm.generation_kwargs`。模型第一次下载和向量计算可能较慢，后续利用 D1 缓存。云端默认使用原 Embedding 模型，不需要额外 Embedding Key。

默认 Jina 模型权重采用 CC BY-NC 4.0，仅凭本项目的 AGPL 源码授权不能商用模型。商业部署先取得模型许可，或替换 `reranker.local.model`/使用许可合适的 Embedding 服务，并按模型要求调整编码参数、重新验证效果；详见 [NOTICE](../NOTICE.md)。

不要将 `ZOTERO_WRITE_KEY` 放进 Actions，也不要把 `.env`、`.dev.vars`、数据库备份上传。公开仓库的日志与预览不是私密存储；有保密要求使用私有部署仓库。[GitHub Artifact 可见性说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。

## 5. 先验证，再开启定时

1. Actions → **Daily literature recommendations** → **Run workflow**，保持 `dry_run=true`、`prepare_only=false`。成功后下载 `daily-preview`，打开 HTML，检查检索结果和摘要。
2. 可选预热：取消 `dry_run`，勾选 `prepare_only`。此模式保存候选和缓存，但不发邮件、不创建收藏链接、不写入 Zotero。两个选项不能同时勾选。
3. 使用测试 Collection 联调：两个选项都取消勾选，运行一次真实邮件；确认邮件到达、JCR/IF 显示符合预期。
4. 从邮件点击保存：打开页面尚不入库，点“确认收藏”后才保存。重复确认不应新增条目；不操作的论文保持未处理。验证后在 Worker 配置中切回正式 Collection 并重新部署。
5. 把 `ENABLE_SCHEDULE` 改成 `true`。默认周一至周六北京时间 08:00 开始，处理完成后发送；没有合格论文时不发。

取消定时只需将该变量改回 `false`。手动运行入口仍然可用。不同部署各自维护去重状态，删除或重建 D1 会丢失推送历史。

如要改时间，编辑 `.github/workflows/main.yml` 的 cron，使用 **UTC**；北京时间减八小时。公开仓库长时间无活动可能被 GitHub 停用定时任务，定期查看 Actions 状态；详见[官方定时说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
