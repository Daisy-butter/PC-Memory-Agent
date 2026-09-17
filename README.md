# PC Memory Agent (MVP)

一个**只读本机文件发现与分析**原型。它将文件元数据、可抽取文本、关键词实体和文件关系保存到项目自己的 SQLite 数据库中；绝不修改、移动或删除被索引的源文件。

## 当前能力

- 递归索引你明确传入的目录（默认不会扫描任何目录）
- 支持 TXT、MD、CSV、JSON、HTML、PY/JS 等 UTF-8 文本文件，以及无额外依赖的 DOCX
- SQLite FTS5 全文检索、路径/文件名匹配和实体关联检索
- 内容哈希识别修改；记录移动/重命名后的历史路径
- `sync` 增量扫描；`watch` 轮询监听并增量维护
- `ask` 将一个任务拆为关键词，返回候选文件、相关实体和证据片段

PDF、XLSX、PPTX 和 OCR 是下一阶段的解析器扩展点：MVP 会安全记录它们的元数据，但不会读取其内容。

## 安全边界

- 所有数据库、日志和状态只写入 `.data/`，该目录已被 Git 忽略。
- 仅访问命令行中明确给出的根目录；拒绝将项目自己的 `.data/` 作为索引目标。
- 默认跳过隐藏目录、系统/缓存/依赖目录、符号链接、超过 25 MiB 的文件和未支持二进制格式。
- `search` 与 `ask` 仅返回内容片段；`show` 是唯一读取文件全文的命令，且只读。
- 本项目没有文件写入、移动、删除或执行被索引文件的功能。

## 使用

Windows PowerShell：

```powershell
cd C:\Users\31521\pc-memory-agent
python -m pcmemory index C:\Users\31521\Documents
python -m pcmemory search "合同 客户A"
python -m pcmemory ask "找出客户A最近的合同和相关会议纪要"
python -m pcmemory show 12
python -m pcmemory sync C:\Users\31521\Documents
python -m pcmemory watch C:\Users\31521\Documents --interval 10
```

先使用 `index` 建立首个索引；同一根目录以后应使用 `sync` 或 `watch`。可通过 `--include-hidden`、`--max-size-mib` 调整策略。

## 数据模型

`files` 保存文件事实与内容版本；`chunks` 保存可检索的文本片段；`entities` 和 `file_entities` 保存可解释的词项关联；`file_history` 保存索引期间发现的历史路径。全文索引使用 SQLite FTS5，所有关系都有源文件 ID 可追溯。

## 验证

```powershell
python -m unittest discover -s tests -v
```
