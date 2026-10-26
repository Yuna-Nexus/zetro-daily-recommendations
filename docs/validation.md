# 通用公开版本验证（2026-10-26）

- Python：147 项通过，1 项需要模型下载的慢测试未运行；包括按自定义非医学课题筛选、无固定医疗摘要上下文、非 AI 研究不被额外降权。
- Worker：13 项通过，包括公开源码入口、二次确认、来源/CSRF 校验、重复操作、响应丢失恢复、负反馈及 D1 调用预算。
- 本地离线联调：真实 Python HTTP → Worker 生产处理函数 → 内存 SQLite，完成生成邮件、确认收藏、拒绝、重复确认和下一轮不重发。检索/Embedding/AI/SMTP/Zotero 使用测试替身，不连接维护者或新用户的外部账号。
- `uv lock --check` 通过；Python 源码包及 wheel 构建通过；Wrangler `deploy --dry-run` 编译通过。这里没有把通用示例配置部署到维护者的 Cloudflare。
- `LICENSE` 与上游许可证逐字节一致；`README.upstream.md` 与声明的上游版本逐字节一致。修改作者、日期和范围见 NOTICE/MODIFICATIONS 及源文件头。
- 发布前已检查新快照、暂存文件和全部 93 个历史文件对象中的维护者已知密钥及部署标识，未发现匹配。不复制个人 Git 历史、私钥文件、数据库或运行预览。
- 新仓库不配置任何生产密钥，`ENABLE_SCHEDULE` 默认未设置即关闭。各部署者必须完成自己的 API、SMTP、检索质量及真实确认收藏验证，再开启定时。

云端 CI 和公开状态以本仓库 Actions / GitHub 页面为准；该记录不保证其他账号、第三方 API 配额或网络始终可用。

- [新仓库云端 CI](https://github.com/Yuna-Nexus/zetro-daily-recommendations/actions/runs/40257352595) 通过：Python、Worker 以及离线端到端步骤全部成功。仓库已公开并启用模板创建；查询确认生产 Secrets 和 Variables 均为 0。源码版本 2aaee00；后续只更新部署样例/验证记录，没有改动已验证的运行逻辑。
