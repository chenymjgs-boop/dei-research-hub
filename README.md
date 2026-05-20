# DEI Research Assistant · 全球 DEI 自动研究助手

> A globally connected, locally rooted DEI research pipeline for in-house and consulting DEI practitioners in China.

每天自动从全球 DEI 研究、咨询机构、国际组织和行业媒体抓取最新内容，
通过 GPT 进行双语摘要、本地化洞察、主题分类，
推送到飞书多维表格 + 群聊卡片，每周自动生成趋势周报。

---

## ✨ 核心能力

| 模块 | 能力 |
|---|---|
| **Sourcing** | 5 类来源 · 20+ 个站点：HBR、MIT Sloan、SSRN、McKinsey、BCG、Deloitte、Catalyst、UN Women、ILO、OECD、WEF、SHRM、HR Dive、DiversityInc 以及中国本土来源（HBR 中文、智联研究院、人瑞、36氪 等） |
| **AI 分析** | 由 GPT 生成中英文双语摘要、关键要点、**对中国企业的启示**、主题/行业标签、证据类型、严谨度与相关性评分 |
| **存储** | SQLite 自动去重，每条研究只处理一次；保留全部历史 |
| **飞书集成** | 多维表格（Bitable）作为可检索知识库 + 群聊每日卡片 + 每周自动生成飞书云文档周报 |
| **运行方式** | GitHub Actions 定时任务，零服务器，零成本 |

---

## 🗂 目录结构

```
dei-research-assistant/
├── main.py                    # CLI 入口：daily / weekly / preview
├── src/
│   ├── config.py              # 环境变量配置
│   ├── utils.py               # 日志、去重哈希、日期解析
│   ├── sources/
│   │   ├── base.py            # RawItem + RSS/HTML 抓取基类
│   │   └── registry.py        # ⭐ 所有源在这里注册
│   ├── processing/
│   │   ├── analyzer.py        # LLM API 调用
│   │   └── prompts.py         # 提示词（在这里调优 AI 输出）
│   ├── storage/database.py    # SQLite 去重与历史
│   ├── delivery/feishu.py     # 飞书 Bitable + 群聊 + 云文档
│   └── tasks/
│       ├── daily.py           # 每日流水线
│       └── weekly.py          # 每周趋势报告
├── .github/workflows/
│   ├── daily.yml              # 每日北京时间 07:30
│   └── weekly.yml             # 每周一北京时间 06:00
├── data/research.db           # （运行后生成）
└── reports/weekly-*.md        # （运行后生成）
```

---

## 🚀 部署步骤（约 30 分钟一次性配置）

### 1. 准备 API Keys

#### OpenAI API
1. 访问 https://platform.openai.com/api-keys
2. 创建 API key，复制保存

#### 飞书自建应用
1. 登录 [飞书开放平台](https://open.feishu.cn/)
2. 创建"自建应用"
3. 在 **凭证与基础信息** 复制 `App ID` 和 `App Secret`
4. 在 **权限管理** 申请以下权限：
   - `bitable:app` —— 读写多维表格
   - `im:message` —— 发送群消息
   - `im:message:send_as_bot` —— 以应用身份发消息
   - `docx:document` —— 创建云文档（可选，用于周报）
5. 在 **版本管理与发布** 创建版本并发布上线
6. 把应用 **添加到目标群聊**（在飞书群里 @拉机器人）

#### 飞书多维表格
1. 在飞书中新建一个"多维表格"
2. 添加以下字段（**字段名必须严格一致**）：
   | 字段名 | 类型 |
   |---|---|
   | 标题 | 文本 |
   | 链接 | 超链接 |
   | 来源 | 文本 |
   | 类别 | 单选（选项：academic / consulting / international / media / china）|
   | 地域 | 单选（选项：global / china）|
   | 发布日期 | 日期 |
   | 收录日期 | 日期 |
   | 英文摘要 | 多行文本 |
   | 中文摘要 | 多行文本 |
   | 关键要点 | 多行文本 |
   | 对中国的启示 | 多行文本 |
   | 话题 | 多选 |
   | 行业 | 多选 |
   | 证据类型 | 单选 |
   | 严谨度 | 数字 |
   | 相关性 | 数字 |
3. 复制表格 URL，从中提取 `app_token` 和 `table_id`：
   `https://xxx.feishu.cn/base/<APP_TOKEN>?table=<TABLE_ID>`
4. 把应用添加为表格的"协作者"，权限选择"可编辑"

#### 获取群聊 chat_id
- 简单方式：在群里发 `@机器人 chat_id`，让机器人回复（需自定义实现），或者
- API 方式：调用 `GET /open-apis/im/v1/chats` 列出机器人所在的所有群

### 2. 本地试跑

```bash
git clone <your-repo-url> dei-research-assistant
cd dei-research-assistant

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入所有 key

# Step A: 仅测试源抓取（不调 LLM、不推送）
python main.py preview

# Step B: 跑一次完整流水线
python main.py daily

# Step C: 等几天有数据后试跑周报
python main.py weekly
```

### 3. 部署到 GitHub Actions

1. 把项目推送到一个 GitHub 仓库（建议私有仓库）
2. 在仓库 **Settings → Secrets and variables → Actions** 添加：
   **Repository secrets:**
   - `OPENAI_API_KEY`
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_BITABLE_APP_TOKEN`
   - `FEISHU_BITABLE_TABLE_ID`
   - `FEISHU_CHAT_ID`
   - `FEISHU_DOC_FOLDER_TOKEN`（可选）

   **Repository variables:**
   - `LLM_PROVIDER` = `openai`
   - `OPENAI_MODEL` = `gpt-5.4-mini`（可按预算改为 `gpt-5.4` / `gpt-5.5`）
   - `MAX_ITEMS_PER_RUN` = `40`
   - `MAX_ITEMS_PER_SOURCE` = `8`
   - `LOOKBACK_DAYS` = `7`

3. 在 **Actions** 标签页手动触发一次 `DEI Daily Research Run` 测试
4. 之后每天北京时间 07:30 自动运行；每周一 06:00 自动跑周报

### 4. 发布到 GitHub Pages 周报网站

仓库已配置 GitHub Pages Actions 部署：`Publish Research Hub` 会在推送
`main` 后发布当前站点，`daily` 会在每日入库后重建 `reports/site` 并发布，
`weekly` 会在生成新周报后发布。

在 GitHub 仓库 **Settings → Pages** 中，将 **Build and deployment → Source**
设置为 **GitHub Actions**。之后每次 workflow 成功结束，站点会更新到：

`https://chenymjgs-boop.github.io/dei-research-hub/`

---

## 🛠 自定义与调优

### 增加新的来源
编辑 `src/sources/registry.py`，添加 `RSSSource` 或 `HTMLListSource` 实例。
RSS 优先（稳定、低成本、尊重发布方）。

### 调整 AI 输出风格
所有 prompt 集中在 `src/processing/prompts.py`，可以根据你的方法论调整：
- 改写 `ANALYZE_SYSTEM` 调整 AI 角色与输出原则
- 修改 `key_takeaways` / `topics` 标签集合
- 调整周报的章节结构

### 改变运行频率
编辑 `.github/workflows/daily.yml` 中的 `cron` 表达式。

### 切换 LLM
当前默认使用 OpenAI Responses API。可通过 `LLM_PROVIDER=openai|anthropic` 切换后端；模型通过 `OPENAI_MODEL` 或 `CLAUDE_MODEL` 调整。

---

## 🔒 隐私与合规

- 所有抓取仅访问公开 RSS / 公开网页，遵循 robots.txt 与发布方版权
- AI 摘要不超过原文 30%，并保留原文链接，符合合理使用
- API key 仅存于你自己的 GitHub Secrets，不会暴露
- SQLite 数据库存于你的私有仓库，由你完全控制

---

## 🧭 下一步路线图建议

- [ ] 加入 PDF 抓取与全文分析（处理咨询机构的报告 PDF）
- [ ] 增加"行业聚焦"模式（如金融、科技、消费）
- [ ] 月度报告：从 4 周的周报中提炼 macro narrative
- [ ] 引入读者反馈机制：在飞书卡片上加"⭐收藏 / 🚫不感兴趣"按钮，反向训练相关度评分
- [ ] 多用户版（SaaS 化）：如果未来想给同行/客户提供服务
