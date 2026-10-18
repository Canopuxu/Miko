# 第三方来源与许可

Miko 使用以下开源项目。固定版本和来源见 `config/upstream.lock.json`、`config/python-dependencies.lock.json` 与 `config/web-dependencies.lock.json`；许可副本保存在 `licenses/` 和内嵌模块目录中。

| 项目 | 来源 | 许可 | 用途 |
| --- | --- | --- | --- |
| DeepSeek Harness | https://github.com/deepseek-ai/deepseek-harness | MIT | Python SDK、运行时与开发版工作台界面组件 |
| Tulpa | https://github.com/fumingyang2004/Tulpa | MIT | QQ 会话调度、消息处理、原文回查、本地检索、表情库和群内词语理解；适配 Miko 数据与聊天范围 |
| memU | https://github.com/NevaMind-AI/memU | Apache-2.0 | 向量索引、语义检索与 SQLite 存储 |
| Memobase | https://github.com/memodb-io/memobase | Apache-2.0 | 中文记忆提炼、条目合并与整理模块 |
| AstrBot Life Scheduler | https://github.com/muyouzhi6/astrbot_plugin_life_scheduler | MIT | 生活记录的日期归一化与 JSON 存取 |
| Frida | https://github.com/frida/frida | wxWindows Library Licence 3.1 | 自建 QQ 桥接的客户端加载；随包保留许可及第三方声明 |
| CPython | https://www.python.org/ | PSF License Agreement | 便携版内置 Python 运行环境 |
| pywebview | https://github.com/r0x0r/pywebview | BSD-3-Clause | 便携版独立窗口，使用系统 WebView2 |
| pythonnet | https://github.com/pythonnet/pythonnet | MIT | Windows 窗口与 .NET 组件连接 |
| jieba | https://github.com/fxsjy/jieba | MIT | 中文分词 |
| PyYAML | https://github.com/yaml/pyyaml | MIT | 中文记忆主题配置 |
| React、React DOM | https://github.com/facebook/react | MIT | 用户界面 |
| React Flow | https://github.com/xyflow/xyflow | MIT | 生活轨迹节点、连线与交互 |
| Radix Primitives | https://github.com/radix-ui/primitives | MIT | 弹窗、焦点与键盘交互 |
| Lucide | https://github.com/lucide-icons/lucide | ISC | 界面图标 |
| Allura | https://github.com/google/fonts/tree/main/ofl/allura | SIL Open Font License 1.1 | 已转轮廓的正式 Logo 字标 |
| SQLite | https://sqlite.org/ | Public Domain | 通过 Python 标准库保存本地记录 |

Python 和前端的直接、间接依赖及其许可分别记录在上述依赖清单中，包括构建工具和第三方组件的传递依赖。内嵌源码的固定提交、文件哈希与适配范围见各模块的 `source-manifest.json` 或来源记录。

发布包保留实际随包组件的许可与第三方声明。

专用 Frida 构建使用 [PortableMSVC](https://github.com/tgbender/portablemsvc)（MIT）准备本地编译工具；该工具不随便携包分发。
