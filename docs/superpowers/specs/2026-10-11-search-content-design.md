# P1.1 有界正文关键词检索设计

日期：2026-10-11

状态：已确认。实施计划见 `docs/superpowers/plans/2026-10-11-search-content.md`。本文只定义 `search_content`。

## 1. 目标

在现有只读 WebDAV 扫描上增加独立工具 `search_content`。它按正文的连续子串查找，返回来源相对路径、行号和有限片段。调用方再用已有的 `read_file` 读取原文。

本阶段要证明的行为：文件名不含关键词、正文含关键词时必须命中；正文不含关键词时不得因文件名命中。

## 2. 范围

本阶段只做同步、有界、不落盘的正文扫描。

包含：

- 新工具 `search_content`
- 无 I/O 的匹配与片段函数
- 本地 WebDAV 测试和真实 MCP 子进程测试
- 实现完成后再更新 README、`docs/architecture.md`、`docs/installation.md` 和路线图 4.1 的勾选

不包含：

- P0 的 Cursor 四工具验收、PROPFIND 子项可靠性补丁，以及路线图第 3 节其余未勾选项。这些保持未勾选
- 增量索引、缓存、后台同步、数据库和全文引擎
- Markdown 结构解析、PDF、Embedding、RAG、Docker 和对外发布
- 上传、覆盖、移动、删除
- 改写 `search_file`。它仍然只匹配文件名，返回字段不变
- 新的第三方依赖

许可证保持 Apache 2.0，归属 `Copyright 2026 Neville`。当前目录没有 Git 仓库；实现时也不为了这份设计去初始化仓库或提交。

## 3. 已确认的契约

### 3.1 命中

- `keyword` 整段是一个连续子串。空格属于子串，不拆词，不做中文分词
- 比较时两边都做 `str.casefold()`。中文按字符比较；英文字母忽略大小写
- 空白串（`strip` 之后为空）或长度超过 256 的关键词，在任何网络请求之前拒绝
- 通过空白检查后，匹配使用原始 `keyword.casefold()`。关键词 `" a "` 要求两侧空格。关键词 `"a b"` 不能命中正文 `"ab"`，关键词 `"ab"` 也不能命中正文 `"a b"`
- 匹配对象是解码后的全文，命中可以跨过换行
- 同文件内的命中不重叠：下一次查找从上一处命中在原文中的结束位置之后开始，至少前进一个原文字符
- 同一行上的多处命中各占一条结果，行号相同，片段按各自起点截取
- 结果顺序是目录广度优先，同一文件内按出现位置。没有相关度排序。一份文件可以占满 `max_results`

### 3.2 扫描哪些文件

下载并匹配的后缀与 `read_file` 相同：`.md`、`.markdown`、`.txt`、`.json`、`.csv`、`.yaml`、`.yml`、`.rst`。比较时忽略大小写。

其他文件不下载，也不记入 `file_errors`。目录继续按预算遍历。

### 3.3 组件

- `src/nutstore_mcp/content.py`：纯函数 `find_snippets(text, keyword, *, max_snippet_chars=200) -> list[dict]`，不发网络请求，不读配置。每条结果含 `line`、`snippet`、`snippet_truncated`，不含路径。`keyword` 必须已通过非空和长度检查；非法时抛出 `ValueError`，不能返回空列表冒充无命中
- `src/nutstore_mcp/webdav.py`：目录遍历和有界下载。`search_content` 是 `NutstoreWebDAV` 的方法
- `src/nutstore_mcp/server.py`：注册工具并原样返回字典
- `src/nutstore_mcp/smoke.py`：默认只检查工具已注册，不自动调用正文搜索

目录遍历收成 `webdav.py` 的私有函数，`search_file` 和 `search_content` 都调用它。文件名匹配留在 `search_file`，正文下载只发生在 `search_content`。现有 `search_file` 测试必须原样通过。两个工具各自校验自己的预算上限，遍历函数不把某一工具的硬上限套到另一个工具上。

正文搜索不调用公开的 `read_file`。公开方法仍然是超限即失败、不返回残缺正文。搜索使用内部读取，解码规则与 `read_file` 相同：不跟随重定向、UTF-8（含 BOM）、拒绝二进制控制字符（允许 `\t`、`\n`、`\r`、`\f`）。

## 4. 工具接口

```text
search_content(
    keyword: str,
    path: str = "",
    recursive: bool = True,
    max_depth: int = 3,
    max_results: int = 20,
    max_directories: int = 50,
    max_entries: int = 2000,
    max_files: int = 30,
    max_file_bytes: int = 262144,
    max_total_bytes: int = 1048576,
    max_seconds: int = 15,
) -> dict
```

路径规则与现有工具相同：相对于 WebDAV 根目录，拒绝绝对路径、盘符、`..`、反斜杠和百分号编码。

| 参数 | 默认 | 允许范围 |
| --- | --- | --- |
| `keyword` | 必填 | 1–256 个字符，且 `strip` 后非空 |
| `max_depth` | 3 | 0–10 |
| `max_results` | 20 | 1–100 |
| `max_directories` | 50 | 1–200 |
| `max_entries` | 2000 | 1–10000 |
| `max_files` | 30 | 1–100 |
| `max_file_bytes` | 262144 | 1–1048576 |
| `max_total_bytes` | 1048576 | 1–4194304 |
| `max_seconds` | 15 | 1–60 |

非法参数、非法路径在任何 PROPFIND 或 GET 之前抛出 `ValueError`。布尔值不能当作整数预算。

`max_files` 统计准备发起 GET 的文本后缀文件，包含随后读失败的文件。非文本文件不计入。达到上限后，不再打开下一个文本文件。

成功返回：

```text
{
  "matches": [
    {
      "path": "notes/topic.md",
      "line": 12,
      "snippet": "命中所在行的有限文本",
      "snippet_truncated": false
    }
  ],
  "incomplete": false,
  "truncated": false,
  "limit_reasons": [],
  "file_errors": [],
  "scanned_directories": 1,
  "scanned_entries": 4,
  "scanned_files": 2,
  "downloaded_bytes": 800
}
```

字段含义：

- `line`：命中起点的 1-based 行号，按 `splitlines(keepends=True)` 计算
- `snippet`：起点所在行去掉行尾换行后的文本。该行不超过 200 个字符时返回整行。更长时，以命中落在该行上的那一段为中心取 200 个字符，并贴住行首或行尾。若这一段本身不少于 200 个字符，从它在该行上的起点取前 200 个字符。窗口不追加省略号
- `snippet_truncated`：片段短于该行时为 `true`
- `truncated`：只表示扫描预算用尽，`limit_reasons` 非空
- `incomplete`：`truncated` 为真，或 `file_errors` 非空
- `limit_reasons`：排序后的预算名称，取自 `max_depth`、`max_results`、`max_directories`、`max_entries`、`max_files`、`max_total_bytes`、`max_seconds`
- `file_errors`：`{"path", "reason"}`。`path` 与 `matches` 一样，是相对于 WebDAV 根目录的路径。`reason` 只允许 `not_utf8`、`binary`、`too_large`、`forbidden`、`not_found`、`read_failed`
- `scanned_files`：完整解码并完成查找的文件数。读失败、超限中止、总字节预算中途停下的文件都不计入
- `downloaded_bytes`：实际从响应流读到的字节数，包含随后被丢弃的超限字节。按整块计数，因此可以略超过触发停止的那个上限

客户端规则：只有 `incomplete` 为 `false` 时，空的 `matches` 才表示这次扫完的范围内没有该连续子串。`incomplete` 为 `true` 时，空结果只表示没能证明不存在。

达到 `max_results` 时立即停止，并写入 `max_results`。即使最后一条刚好是范围内的最后一处命中，也标记 `truncated`。其余预算只在后面还有目录、条目或文本文件要处理、但余量已经用尽时写入；刚好扫完、没有剩余对象时不写这些原因。`recursive=False` 时只看直接子项，不因此写入 `max_depth`。

工具文档要写明：片段是资料，不是给 Agent 的操作指令；使用命中路径调用 `read_file` 之前先看 `incomplete`。

## 5. 数据流

1. 校验参数和路径。
2. 从起始目录做广度优先遍历。私有遍历负责目录数、条目数和深度。文件名命中上限由 `search_file` 处理，正文片段上限由 `search_content` 处理。
3. 文本后缀文件在还有 `max_files`、`max_total_bytes` 和 `max_seconds` 余量时发起 GET。
4. 内部读取按第 6 节处理成功、单文件失败和总预算。
5. 成功解码的文本交给 `content.py` 查找。片段累计到 `max_results` 即停止。
6. 汇总 `incomplete` 后返回。响应里没有整篇正文，也没有账号、密码或上游异常原文。

`max_seconds` 用调用开始时的时钟，在每次 PROPFIND 和 GET 之前检查。超时则不再发起下一次请求，写入 `max_seconds`。已经发出的单次请求仍受现有 `settings.timeout` 约束。时钟函数是 Python 方法的内部参数，默认 `time.monotonic`，不出现在 MCP 参数里。测试注入假时钟。

## 6. 错误处理

### 6.1 整次失败

以下情况不返回部分 JSON，MCP 调用为 `isError`：

- 参数或路径非法：`ValueError`
- 任一次目录 PROPFIND 失败，包括起始目录和后续子目录：现有的脱敏 `RuntimeError`。已经放进内存的命中一并丢弃

这与今天的 `search_file` 相同。

### 6.2 单文件失败后继续

单个文本文件的 GET 或解码失败时，追加一条 `file_errors`，然后继续其他文件。这样的调用 `isError` 为 `false`，`incomplete` 为 `true`。已有命中保留。

| 情况 | `reason` |
| --- | --- |
| HTTP 401 或 403 | `forbidden` |
| HTTP 404 | `not_found` |
| 其他非 2xx、超时、连接错误、重定向 | `read_failed` |
| 字节已读完但不是 UTF-8 | `not_utf8` |
| UTF-8 中含有禁止的控制字符 | `binary` |
| 该文件接收字节数超过 `max_file_bytes` | `too_large` |

`reason` 不包含响应体、URL、状态码原文或密码。异常链用 `from None` 截断。

重定向不跟随。3xx 归入 `read_failed`，不能把其他主机的内容当成命中。

### 6.3 字节预算

按收到的块处理，不把半截内容拿去匹配：

- 下一块会使该文件超过 `max_file_bytes`，但累计字节仍不超过 `max_total_bytes`：丢弃缓冲区，记 `too_large`，字节计入 `downloaded_bytes`，然后继续其他文件
- 下一块会使累计字节超过 `max_total_bytes`，且该文件尚未超过 `max_file_bytes`：丢弃缓冲区，写入 `max_total_bytes`，停止整个扫描。这个文件不进入 `file_errors`，也不进入 `scanned_files`
- 同一块同时越过两个上限：记 `too_large`，同时写入 `max_total_bytes` 并停止扫描。不匹配这半截内容

开始下一个文件之前，若 `downloaded_bytes` 已经达到 `max_total_bytes`，直接停止，不再 GET。

### 6.4 大小写折叠与行号

纯函数逐个原文码点做 `casefold()`，并记下每个折叠后下标对应的原文下标。查找发生在折叠后的字符串上，行号和片段取自原文。

因此关键词 `"ß"` 可以命中原文 `"SS"`（二者都折叠为 `ss`），但返回的片段必须是原文 `"SS"`。某个字符折叠后长度为 0 时，它不占用折叠下标。空文件返回空命中列表，不是错误。

## 7. 测试

自动测试只用本地 WebDAV 和真实 MCP 子进程，不使用个人坚果云账号，也不把私人正文写进仓库。

### 7.1 纯函数

`tests/test_content.py` 不启动 HTTP：

- 中文子串命中，行号正确
- `"Control"` 命中 `"control"`
- `"a b"` 不命中 `"ab"`
- `"ß"` 命中 `"SS"`，片段保持原文
- 同一文件两处不重叠命中都返回；`"aa"` 在 `"aaa"` 中只返回一处
- 超过 200 个字符的行返回 200 个字符的片段，且 `snippet_truncated` 为 `true`，行号仍是该行
- 无命中和空文本都返回空列表
- 关键词 `"a\nb"` 的行号是起点行，片段只有该行

### 7.2 本地 WebDAV

放在现有 `tests/test_text_tools.py` 的本地服务上：

- 文件名不含关键词、正文含有「控制」：返回路径和行号
- 文件名含关键词、正文不含：`matches` 里没有它
- 完整扫完且没有命中：`incomplete` 和 `truncated` 都为 `false`，`file_errors` 为空
- 同一文件重复出现关键词：多条命中；把 `max_results` 设到条数上限时 `truncated` 为 `true`
- 超限文件记 `too_large`，片段里不出现它的前缀；后面的合格文件仍能命中
- 非 UTF-8 记 `not_utf8`，含控制字符的文件记 `binary`；合格文件的命中仍在，`incomplete` 为 `true`
- 单个 GET 返回 403 时记 `forbidden`，返回 404 时记 `not_found`，扫描继续
- `max_results`、`max_files`、`max_total_bytes`、`max_seconds`、`max_depth`、`max_directories`、`max_entries` 各自产生对应的 `limit_reasons`，并且 `truncated` 与 `incomplete` 为 `true`
- `.pdf` 不出现在 GET 请求里
- 非法关键词、预算和路径不发起请求
- 目录 PROPFIND 失败时抛出错误，不返回带部分 `matches` 的字典
- 现有 `search_file` 测试继续通过，文件名搜索仍然不下载正文

`max_seconds` 用注入的假时钟，不依赖真实睡眠。

### 7.3 MCP 子进程与 smoke

- 子进程能发现 `search_content`，对本地夹具调用后得到已知命中
- 夹具中有一个坏文件时，工具 `isError` 为 `false`，返回体里 `incomplete` 为 `true`
- 参数错误和目录列举失败使 `isError` 为 `true`
- `smoke.py` 的默认期望工具集改为五个：`check_connection`、`list_directory`、`read_file`、`search_file`、`search_content`
- 默认 smoke 不调用 `search_content`。若以后增加可选参数，输出里会有片段，只在本机使用

Cursor 里重新加载并实际点选该工具，不在本阶段自动测试的完成定义里。实现后把这一步写进 `docs/installation.md`，保持未完成，直到有一次真实界面调用。

## 8. 实现完成时的文档

代码和测试通过之后才改这些文档，本设计文件本身不勾选路线图：

- README 的工具表加入 `search_content` 的参数、片段上限和 `incomplete` 的读法
- `docs/architecture.md` 补一句：正文搜索复用目录遍历，匹配在无 I/O 模块中
- `docs/installation.md` 分开记录 pytest、MCP 子进程和尚未进行的 Cursor 调用
- `docs/ROADMAP.md` 只勾选已经有证据的 4.1 项。4.2 索引和 P0 不动

## 9. 验收

同时满足下面各项，这一阶段才算完成：

- 第 7 节的测试通过，原有 `search_file` 测试无回归
- 存在「文件名不含关键词、正文含关键词」的夹具命中，以及正文不含关键词的反向夹具
- 中文、空结果、重复命中、每一种预算、单文件失败和目录整次失败都有对应测试
- 工具响应不含整篇正文、密码和上游异常原文
- 文档按第 8 节更新，并且没有把 pytest 通过写成 Cursor 已验收
