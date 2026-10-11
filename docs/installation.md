# 安装和验收

完整命令见根目录 README.md。

当前已创建 .venv，并完成可编辑安装。真实坚果云连接和 MCP smoke 已通过。
首次配置时，在根目录 .env 填写邮箱和第三方应用密码，依次运行 WebDAV 命令、
MCP smoke 命令，再由 Cursor 调用四个工具。

项目 .cursor/mcp.json 已提供当前 Windows 路径的服务配置。
需要在 Cursor 中打开 nutstore-mcp 项目目录，确认 MCP 服务开启并重新加载。
若打开 AI-APP-Lib 父目录，应将 examples/cursor_mcp.json 的服务项合并到父目录配置中。

早期连接 MVP 验收结果（新增工具的验证见下文）：

- 本地自动测试：8 项通过；包括本地 WebDAV 和真实 MCP stdio 子进程。
- 依赖一致性检查：通过。
- 真实坚果云：通过，根目录返回 2 个条目。
- 真实坚果云 MCP 子进程：通过，发现两个工具，两次调用均 `isError: false`。
- Cursor 界面调用：通过（用户提供的 Cursor 验收结果）；两个工具调用成功，
  `check_connection` 返回 `ok: true`、`entry_count: 2`，根目录列表与
  同时直接调用 WebDAV 的结果在名称和顺序上均一致。

### 2026-10-11 续做检查

- 重新运行 `python -m pytest -q`：8 项通过。
- 重新运行 `python -m pip check`：无依赖冲突。
- 运行 `python -m nutstore_mcp.webdav`：退出码 1，提示缺少
  `NUTSTORE_USERNAME`。失败发生在配置校验阶段，尚未发起远端请求，
  不能据此判断坚果云认证或网络是否正常。
- 剩余验收顺序：在本机 `.env` 填写 `NUTSTORE_USERNAME` 和
  `NUTSTORE_PASSWORD`（第三方应用密码），运行 WebDAV 命令，再运行
  `python -m nutstore_mcp.smoke`，最后在 Cursor 实际调用两个工具。
- 项目 `.cursor/mcp.json` 已存在；配置文件存在不能替代 Cursor 实际调用验收。

上述配置缺失是首次检查结果；随后填写凭据后曾收到 HTTP 401。
更新第三方应用密码后，直接调用 `NutstoreWebDAV.check_connection()` 返回
`{'ok': True, 'entry_count': 2}`，MCP smoke 命令退出码为 0，
`check_connection` 和 `list_directory` 均成功。无需在验收记录中保存私人目录名称。

Cursor 配置 JSON 可解析，解释器和 `.env` 路径均存在，模块启动参数正确。
用户随后提供 Cursor 界面验收结果，确认服务已启用且两个工具调用成功，
返回值与直接 WebDAV 请求一致。当前只读 MVP 的三阶段验收全部通过。
该次验收仅覆盖原先的连接检查和目录列举。

### v0.1 原始目标补齐

根据用户补充的原始计划，本版目标是 list_directory、read_file、search_file，
而不仅是连接链路。现已新增有界 UTF-8 文本读取和有界文件名搜索，保留连接检查工具。
搜索不包含正文全文检索；PDF、索引、RAG 和部署发布保留到后续版本。

真实 MCP 子进程已验证四个工具：连接、根目录及子目录列举、文件名搜索、文本读取
均成功。验证读取了一份 7115 字节的 Markdown 文件，未在日志或本文记录正文。
搜索使用 max_results=1，因此返回 truncated=true，符合预期预算行为。
同时修复了坚果云子目录拒绝 HEAD、但接受 PROPFIND 导致上游预检查失败的问题。

当前自动验收结果：完整 pytest 49 项通过；pip check 无依赖冲突；
本地 wheel 构建通过，确认包含四个工具、LICENSE 和 NOTICE，且未包含 `.env`。
回归测试另覆盖条目预算恰好耗尽时，不再发起下一目录查询。
独立代码审查发现的 HTML/畸形 XML 被误判为空目录、目录重定向越界两项问题
均已修复并新增回归测试；复审未发现重要问题。

更新后请在 Cursor 重新加载或重启 nutstore MCP 服务，确认出现四个工具。
用 search_file 在指定知识库子目录搜索一个已知文本文件，再将返回的完整相对路径
传给 read_file，并对照原文。新增工具的 Cursor 界面调用尚待验收；
此前两工具的 Cursor 验收结果不能自动替代新增工具的验收。

请仅在本机填写凭据，不将密码发到聊天中。

定位顺序：配置 → 直接 WebDAV 请求 → MCP 客户端 → Cursor。
直接 WebDAV 失败时，先解决认证或网络；MCP 客户端成功但 Cursor 失败时，
检查解释器绝对路径、可编辑安装、.env 路径及 MCP 配置是否加载。

### search_content

本地 WebDAV 与 pytest 覆盖正文命中、文件名反向命中、中文、空结果、重复命中、各类预算、单文件失败和目录整次失败。真实 MCP 子进程已发现 `search_content`，并在本地夹具上返回 `资料/deep/control.txt` 的 `learning` 片段。这些结果不是 Cursor 界面验收。

Cursor 仍需重新加载 nutstore 服务，确认五个工具，再用 `search_content` 的命中路径调用 `read_file`。这一步完成前保持未验收。
