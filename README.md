# PC Memory Agent

选题 1「帮助 Agent 的全 PC 文件管理记忆系统」的中期实现。它只读扫描指定目录，把异构文件整理进本地 SQLite 知识图谱，再让本机 vLLM 上的 **Qwen3-8B** 依据图谱回答跨文件、跨时间的问题。

源文件不会被修改、移动或删除。数据库只写在项目的 `.data/`，该目录不进入 Git。试跑用的 `FileSystem_wrh` 数据集和 Qwen3-8B 权重也不在本仓库里，需要按下面的步骤在本机准备。

## 中期做到了什么

- 指定一个根目录后递归索引。文本、代码、Notebook、Office、PDF、图片、音频、视频、压缩包和 OmniGraffle 都会留下可追溯记录。
- 用规则写出显式事实：文件属于哪个顶层项目、什么模态、和哪些路径是同一份字节、同一文件夹里哪些文件名构成版本族。
- `ask` 先组装图谱证据，再调用本地 Qwen3-8B。证据不够或服务不可用时，退回图谱检索，不编造文件。
- 内容哈希能把移动、重命名接回同一文件，并在相同内容出现于多个目录时按修改时间排出「最新一份 / 更早副本」。
- 在 `FileSystem_wrh`（1510 个文件）上完成了索引和中期演示问题。

## 中期还没有做、期末再补

课程要求系统最终回答「属于哪个项目」「哪个是最新版本」「以前在哪个项目做过类似的事」，并和普通 LLM/RAG 对比。中期只完成了可运行的图谱原型和一组案例，下面这些还没有：

- **评测数据集和对照实验。** 正式评测集仍在整理。还没有「只把原文片段交给 Qwen3」的 RAG 基线，也没有准确率、命中率一类指标。
- **真正的内容修订。** 当前「最新」主要比较同一份字节在不同目录的修改时间。数据里几乎没有「方案 v1 / 最终稿、正文已经改写」这种版本。文件名里的 `v1`、`final` 规则已经写了，但这份数据用不上。
- **同一主题落在多个项目时的消歧。** 项目名取索引根下的第一层目录。论文既在 `03_Research_Papers`，又被 `21_Real_GitHub_Projects` 里的教材提到。演示中模型曾把论文归到 GitHub 快照，而不是论文目录。
- **一句话两问的稳定性。** 音频和视频连着问时，花朵视频曾被说成 `07_Media`。图谱里它属于 `08_Media`。
- **视觉理解。** Qwen3-8B 是纯文本模型。图片靠 OCR 和 SVG 文字，视频靠抽帧和音轨转写。没有默认接入视觉模型，扫描版 PDF 也不会先渲染成图片再识别。语音转写会有误差，例如把 Ogg Vorbis 听成 “AgForbus”。
- **更细的本体。** 现在是规则三元组，不是从正文抽取引用、作者、上下游关系。向量检索还没有做。
- **长期实时维护的评测。** 代码里已有 `sync`，以及 Linux inotify / Windows 目录监听的初版。中期演示不展开这部分，期末再讲实现和漏事件、溢出后的全量校验。

## 数据准备

仓库不包含数据集。中期使用的是本机目录 `FileSystem_wrh`（约 1510 个文件，含一份 `metadata.json`）。`metadata.json` 给每个相对路径一条中文摘要和关键词；索引时写到对应文件上，不把整份目录重复灌进全文索引。

目录本身放在 SSD 上，不提交：

```text
/SSD_DISK/users/wuruihan/PC-Memory-Agent/FileSystem_wrh
/SSD_DISK/users/wuruihan/datasets/FileSystem_wrh   -> 指向上面的目录
```

换机器时把数据放到任意路径，再用环境变量告诉索引脚本：

```bash
export PCM_DATASET_ROOT=/你的路径/FileSystem_wrh
```

顶层目录在图谱里就是「项目」。和中期问题直接相关的有：

| 项目目录 | 演示里用到的材料 |
| --- | --- |
| `03_Research_Papers` | Attention Is All You Need 论文 PDF |
| `05_Datasets` | Iris 表格 |
| `01_AI_Projects` | GPT-2 损失曲线、nanoGPT 模型代码的较早副本 |
| `15_Experiments` | 上述图片和代码的实验副本 |
| `20_Data_Visualisation`、`08_Media` | 同一张损失曲线的图和海报 |
| `21_Real_GitHub_Projects/nanogpt` | 同一批字节里修改时间最新的图片和 `model.py` |
| `07_Media` | `example.ogg` 语音样例 |
| `08_Media` | `flower.mp4` 花朵视频 |

同一张 GPT-2 损失曲线图的字节在五个路径各有一份。按修改时间从新到旧是：nanoGPT 仓库里的 `assets/gpt2_124M_loss.png`，然后是 `15_Experiments`、`20_Data_Visualisation`、`08_Media`，最早是 `01_AI_Projects/transformer_demo`。这是中期用来说明「最新一份」和「以前在哪个项目出现过」的证据，不是五次内容改写。

## 模型准备

Qwen3-8B 权重大约 16GB，放在本机，不提交：

```text
/SSD_DISK/users/wuruihan/models/Qwen3-8B
```

这台机器的驱动是 570.86（CUDA 12.8）。请使用 **vLLM 0.19.0**。更新的 vLLM 会带上 CUDA 13 的 PyTorch，当前驱动起不来。依赖写在 `requirements-server.txt`。中期环境是：

```text
/SSD_DISK/users/wuruihan/venvs/pcmemory
```

Qwen3-8B 只读文本。图片、音频、视频在索引阶段变成文字：

| 模态 | 已实现的处理 |
| --- | --- |
| 文本、代码、Markdown、CSV、JSON、HTML、Notebook、Bib、ICS、无扩展名的可解码文本 | 按 UTF-8 或 GB18030 解码；Notebook 抽单元格源码 |
| DOCX、XLSX、PPTX | 解包 XML，不执行宏和公式 |
| PDF | `pypdf` 抽取嵌入文字。扫描页不会渲染后再 OCR |
| PNG、JPG、GIF、TIFF、BMP、WEBP | 记录尺寸；有 Tesseract 时做 OCR（默认 `eng+chi_sim`） |
| SVG、EPS | SVG 抽取文字节点和 viewBox；EPS 保留注释 |
| MP3、OGG、WAV 等 | ffmpeg 转成 16kHz 单声道，再用 faster-whisper 转写 |
| MP4 等视频 | ffprobe 记录容器；抽取音轨转写，并抽 3 个关键帧做 OCR |
| ZIP、OmniGraffle | 列出压缩包条目，或从 graffle 的 plist 抽出文字。不展开大压缩包的内容 |
| 证书、私钥 | 只记录路径，不把密钥正文写入图谱 |

可选：另起一个视觉 vLLM，设置 `PCM_VISION_BASE_URL` 后，索引图片时会追加一句画面描述。中期没有启动这个服务。

本机 8000 端口已被占用，文本模型用 **8001**。权重和缓存都在 SSD 上时可以离线启动：

```bash
# 已有服务时不要再开第二个，否则会抢同一块 GPU
curl -s http://127.0.0.1:8001/v1/models

# 没有服务时
bash scripts/serve_qwen3_vllm.sh
```

可用环境变量：`PCM_CUDA_DEVICE`（默认 1）、`PCM_VLLM_PORT`（默认 8001）、`PCM_VLLM_MODEL`、`PCM_VLLM_PYTHON`、`HF_HOME`。

## 索引和提问

```bash
cd /SSD_DISK/users/wuruihan/PC-Memory-Agent
bash scripts/index_dataset.sh
```

脚本会把 Tesseract 放进 `PATH`，并用 CPU 上的 Whisper `small`。索引根目录、解释器和单文件上限可以用 `PCM_DATASET_ROOT`、`PCM_PYTHON`、`PCM_MAX_SIZE_MIB` 覆盖。

已经索引过的目录再跑一次，只会按大小和修改时间跳过未改文件。中期这次复跑的结果是 `seen=1510, indexed=0, unchanged=1510`。

提问（索引已完成时加 `--no-sync`，避免提问前再扫一遍）：

```bash
PY=/SSD_DISK/users/wuruihan/venvs/pcmemory/bin/python
$PY -m pcmemory ask "Attention Is All You Need 这篇论文属于哪个项目？Iris 数据集又在哪个项目？" --no-sync
$PY -m pcmemory ask "GPT-2 损失曲线这张图最新的一份在哪个项目，更早在哪些项目出现过？" --no-sync
$PY -m pcmemory ask "nanogpt_model.py 这套模型代码最新出现在哪里，更早在哪个项目里写过？" --no-sync
$PY -m pcmemory ask "example.ogg 转写了什么，它属于哪个项目？花朵视频又在哪个项目、是什么模态？" --no-sync
```

`--no-llm` 只打印图谱证据。演示里每条指令对应的评价维度见 [docs/中期演示说明.md](docs/中期演示说明.md)。

其他命令：`search` 做全文和路径检索，`show <file_id>` 看已索引文本，`explain <file_id>` 看三元组，`sync <目录>` 增量扫描，`roots` 查看已登记目录。

## 图谱里实际存了什么

SQLite 属性图，主要是这些表：

- `files`、`chunks`、`chunk_fts`：路径、哈希、修改时间和可检索文本。
- `entities`、`facts`：`文件 — 谓词 — 实体`。谓词包括 `BELONGS_TO_PROJECT`、`HAS_MODALITY`、`MEMBER_OF_VERSION_FAMILY`、`TAGGED`、`HAS_CONTENT_ID`。
- `file_relations`：同一份内容（`SAME_CONTENT`）、同一文件夹内的较新版本（`NEWER_VERSION`）、相同课号或联系人。不为「同一个项目」或「同一个常见关键词」展开两两边，避免大目录把关系表撑满。
- `file_history`：索引期间发现的旧路径。

项目名是相对索引根的第一层目录，松散文件算在根目录名下。同一文件夹里，去掉 `v1`、`final`、`草稿` 等字样后的文件名主干相同，就归入一个版本族，并按修改时间排序。不同目录里字节完全相同的文件另走内容时间线：回答「最新」时采用该列表的第一项。

`ask` 的证据顺序是：项目和模态统计、与命中文件相关的同目录版本族、跨目录内容时间线、命中文件的事实和片段。系统提示要求模型只根据这些证据回答，并写上 file id 和路径。

## 边界

- 只索引命令行给出的根目录。跳过符号链接、`.git`、虚拟环境和缓存目录，以及超过 1024MiB 的文件。普通点文件（例如 `.github`）会索引。
- 不能把项目自己的 `.data/` 当作索引根。
- 没有修改、移动、删除或执行被索引文件的功能。

## 测试

```bash
/SSD_DISK/users/wuruihan/venvs/pcmemory/bin/python -m unittest discover -s tests -v
```

覆盖 Office/PDF 抽取、项目和版本族事实、相同内容的新旧顺序、检索问句里的文件名，以及 Linux 上创建、改名、删除后的增量更新。
