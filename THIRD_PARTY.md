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
| SQLite | [sqlite.org](https://sqlite.org/) | Public Domain；[版权说明](https://sqlite.org/copyright.html) | 通过现有 Python 标准库 sqlite3 保存会话、消息与作者 |

上游根目录许可不替代仓库内其他组件或后续安装依赖各自的许可。
源码中的提示词保留供审阅，尚未采用或启用。

## Python 依赖

DSH SDK 与 Windows runtime 均使用官方 PyPI 发行版 `0.1.5rc1`，与上方固定的较新调研源码分开记录。
两个 wheel 的 SHA-256 已核对；许可副本保存在 `licenses/python/`，包含 runtime 随附的第三方声明。
全部直接与间接依赖的固定版本见 `requirements.lock.txt`，来源及许可文件见 `config/python-dependencies.lock.json`。
第二阶段新增 HTTPX、httpcore 和 certifi，用于模型接口的流式桥接。

| 名称 | 版本及来源 | 许可 |
| --- | --- | --- |
| annotated-doc | [0.0.5](https://pypi.org/project/annotated-doc/0.0.5/) | MIT |
| annotated-types | [0.8.0](https://pypi.org/project/annotated-types/0.8.0/) | MIT |
| anyio | [4.15.1](https://pypi.org/project/anyio/4.15.1/) | MIT |
| certifi | [2026.7.22](https://pypi.org/project/certifi/2026.7.22/) | MPL-2.0 |
| click | [8.5.0](https://pypi.org/project/click/8.5.0/) | BSD-3-Clause |
| deepseek-harness-runtime-bin | [0.1.5rc1](https://pypi.org/project/deepseek-harness-runtime-bin/0.1.5rc1/) | MIT |
| deepseek-harness-sdk | [0.1.5rc1](https://pypi.org/project/deepseek-harness-sdk/0.1.5rc1/) | MIT |
| fastapi | [0.143.0](https://pypi.org/project/fastapi/0.143.0/) | MIT |
| h11 | [0.16.0](https://pypi.org/project/h11/0.16.0/) | MIT |
| httpcore | [1.0.9](https://pypi.org/project/httpcore/1.0.9/) | BSD-3-Clause |
| httpx | [0.28.1](https://pypi.org/project/httpx/0.28.1/) | BSD-3-Clause |
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

## 第二阶段 Web 依赖

实际复用官方 npm 发布的 DSH UI Renderer、UI Slots 和 Store（`0.2.1-alpha.2`），以及 Cordis；前端不使用 DSH 服务端会话协议。`apps/web/vite.config.ts` 仅在构建时将官方客户端的注册工厂转换为 ESM。
固定版本与完整性哈希见 `pnpm-lock.yaml`；当前 Windows 安装的直接及间接依赖、许可副本见 `config/web-dependencies.lock.json` 和 `licenses/npm/`。
原生 esbuild、Rollup 平台包沿用同一发行版主包的许可，见清单中的 `licenseFrom`。

| 名称 | 版本及来源 | 许可 |
| --- | --- | --- |
| @deepseek-ai/cordis | [4.0.5-alpha.1](https://www.npmjs.com/package/@deepseek-ai/cordis/v/4.0.5-alpha.1) | MIT |
| @deepseek-ai/cosmokit | [1.8.6-alpha.1](https://www.npmjs.com/package/@deepseek-ai/cosmokit/v/1.8.6-alpha.1) | MIT |
| @deepseek-ai/dsh-client-store | [0.2.1-alpha.2](https://www.npmjs.com/package/@deepseek-ai/dsh-client-store/v/0.2.1-alpha.2) | MIT |
| @deepseek-ai/dsh-client-ui-renderer | [0.2.1-alpha.2](https://www.npmjs.com/package/@deepseek-ai/dsh-client-ui-renderer/v/0.2.1-alpha.2) | MIT |
| @deepseek-ai/dsh-client-ui-slots | [0.2.1-alpha.2](https://www.npmjs.com/package/@deepseek-ai/dsh-client-ui-slots/v/0.2.1-alpha.2) | MIT |
| @esbuild/win32-x64 | [0.25.12](https://www.npmjs.com/package/@esbuild/win32-x64/v/0.25.12) | MIT |
| @rollup/rollup-win32-x64-gnu | [4.64.5](https://www.npmjs.com/package/@rollup/rollup-win32-x64-gnu/v/4.64.5) | MIT |
| @rollup/rollup-win32-x64-msvc | [4.64.5](https://www.npmjs.com/package/@rollup/rollup-win32-x64-msvc/v/4.64.5) | MIT |
| @standard-schema/spec | [1.1.0](https://www.npmjs.com/package/@standard-schema/spec/v/1.1.0) | MIT |
| @types/estree | [1.0.9](https://www.npmjs.com/package/@types/estree/v/1.0.9) | MIT |
| @types/prop-types | [15.7.15](https://www.npmjs.com/package/@types/prop-types/v/15.7.15) | MIT |
| @types/react | [18.3.1](https://www.npmjs.com/package/@types/react/v/18.3.1) | MIT |
| @types/react-dom | [18.3.0](https://www.npmjs.com/package/@types/react-dom/v/18.3.0) | MIT |
| csstype | [3.2.3](https://www.npmjs.com/package/csstype/v/3.2.3) | MIT |
| esbuild | [0.25.12](https://www.npmjs.com/package/esbuild/v/0.25.12) | MIT |
| fdir | [6.5.0](https://www.npmjs.com/package/fdir/v/6.5.0) | MIT |
| js-tokens | [4.0.0](https://www.npmjs.com/package/js-tokens/v/4.0.0) | MIT |
| loose-envify | [1.4.0](https://www.npmjs.com/package/loose-envify/v/1.4.0) | MIT |
| nanoid | [3.3.20](https://www.npmjs.com/package/nanoid/v/3.3.20) | MIT |
| picocolors | [1.1.1](https://www.npmjs.com/package/picocolors/v/1.1.1) | ISC |
| picomatch | [4.0.7](https://www.npmjs.com/package/picomatch/v/4.0.7) | MIT |
| postcss | [8.5.29](https://www.npmjs.com/package/postcss/v/8.5.29) | MIT |
| react | [18.3.1](https://www.npmjs.com/package/react/v/18.3.1) | MIT |
| react-dom | [18.3.1](https://www.npmjs.com/package/react-dom/v/18.3.1) | MIT |
| rollup | [4.64.5](https://www.npmjs.com/package/rollup/v/4.64.5) | MIT |
| scheduler | [0.23.2](https://www.npmjs.com/package/scheduler/v/0.23.2) | MIT |
| source-map-js | [1.2.2](https://www.npmjs.com/package/source-map-js/v/1.2.2) | BSD-3-Clause |
| tinyglobby | [0.2.17](https://www.npmjs.com/package/tinyglobby/v/0.2.17) | MIT |
| typescript | [5.9.3](https://www.npmjs.com/package/typescript/v/5.9.3) | Apache-2.0 |
| use-sync-external-store | [1.6.0](https://www.npmjs.com/package/use-sync-external-store/v/1.6.0) | MIT |
| vite | [6.4.3](https://www.npmjs.com/package/vite/v/6.4.3) | MIT |
