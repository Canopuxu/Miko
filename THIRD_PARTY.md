# 开源来源

- [SnowLuma](https://github.com/SnowLuma/SnowLuma)：现有本机运行服务；通过 `fetch_sys_faces` 接口读取 QQ 系统表情目录，不复制其源码或打包发行。上游采用源码可见非商业许可（非 OSI 开源许可）。

- [AstrBot Life Scheduler](https://github.com/muyouzhi6/astrbot_plugin_life_scheduler)：MIT，复用 `core/data.py` 的日期归一化、单日记录与 JSON 原子存取，适配为 Miko 简短生活条目。固定提交及改动见 `apps/backend/src/miko/adapters/life/source-manifest.json`，许可随模块保存。

本清单记录 Miko 使用及调研的开源源码。具体提交、模块路径与本地位置见
[`config/upstream.lock.json`](config/upstream.lock.json)。DSH 发行包、Tulpa QQ 与记忆流程、memU 检索、jieba 分词及 Memobase 中文整理模块已接入。实际使用的发行包记录在下表。

| 项目 | 来源 | 许可 | 选用部分 |
| --- | --- | --- | --- |
| DeepSeek Harness | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | MIT；[许可副本](licenses/deepseek-harness-LICENSE.txt) | Python SDK、运行时、界面扩展、上下文组装；独立原生 Web 工作台使用官方 npm @deepseek-ai/dsh 0.2.1-alpha.2 |
| memU | [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | Apache-2.0；[许可副本](licenses/memU-LICENSE.txt) | MemoryService、记忆提交与检索、SQLite 存储、Embedding 接口 |
| Memobase | [memodb-io/memobase](https://github.com/memodb-io/memobase) | Apache-2.0；[许可副本](licenses/memobase-LICENSE.txt) | 中文记忆提炼、合并与事件整理流程 |
| jieba | [fxsjy/jieba](https://github.com/fxsjy/jieba) | MIT；[许可副本](licenses/jieba-LICENSE.txt) | 中文分词 |
| Tulpa 本地 QQ 基线 | [fumingyang2004/Tulpa](https://github.com/fumingyang2004/Tulpa)；本地提交 `7b603d3` | MIT；[许可副本](licenses/tulpa-qq-baseline-LICENSE.txt) | QQ 接收、调度、发送确认；适配 MCP Chat 的消息段解析、原生引用校验、ChatTurns 气泡队列/节奏/回执恢复，以及 ChatMedia/StickerLibrary 的 QQ 图片下载、格式校验、采样编码、中文匹配和表情笔记；复用 snowluma_setup.py 的本机节点解析，自动读取已安装桥接的端口与 Token |
| SQLite | [sqlite.org](https://sqlite.org/) | Public Domain；[版权说明](https://sqlite.org/copyright.html) | 通过现有 Python 标准库 sqlite3 保存会话、消息与作者 |
| PyYAML 6.0.3 | [yaml/pyyaml](https://github.com/yaml/pyyaml) | MIT；[许可副本](licenses/python/pyyaml-LICENSE.txt) | Memobase 内嵌主题配置与隔离验证 |

上游根目录许可不替代仓库内其他组件或后续安装依赖各自的许可。
上游角色提示词保留供审阅；当前身份及 QQ 输出格式使用用户审核后的项目文件。
设定库保留 Tulpa 的中文双字匹配与全文检索，并接入 memU 语义检索；手动资料不作为模仿主人表达的范本。关系整理复用 Tulpa 流程，经独立 DSH 调用模型。当前固定版本的 memU 是仅使用 Embedding 的存储与检索服务，不承担模型记忆整理。

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
| Pillow | [12.3.0](https://pypi.org/project/Pillow/12.3.0/) | MIT-CMU；[许可副本](licenses/python/Pillow/LICENSE)及随包第三方声明 |
| pydantic | [2.14.1](https://pypi.org/project/pydantic/2.14.1/) | MIT |
| pydantic_core | [2.50.1](https://pypi.org/project/pydantic_core/2.50.1/) | MIT |
| setuptools | [82.0.1](https://pypi.org/project/setuptools/82.0.1/) | MIT |
| starlette | [1.7.0](https://pypi.org/project/starlette/1.7.0/) | BSD-3-Clause |
| typing-inspection | [0.4.4](https://pypi.org/project/typing-inspection/0.4.4/) | MIT |
| typing_extensions | [4.16.0](https://pypi.org/project/typing_extensions/4.16.0/) | PSF-2.0 |
| uvicorn | [0.54.0](https://pypi.org/project/uvicorn/0.54.0/) | BSD-3-Clause |
| websockets | [15.0.1](https://pypi.org/project/websockets/15.0.1/) | BSD-3-Clause；[许可副本](licenses/python/websockets/LICENSE) |

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

## 关系记忆与语义检索复用

- [Tulpa](https://github.com/fumingyang2004/Tulpa)：MIT，本地固定基线 `7b603d32bc72cab082a3b39aa52658b64bd506ff` 的 `chatlocal/tulpa.py`，复用收集、分批整理、修正、证据校验、历史与后台工作器；仅将工作器的旧全局配置检查接到 Miko。页面沿用原记忆面板的功能组织，移植到统一 React 页面。
- [memU](https://github.com/NevaMind-AI/memU)：Apache-2.0，固定源码 `dc7bda82b1f2a6429d9d1531ebdfd08d71e4e2b1`（0.11.0-beta.3），完整未修改 Python 包保存于 `packages/upstream/memu/`，实际调用 MemoryService、SQLite 与 Embedding 检索；未启用其宿主插件。
- [jieba](https://github.com/fxsjy/jieba)：MIT，0.42.1，用于 Tulpa 的中文分词。

- [ReMe](https://github.com/agentscope-ai/ReMe)：Apache-2.0，研究基线 `749c653f23bbe6791574dd8428fec6c49984574d`（0.4.1.13），原始源码位于 `MikoLocal/research/upstream/reme/`，仅用于独立的 Windows 本地记忆验证，尚未接入生产记忆系统。
- [Memobase](https://github.com/memodb-io/memobase)：Apache-2.0，模块抽取基线 `358c16bbc6d687937d79bc2f984a11c3be8da901`。复用中文提炼/提取/合并模板、条目归并与摘要模板、解析器、主题类型，以及 `merge_by_topic_sub_topics` / `deduplicate_profiles` 原函数。保留原始模板和解析函数；替换服务器配置依赖，抽取所需数据类型。来源、文件哈希及改动范围见内嵌模块的 `source-manifest.json`；许可副本随模块保存。
- 实际内嵌的 Memobase 模块位于 `apps/backend/src/miko/adapters/memory/memobase_upstream/`，许可与逐文件来源随包保留。五份原模板及项目任务说明已获用户批准用于正式整理。Miko 适配作者、时间、DSH 调用与 SQLite 提交；Tulpa 增加小批次整理、事务提交回调和后台待整理选择，保留原文校验与版本竞争检查。长条目摘要仅供页面浏览，完整条目继续参与检索。
- [AgentScope](https://github.com/agentscope-ai/agentscope)：Apache-2.0，2.0.9，ReMe 隔离验证采用上游原生模型封装与记忆 Agent 工具循环；未替换 Miko 的 DSH。隔离环境安装依赖的名称、版本、来源与许可元数据见 [dependencies.json](../MikoLocal/research/reme-evaluation/dependencies.json)。

新增 Python 依赖及许可记录在 `config/python-dependencies.lock.json`，许可副本在 `licenses/python/`。

- [alembic 1.20.0](https://pypi.org/project/alembic/1.20.0/)
- [httpcore2 2.13.1](https://pypi.org/project/httpcore2/2.13.1/)
- [httpx2 2.13.1](https://pypi.org/project/httpx2/2.13.1/)
- [jieba 0.42.1](https://pypi.org/project/jieba/0.42.1/)
- [jiter 0.17.0](https://pypi.org/project/jiter/0.17.0/)
- [Mako 1.4.3](https://pypi.org/project/Mako/1.4.3/)
- [MarkupSafe 3.0.4](https://pypi.org/project/MarkupSafe/3.0.4/)
- [numpy 2.5.4](https://pypi.org/project/numpy/2.5.4/)
- [openai 3.29.0](https://pypi.org/project/openai/3.29.0/)
- [pendulum 3.2.0](https://pypi.org/project/pendulum/3.2.0/)
- [python-dateutil 2.9.0.post0](https://pypi.org/project/python-dateutil/2.9.0.post0/)
- [six 1.17.0](https://pypi.org/project/six/1.17.0/)
- [sniffio 1.3.1](https://pypi.org/project/sniffio/1.3.1/)
- [SQLAlchemy 2.1.4](https://pypi.org/project/SQLAlchemy/2.1.4/)
- [sqlmodel 0.0.48](https://pypi.org/project/sqlmodel/0.0.48/)
- [truststore 0.10.4](https://pypi.org/project/truststore/0.10.4/)
- [tzdata 2026.5](https://pypi.org/project/tzdata/2026.5/)

## 生活轨迹图

- [React Flow](https://github.com/xyflow/xyflow)：@xyflow/react 12.12.0，MIT；复用画布、节点交互、连线、缩放与平移。
- [d3-hierarchy](https://github.com/d3/d3-hierarchy)：3.1.2，ISC；复用 tree / hierarchy，转换为径向坐标。
- 新增直接与传递依赖的固定版本及许可副本记录在 `config/web-dependencies.lock.json` 与 `licenses/npm/`。
  - [@types/d3-color 3.1.3](https://www.npmjs.com/package/@types/d3-color/v/3.1.3)：MIT。
  - [@types/d3-drag 3.0.7](https://www.npmjs.com/package/@types/d3-drag/v/3.0.7)：MIT。
  - [@types/d3-hierarchy 3.1.7](https://www.npmjs.com/package/@types/d3-hierarchy/v/3.1.7)：MIT。
  - [@types/d3-interpolate 3.0.4](https://www.npmjs.com/package/@types/d3-interpolate/v/3.0.4)：MIT。
  - [@types/d3-selection 3.0.12](https://www.npmjs.com/package/@types/d3-selection/v/3.0.12)：MIT。
  - [@types/d3-transition 3.0.9](https://www.npmjs.com/package/@types/d3-transition/v/3.0.9)：MIT。
  - [@types/d3-zoom 3.0.9](https://www.npmjs.com/package/@types/d3-zoom/v/3.0.9)：MIT。
  - [@xyflow/react 12.12.0](https://www.npmjs.com/package/@xyflow/react/v/12.12.0)：MIT。
  - [@xyflow/system 0.0.83](https://www.npmjs.com/package/@xyflow/system/v/0.0.83)：MIT。
  - [classcat 5.0.5](https://www.npmjs.com/package/classcat/v/5.0.5)：MIT。
  - [d3-color 3.1.0](https://www.npmjs.com/package/d3-color/v/3.1.0)：ISC。
  - [d3-dispatch 3.0.1](https://www.npmjs.com/package/d3-dispatch/v/3.0.1)：ISC。
  - [d3-drag 3.0.0](https://www.npmjs.com/package/d3-drag/v/3.0.0)：ISC。
  - [d3-ease 3.0.1](https://www.npmjs.com/package/d3-ease/v/3.0.1)：BSD-3-Clause。
  - [d3-hierarchy 3.1.2](https://www.npmjs.com/package/d3-hierarchy/v/3.1.2)：ISC。
  - [d3-interpolate 3.0.1](https://www.npmjs.com/package/d3-interpolate/v/3.0.1)：ISC。
  - [d3-selection 3.0.0](https://www.npmjs.com/package/d3-selection/v/3.0.0)：ISC。
  - [d3-timer 3.0.1](https://www.npmjs.com/package/d3-timer/v/3.0.1)：ISC。
  - [d3-transition 3.0.1](https://www.npmjs.com/package/d3-transition/v/3.0.1)：ISC。
  - [d3-zoom 3.0.0](https://www.npmjs.com/package/d3-zoom/v/3.0.0)：ISC。
  - [zustand 4.5.7](https://www.npmjs.com/package/zustand/v/4.5.7)：MIT。

## 统一界面与交互

- [Caveat](https://github.com/googlefonts/caveat)：SIL Open Font License 1.1；用于 Miko 手写字标。字体取自固定提交 `59745e818ef7973e11e70cb1358d0e902b56c5fc` 的 `fonts/variable/Caveat[wght].ttf`，原文件保存在 `apps/web/src/assets/fonts/Caveat.ttf`，许可副本见 `licenses/fonts/Caveat/OFL.txt`。

- [Lucide](https://github.com/lucide-icons/lucide)：lucide-react 1.56.0，ISC；复用现成 SVG 图标。
- [Radix Primitives](https://github.com/radix-ui/primitives)：Dialog 1.3.1、Alert Dialog 1.2.1，MIT；复用弹窗、键盘交互与焦点管理。
- 固定版本与许可副本见 `config/web-dependencies.lock.json` 与 `licenses/npm/`。
  - [@radix-ui/primitive 1.2.0](https://www.npmjs.com/package/@radix-ui/primitive/v/1.2.0)：MIT。
  - [@radix-ui/react-alert-dialog 1.2.1](https://www.npmjs.com/package/@radix-ui/react-alert-dialog/v/1.2.1)：MIT。
  - [@radix-ui/react-compose-refs 1.1.5](https://www.npmjs.com/package/@radix-ui/react-compose-refs/v/1.1.5)：MIT。
  - [@radix-ui/react-context 1.3.0](https://www.npmjs.com/package/@radix-ui/react-context/v/1.3.0)：MIT。
  - [@radix-ui/react-dialog 1.3.1](https://www.npmjs.com/package/@radix-ui/react-dialog/v/1.3.1)：MIT。
  - [@radix-ui/react-dismissable-layer 1.2.1](https://www.npmjs.com/package/@radix-ui/react-dismissable-layer/v/1.2.1)：MIT。
  - [@radix-ui/react-focus-guards 1.2.0](https://www.npmjs.com/package/@radix-ui/react-focus-guards/v/1.2.0)：MIT。
  - [@radix-ui/react-focus-scope 1.3.1](https://www.npmjs.com/package/@radix-ui/react-focus-scope/v/1.3.1)：MIT。
  - [@radix-ui/react-id 1.1.4](https://www.npmjs.com/package/@radix-ui/react-id/v/1.1.4)：MIT。
  - [@radix-ui/react-portal 1.2.0](https://www.npmjs.com/package/@radix-ui/react-portal/v/1.2.0)：MIT。
  - [@radix-ui/react-presence 1.1.11](https://www.npmjs.com/package/@radix-ui/react-presence/v/1.1.11)：MIT。
  - [@radix-ui/react-primitive 2.2.0](https://www.npmjs.com/package/@radix-ui/react-primitive/v/2.2.0)：MIT。
  - [@radix-ui/react-slot 1.5.0](https://www.npmjs.com/package/@radix-ui/react-slot/v/1.5.0)：MIT。
  - [@radix-ui/react-use-callback-ref 1.1.5](https://www.npmjs.com/package/@radix-ui/react-use-callback-ref/v/1.1.5)：MIT。
  - [@radix-ui/react-use-controllable-state 1.3.0](https://www.npmjs.com/package/@radix-ui/react-use-controllable-state/v/1.3.0)：MIT。
  - [@radix-ui/react-use-effect-event 0.0.5](https://www.npmjs.com/package/@radix-ui/react-use-effect-event/v/0.0.5)：MIT。
  - [@radix-ui/react-use-layout-effect 1.1.4](https://www.npmjs.com/package/@radix-ui/react-use-layout-effect/v/1.1.4)：MIT。
  - [aria-hidden 1.2.6](https://www.npmjs.com/package/aria-hidden/v/1.2.6)：MIT。
  - [detect-node-es 1.1.0](https://www.npmjs.com/package/detect-node-es/v/1.1.0)：MIT。
  - [get-nonce 1.0.1](https://www.npmjs.com/package/get-nonce/v/1.0.1)：MIT。
  - [lucide-react 1.56.0](https://www.npmjs.com/package/lucide-react/v/1.56.0)：ISC。
  - [react-remove-scroll 2.7.2](https://www.npmjs.com/package/react-remove-scroll/v/2.7.2)：MIT。
  - [react-remove-scroll-bar 2.3.8](https://www.npmjs.com/package/react-remove-scroll-bar/v/2.3.8)：MIT。
  - [react-style-singleton 2.2.3](https://www.npmjs.com/package/react-style-singleton/v/2.2.3)：MIT。
  - [tslib 2.8.1](https://www.npmjs.com/package/tslib/v/2.8.1)：0BSD。
  - [use-callback-ref 1.3.3](https://www.npmjs.com/package/use-callback-ref/v/1.3.3)：MIT。
  - [use-sidecar 1.1.3](https://www.npmjs.com/package/use-sidecar/v/1.1.3)：MIT。

- [react-masonry-css](https://github.com/paulcollett/react-masonry-css)：1.0.16，MIT；曾用于生活页卡片流，已从当前前端移除，保留历史致谢与许可副本。
- [React Flow](https://github.com/xyflow/xyflow)：@xyflow/react 12.12.0，MIT；生活页复用节点、连线和交互，适配为顶部汇总、按日期对齐的纵向目标分支；继续使用既有许可副本。
- [VTracer](https://github.com/visioncortex/vtracer)：Python 包 0.6.15，MIT；仅作为开发工具，将用户提供的 Logo 头像转换为平滑 SVG 路径，不作为应用运行依赖。
- Logo 字体：[Allura](https://github.com/google/fonts/tree/main/ofl/allura) 用于正式 Logo `apps/web/src/assets/miko-logo.svg`；[Marck Script](https://github.com/google/fonts/tree/main/ofl/marckscript)、[Cormorant Garamond](https://github.com/google/fonts/tree/main/ofl/cormorantgaramond) 用于 `design/logo-variants` 试排。均为 SIL Open Font License 1.1，文字已转轮廓，许可保存在 `licenses/fonts` 对应目录。
- SVG 文字转轮廓开发工具：[fontTools](https://github.com/fonttools/fonttools) 4.66.1，MIT；[uharfbuzz](https://github.com/harfbuzz/uharfbuzz) 0.56.3，Apache-2.0。仅用于离线生成，不作为应用运行依赖。
- [sharp](https://github.com/lovell/sharp)：0.35.5，Apache-2.0；仅使用开发环境自带工具，将既有 SVG 头像转为 QQ 头像测试用 PNG，不作为应用运行依赖。
