# zetro-daily-recommendations

自动检索文献、按你的课题推荐、邮件初筛，**经你确认后才保存到 Zotero**。

基于 [TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily) 二次开发，采用 [AGPL-3.0](LICENSE)。原作者及贡献者的工作和声明保留在 [NOTICE.md](NOTICE.md) 与 [上游 README](README.upstream.md)。

## 能做什么

- 检索 arXiv、PubMed，主题检索与指定期刊监控同时进行；可选 Crossref 补充期刊覆盖。
- 研究任务描述为主、Zotero 历史收藏为辅，结合 Embedding 和缓存的摘要筛选进行排序。
- 邮件展示中文阅读卡、推荐理由、原文链接；可获取时同时展示 JCR、IF 和引用数。
- 推荐篇数上限、核心/交叉/探索比例由你设置；不凑数，已推送不重复，剩余合格候选可顺延。
- 点击“保存”或“不感兴趣”后还需确认。未操作保持未处理；默认不下载 PDF。
- GitHub Actions 定时运行，Cloudflare Worker + D1 保存状态和处理确认；电脑、Codex、Zotero 均无需一直开启。

**推送周期、星期和时间由你设置，模板默认只提供手动运行。**部署时先填写篇数上限；分组比例可设置，也可不限制。邮件在处理完成后发送，GitHub 可能延迟调度。新文献默认指首次发表近 30 天。

## 你需要准备什么

| 准备项 | 到哪里办理 / 获取什么 |
|---|---|
| GitHub 账号 | [注册或登录](https://github.com/signup)，创建自己的仓库 |
| Cloudflare 账号 | [注册](https://dash.cloudflare.com/sign-up)，部署自己的 Worker 和 D1 |
| Zotero 账号及一个 Collection | [注册](https://www.zotero.org/user/register/)，[创建 API Key、获取 User ID](https://www.zotero.org/settings/keys)；分别准备只读 Key 和独立读写 Key |
| 可以发邮件的邮箱 | 在供应商设置中开启 SMTP，准备服务器、端口、加密方式和授权码；可以发给同一个邮箱 |
| 标准 AI API | 准备 API Key、Base URL 和模型名；支持本项目所用的 Chat Completions 接口，订阅套餐不自动等同于 API 权限 |
| 研究需求 | 课题描述、关键词/检索式，可选关注期刊；修改示例配置即可 |
| EasyScholar（可选） | [开放接口](https://www.easyscholar.cc/console/user/open)，获取 Key 用于 JCR 和 IF |

各人使用自己的账号、密钥和数据库。代码公开不意味着免费提供维护者的 AI 或邮件服务；实际费用取决于所选服务与用量。

## 怎么部署

照着 **[部署指南](docs/deploy.md)** 做即可：

1. 创建自己的副本，修改示例课题、推荐篇数及可选分组比例。
2. 部署自己的 Worker/D1，配置 Zotero Collection。
3. 把密钥放入 Secrets，先预览、再测试确认收藏。
4. 按自己的时间添加定时规则，验证后设 `ENABLE_SCHEDULE=true`，以后等邮件。

如果让 AI 帮忙，可直接复制：

> 请按本仓库 docs/deploy.md 帮我部署。我会准备 GitHub、Cloudflare、Zotero、SMTP、标准 AI API 和研究主题。请先问我每次最多推荐几篇、是否限制核心/交叉/探索比例、推送星期和时间及所在时区，不要替我套用固定设置。所有密钥只写入 Secrets 或被忽略的本地文件，不发到聊天或提交 Git。先通过 dry_run 预览，再由我测试邮件确认收藏，最后启用我选定的定时规则。请保留 AGPL 许可证、上游署名和实际部署版本的源码入口。

公开仓库的 Actions 日志和预览 Artifact 对有读取权限的人可见，预览也可能包含收藏匹配理由；需要保密研究方向时，使用私有部署仓库。密钥、D1 数据库和邮件收藏令牌不应公开。

## 开发与维护

Python 3.13+、uv、Node.js 24+。内部 Python 模块名继续使用 `zotero_arxiv_daily`，避免无关改名。

默认下载的 [Jina 语义模型](https://huggingface.co/jinaai/jina-embeddings-v5-text-nano-retrieval)采用 **CC BY-NC 4.0**，模型权重不随仓库分发。项目源码的 AGPL 许可不授予该模型的商用权；商业部署需另获模型授权，或换用许可合适的模型/服务。见 [NOTICE.md](NOTICE.md)。

```sh
uv sync --locked
uv run --locked pytest
node --test feedback/test/*.test.js
```

[配置、失败恢复与备份](docs/operations.md) · [公开版本验证](docs/validation.md)

## 许可证与来源

原始项目由 **TideDra 及其贡献者**开发；本衍生版本由 **Yuna-Nexus**维护，主要增加 PubMed、期刊指标、持久化去重、可配置筛选与人工确认入库。上游不代表认可本衍生版本。

请保留 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md) 和修改说明；[MODIFICATIONS.md](MODIFICATIONS.md) 记录来源版本、修改日期和文件范围。再次修改/分发时遵守 AGPL-3.0，网络交互用户应能获取实际运行版本的对应源码。第三方依赖和模型保留各自许可。本软件不提供担保。
