# 开源来源

本清单记录 Miko 开发准备阶段下载的源码。具体提交、模块路径与本地位置见
[`config/upstream.lock.json`](config/upstream.lock.json)。这些源码目前尚未接入应用。

| 项目 | 来源 | 许可 | 选用部分 |
| --- | --- | --- | --- |
| DeepSeek Harness | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | MIT；[许可副本](licenses/deepseek-harness-LICENSE.txt) | Python SDK、运行时、界面扩展、上下文组装；MCP 接口供后续使用 |
| memU | [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | Apache-2.0；[许可副本](licenses/memU-LICENSE.txt) | MemoryService、记忆提交与检索、SQLite 存储、Embedding 接口 |
| Memobase | [memodb-io/memobase](https://github.com/memodb-io/memobase) | Apache-2.0；[许可副本](licenses/memobase-LICENSE.txt) | 中文记忆提炼、合并与事件整理流程 |
| jieba | [fxsjy/jieba](https://github.com/fxsjy/jieba) | MIT；[许可副本](licenses/jieba-LICENSE.txt) | 中文分词 |
| Tulpa 本地 QQ 基线 | [fumingyang2004/Tulpa](https://github.com/fumingyang2004/Tulpa)；本地提交 `7b603d3` | MIT；[许可副本](licenses/tulpa-qq-baseline-LICENSE.txt) | 已验证的 QQ 接收、调度、发送确认及 DSH 桥接流程 |

上游根目录许可不替代仓库内其他组件或后续安装依赖各自的许可。
源码中的提示词保留供审阅，尚未采用或启用。
