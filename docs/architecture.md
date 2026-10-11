# MVP 架构

Cursor → stdio MCP（server.py）→ WebDAV 适配器（webdav.py）→ 坚果云。

配置加载由 config.py 负责，环境变量优先于指定 .env 文件。服务启动不访问远端，
工具调用时才校验凭据，便于区分「协议是否启动」与「账号是否可用」。

WebDAV URL 分解为 origin 与 root，避免第三方库将 /dav/ 自身当成子目录。
列表返回直接子项的名称，目录名称以 / 结尾，不递归扫描。

同步工具由 FastMCP 调度，注册 check_connection、list_directory、read_file、search_file。
保留现有三个模块的责任划分，无需增加空的 tools 包。
不添加写入或删除能力。请求设置超时，不自动重试以避免额外消耗服务配额。

read_file 用现有 HTTP Session 发起流式 GET，按收到的实际字节数限制内存，
最大 1 MiB，解码为 UTF-8（支持 BOM）。超限直接报错，不截断正文；只允许支持的
文本后缀并拒绝二进制控制字符。response 上下文管理器保证成功及异常路径均关闭响应。
GET 不跟随重定向，避免读取偏离指定 WebDAV 根目录的资源。

search_file 用 deque 实现广度优先遍历，队列记录相对目录和深度，文件名按 casefold
做子串匹配。分别限制深度、目录数、处理条目数与返回条数；结果包含扫描计数以及
truncated/limit_reasons。非递归查询的范围本身只包含直接子项，不因此标记截断。
搜索不下载正文，无索引、Embedding 或后台任务；单个目录响应仍由上游库完整解析。

search_content 复用同一私有广度优先遍历。文本后缀文件由有界 GET 读取，匹配和片段在 content.py 中完成，该模块不访问网络。结果含行号和最多 200 字符的片段，并单独报告 file_errors 与预算截断。没有索引或后台同步。

坚果云实际验证中，子目录 HEAD 返回 403，PROPFIND 返回 207。适配器使用已有
Client 的 HTTP Session、URL、头部和 XML 辅助函数，直接发起 PROPFIND，避免 HEAD 预检查。
目录请求不跟随重定向，并校验 HTTP 207、DAV multistatus 结构和查询集合自身的 href，
防止把 HTML 或畸形 XML 响应误报为空目录。解析失败统一返回脱敏错误。
本地回归测试覆盖 HEAD 被禁用时子目录搜索仍能成功，以及不存在目录时返回错误。

SDK 依赖范围限定在 MCP 1.x；本次验证版本为 mcp 1.30.0、webdavclient3 3.14.7、
python-dotenv 1.2.4。升级跨主版本前需要重新验证客户端和服务端接口。
