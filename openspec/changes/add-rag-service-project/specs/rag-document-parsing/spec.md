## ADDED Requirements

### Requirement: 多格式文档解析能力

系统 SHALL 支持 PDF、Markdown、TXT 三种文档格式的解析，对每种格式使用对应的解析器：PDF 使用复杂解析（版面分析 + 表格识别 + 文本结构化），Markdown 使用结构化解析（识别 ATX 标题层级），TXT 使用纯文本段落解析。

系统 SHALL 通过解析器工厂根据文件类型或显式参数选择解析器，未知格式返回 `400 UNSUPPORTED_FORMAT`。

#### Scenario: PDF 文档解析

- **WHEN** 调用方通过 `/parse` 或 `/ingest` 上传 PDF 文件
- **THEN** 系统 MUST 使用 `pdf_parser` 完成版面分析、表格识别、文本结构化
- **AND** 输出的 chunk 列表 MUST 携带 `metadata.page` 字段标识来源页码
- **AND** 表格内容 MUST 以 Markdown 或 HTML 格式保留在 chunk content 中

#### Scenario: Markdown 文档解析

- **WHEN** 调用方上传 `.md` 文件或传入 Markdown 文本
- **THEN** 系统 MUST 使用 `markdown_parser` 识别 ATX 标题层级
- **AND** 代码块 MUST 作为独立 chunk 保留完整内容
- **AND** chunk metadata MUST 携带 `heading_path` 反映标题树位置

#### Scenario: 不支持的格式

- **WHEN** 调用方上传 `.docx` 或 `.xlsx` 等未支持格式
- **THEN** 系统 MUST 返回 `400 UNSUPPORTED_FORMAT` 错误
- **AND** 错误消息 MUST 列出当前支持的格式列表

### Requirement: PDF 版面分析

系统 SHALL 对 PDF 每一页执行版面分析，输出页面元素的 bounding box 与类别。类别集合 MUST 包括：`title`、`paragraph`、`table`、`figure`、`formula`、`header`、`footer`、`list_item`。

系统 SHALL 实现以下版面处理能力：

- 页眉页脚剔除：基于位置阈值（顶部/底部 5% 高度）+ 文本重复模式
- 多栏布局：按列还原阅读顺序，避免双栏文档错乱
- 标题层级推断：根据字号、缩进、是否带编号（如 `1.2.3`）推断层级，构建 TOC

#### Scenario: 双栏文档阅读顺序还原

- **WHEN** 输入 PDF 为双栏排版（如学术论文）
- **THEN** 系统 MUST 按列还原阅读顺序（左栏读完再读右栏）
- **AND** 输出的 chunk 顺序 MUST 反映正确的语义顺序

#### Scenario: 页眉页脚剔除

- **WHEN** PDF 页面顶部或底部存在重复出现的页眉页脚
- **THEN** 系统 MUST 通过位置阈值与文本重复模式识别并剔除
- **AND** 输出 chunk MUST 不包含页眉页脚内容

### Requirement: 表格识别

系统 SHALL 识别 PDF 中的表格，输出结构化的行/列/合并单元格信息。表格识别 MUST 支持以下子能力：

- 表格定位：版面分析输出 `table` 区域
- 结构识别：识别行/列/合并单元格，输出 HTML 或 Markdown 表格
- 文本回填：单元格 OCR 文本回填到对应单元格
- 跨页处理：跨页表格做拼接，保留表头

#### Scenario: 简单表格识别

- **WHEN** 输入 PDF 包含标准的二维表格
- **THEN** 系统 MUST 输出 Markdown 或 HTML 格式的表格内容
- **AND** 每个单元格的 OCR 文本 MUST 正确回填

#### Scenario: 跨页表格拼接

- **WHEN** 表格跨多页显示
- **THEN** 系统 MUST 拼接多页内容为一个完整表格
- **AND** 后续页的表头 MUST 被识别并剔除，保留首页表头

### Requirement: 智能分块策略

系统 SHALL 提供三种分块策略供调用方选择：`structure_aware`、`semantic`、`fixed`。未指定时按文档类型自动选择：PDF → `structure_aware`，MD → `semantic`，TXT → `fixed`。

| 策略 | 适用场景 | 实现要点 |
| --- | --- | --- |
| `structure_aware` | PDF / 复杂文档（默认） | 按标题层级切分，同一二级标题下段落聚为一块，最大 800 字 |
| `semantic` | 长文 Markdown / TXT | 基于句子 Embedding 相似度做语义边界检测 |
| `fixed` | 简单 TXT | 固定窗口 + 重叠，默认 500 字 + 50 重叠 |

#### Scenario: structure_aware 按标题切分

- **WHEN** 输入 PDF 含多级标题（如 `第3章` → `3.1` → `3.1.1`）
- **THEN** 系统 MUST 按二级标题边界切分 chunk
- **AND** 同一二级标题下的段落 MUST 聚为一块，最大不超过 800 字
- **AND** chunk metadata.heading_path MUST 记录标题层级路径

#### Scenario: semantic 语义边界检测

- **WHEN** 输入为长文 Markdown 且策略为 `semantic`
- **THEN** 系统 MUST 通过句子 Embedding 相似度识别语义边界
- **AND** 相邻句子相似度低于阈值时 MUST 切分为不同 chunk

#### Scenario: fixed 固定窗口分块

- **WHEN** 输入为简单 TXT 且策略为 `fixed`
- **THEN** 系统 MUST 按 `chunk_size`（默认 500）+ `chunk_overlap`（默认 50）切分
- **AND** 每个 chunk MUST 携带 `chunk_index`

### Requirement: 元数据清洗

系统 SHALL 对每个 chunk 执行元数据清洗，输出统一 schema：`{source, page, category, heading_path, doc_id, chunk_index}`。

清洗规则 MUST 包括：

- 去除断行符（如 PDF 提取的 `-\n` 连字符）
- 统一空白字符（全角空格、制表符归一化）
- 剔除 OCR 噪声字符（如 `□`、孤立单字符行）
- 长度低于阈值（默认 20 字）的 chunk 合并到相邻 chunk

#### Scenario: 断行符清理

- **WHEN** PDF 提取的 chunk content 含 `inter-\nnational` 等断行
- **THEN** 系统 MUST 清理为完整单词 `international`
- **AND** chunk content MUST 不含孤立的连字符换行

#### Scenario: 短 chunk 合并

- **WHEN** 某个 chunk 长度低于 20 字（如孤立标题行）
- **THEN** 系统 MUST 合并到相邻 chunk
- **AND** 不输出独立短 chunk
