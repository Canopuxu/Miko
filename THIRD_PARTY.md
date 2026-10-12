# 开源来源

本清单记录 Miko 开发准备阶段下载的源码。具体提交、模块路径与本地位置见
[`config/upstream.lock.json`](config/upstream.lock.json)。这些调研源码尚未直接接入应用；第一阶段使用的发行包记录在下表。

| 项目 | 来源 | 许可 | 选用部分 |
| --- | --- | --- | --- |
| DeepSeek Harness | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | MIT；[许可副本](licenses/deepseek-harness-LICENSE.txt) | Python SDK、运行时、界面扩展、上下文组装；MCP 接口供后续使用 |
| memU | [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | Apache-2.0；[许可副本](licenses/memU-LICENSE.txt) | MemoryService、记忆提交与检索、SQLite 存储、Embedding 接口 |
| Memobase | [memodb-io/memobase](https://github.com/memodb-io/memobase) | Apache-2.0；[许可副本](licenses/memobase-LICENSE.txt) | 中文记忆提炼、合并与事件整理流程 |
| jieba | [fxsjy/jieba](https://github.com/fxsjy/jieba) | MIT；[许可副本](licenses/jieba-LICENSE.txt) | 中文分词 |
| Tulpa 本地 QQ 基线 | [fumingyang2004/Tulpa](https://github.com/fumingyang2004/Tulpa)；本地提交 `7b603d3` | MIT；[许可副本](licenses/tulpa-qq-baseline-LICENSE.txt) | 已验证的 QQ 接收、调度、发送确认及 DSH 桥接流程 |

上游根目录许可不替代仓库内其他组件或后续安装依赖各自的许可。
源码中的提示词保留供审阅，尚未采用或启用。

## 第一阶段 Python 依赖

DSH SDK 与 Windows runtime 均使用官方 PyPI 发行版 `0.1.5rc1`，与上方固定的较新调研源码分开记录。
两个 wheel 的 SHA-256 已核对；许可副本保存在 `licenses/python/`，包含 runtime 随附的第三方声明。
全部直接与间接依赖的固定版本见 `requirements.lock.txt`，来源及许可文件见 `config/python-dependencies.lock.json`。

| 名称 | 版本及来源 | 许可 |
| --- | --- | --- |
| annotated-doc | [0.0.5](https://pypi.org/project/annotated-doc/0.0.5/) | MIT |
| annotated-types | [0.8.0](https://pypi.org/project/annotated-types/0.8.0/) | MIT |
| anyio | [4.15.1](https://pypi.org/project/anyio/4.15.1/) | MIT |
| click | [8.5.0](https://pypi.org/project/click/8.5.0/) | BSD-3-Clause |
| deepseek-harness-runtime-bin | [0.1.5rc1](https://pypi.org/project/deepseek-harness-runtime-bin/0.1.5rc1/) | MIT |
| deepseek-harness-sdk | [0.1.5rc1](https://pypi.org/project/deepseek-harness-sdk/0.1.5rc1/) | MIT |
| fastapi | [0.143.0](https://pypi.org/project/fastapi/0.143.0/) | MIT |
| h11 | [0.16.0](https://pypi.org/project/h11/0.16.0/) | MIT |
| idna | [3.20](https://pypi.org/project/idna/3.20/) | BSD-3-Clause |
| opentelemetry-api | [1.45.1](https://pypi.org/project/opentelemetry-api/1.45.1/) | Apache-2.0 |
| psutil | [7.2.2](https://pypi.org/project/psutil/7.2.2/) | BSD-3-Clause |
| pydantic | [2.14.1](https://pypi.org/project/pydantic/2.14.1/) | MIT |
| pydantic_core | [2.50.1](https://pypi.org/project/pydantic_core/2.50.1/) | MIT |
| setuptools | [82.0.1](https://pypi.org/project/setuptools/82.0.1/) | MIT |
| starlette | [1.7.0](https://pypi.org/project/starlette/1.7.0/) | BSD-3-Clause |
| typing-inspection | [0.4.4](https://pypi.org/project/typing-inspection/0.4.4/) | MIT |
| typing_extensions | [4.16.0](https://pypi.org/project/typing_extensions/4.16.0/) | PSF-2.0 |
| uvicorn | [0.54.0](https://pypi.org/project/uvicorn/0.54.0/) | BSD-3-Clause |
