# VC 信息聚合 Agent

一个面向投资研究场景的自动化信息流系统：持续采集 AI / 芯片 / 机器人赛道内容，自动过滤并生成每日简报，支持飞书推送与反馈学习，**飞书侧已支持同一组织内多群同步推送**。

### 简报演示视频

https://github.com/user-attachments/assets/77ddbbb1-780f-4892-bfcd-ab66faaa7549

### 完整简报输出示例

[`example.md`](example.md)

推送到飞书时默认使用 **卡片消息**（分栏折叠、正文为卡片内 Markdown、含原文链接、可配长连接接收 👍/👎 反馈）。卡片挂在具体群聊或会话里，**不能**像云文档那样给出「一条链接、飞书组织外也可按需打开」的独立入口。就阅读体验而言，卡片在群内更紧凑、可折叠；云文档在版式与交互上通常不如卡片精致，但**天然适合用链接做分享**。当前仓库**尚未实现**推送为飞书云文档格式；会话外需要与 `example.md` 同结构的正文时，请使用本地 `output/` 里已生成的 Markdown，或自行从会话中导出、转发（仅限向飞书组织内或已添加的外部联系人）。

### 网络准备（VPN）

当前主数据源是 YouTube RSS，运行时需要访问 `youtube.com`。

- 中国大陆网络环境通常无法直连 YouTube
- 请先连接可用 VPN

自检：
```bash
bash run.sh doctor
```

## 快速开始

请严格按以下顺序执行：**先安装依赖 -> 再复制并填写 `.env` -> 最后一键启动**。

### 1) 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) 复制并填写 `.env`

```bash
cp .env.example .env
```

1. 先填写 LLM 配置（必须）
   - `OPENAI_API_KEY`
   - `OPENAI_BASE_URL`
   - 未配置完整的 Key + URL 时，程序会自动降级到非 LLM 路径

2. 填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`
   - 打开[飞书开放平台](https://open.feishu.cn/)
   - 进入“开发者后台”，创建企业自建应用
   - 在“凭证与基础信息”获取 `App ID` 和 `App Secret`

3. 配置飞书应用能力与权限
   - 在“添加应用能力”中添加“机器人”能力
   - 在“权限管理”中开通消息相关权限（可搜索 `im:message`、`chat`、`机器人`）

4. 启动本地长连接并完成验证

```bash
set -a && source .env && set +a && PYTHONPATH=src .venv/bin/python -m vc_agent.feishu_events
```

   - 终端出现 `connected to wss://...` 后，在开发者后台完成：
   - 事件配置 -> 订阅方式“长连接” -> 验证成功 -> 保存
   - 回调配置 -> 订阅方式“长连接” -> 验证成功 -> 保存 -> 添加回调 `card.action.trigger`
   - 若报 `connecting through a SOCKS proxy requires python-socks`，先安装：

```bash
source .venv/bin/activate
pip install python-socks
```

5. 发布应用（上线到企业）
   - 开发侧完成长连接与事件/回调验证后，再在「版本管理与发布」中**创建版本并发布**（多数企业需**管理员审核**通过后生效）
   - 仅在草稿/未发布时，**企业内其他成员通常看不到该机器人**；发布成功后，成员才能在飞书客户端的应用目录或群聊里找到该应用/机器人

6. 创建群聊、拉机器人进群  
   - 把自建应用的**机器人**拉进所有需要收简报的群。  
   - **默认（多群）**：`.env` 里 **`FEISHU_RECEIVE_ID` 留空不填**，程序会列出机器人所在群并**逐群推送**。  
   - **可选（单群）**：只推一个群或单聊时，填写 `FEISHU_RECEIVE_ID`（`chat_id` 或 `open_id`）；可用下列命令查看 `chat_id`：

```bash
set -a && source .env && set +a
PYTHONPATH=src python -m vc_agent.feishu_list_chats
```

### 3) 一键启动

a. 测试飞书立即推送（立刻执行一轮“收料 -> 简报 -> 飞书”）：

```bash
bash run.sh start
```

b. 每日定时推送（7x24）
```bash
bash run.sh 7x24 
```
默认策略：

- 每 4 小时跑一次收料
- 每天 08:00（`Asia/Shanghai`）生成晨报并推送到飞书

如果要测试定时推送，可临时指定测试时刻

比如我想测试19:05能不能实现简报定时推送到飞书的效果：
```bash
bash run.sh test724 19:05
```

## 功能概览

- 数据采集：YouTube 频道 RSS（白名单）
- 内容处理：去噪、去重、规则评分、分栏归类
- 摘要与简报：调用 LLM 生成可读性摘要与投资信号
- 输出与持久化：`output/` Markdown + `data/vc_agent.db`
- 反馈学习：👍/👎 回写 `preferences.json` 影响后续排序
- 飞书推送：同一企业内多群逐条发送（接收方留空）；见下文「飞书推送」
- 调度运行：单次执行 / 7x24 常驻调度

## 数据源状态

- `YouTube`：已接入主流程
- `Twitter/X`：预留 TODO，当前未接入主流程
- `公众号`：预留 TODO，当前未接入主流程

## 飞书推送（能力与路线）

### 已落地：同一组织内多群

- **范围**：单个飞书企业内的**自建应用**。  
- **行为**：可对机器人所在**每个群各发一条**简报（`.env` 里 `FEISHU_RECEIVE_ID` 留空时按群列表逐群发；填写则只发该群聊）。  
- **运维**：机器人须已加入所有需要收简报的群；须在开放平台开通「拉会话列表」「发消息」等相关权限。

### 下一步

- **数据源**：优先把 Twitter/X、公众号接进采集与主流程，简报仍**只生成一份**，推送侧继续走现有的多群分发。  
- **多飞书企业**：每个飞书企业仍对应一套自建应用凭证；后续可支持**多套 `.env` 或等价配置**、**每个应用独立一条长连接**，以免 A 企业的消息或卡片事件误进 B 企业。  
- **上架与交付**：把应用上架到飞书应用中心或商店，让客户侧少重复「从零建应用、过审、开权限、配事件回调」这类交付动作。  
- **能力与商业复用**：在「拉会话列表 → 选目标群 → 发消息」之上加**群名/白名单过滤**、**按群裁剪篇幅或栏目**；把这条链路做成与「VC 简报」无关的通用群发能力，复用到其它定时通知或运营触达，少写重复的飞书对接与会话维护代码。

## 项目结构

- `design.md`：系统设计文档
- `run.sh`：统一启动脚本

### `src/vc_agent/`

- `__init__.py`：包导出
- `agent.py`：主流程、规则评分与晨报生成
- `briefing.py`：Markdown / JSON 简报输出封装
- `config.py`：项目根、`DATA_DIR` / `OUTPUT_DIR` 等路径
- `feishu_app_send.py`：飞书应用机器人 REST 发消息
- `feishu_events.py`：飞书长连接与卡片事件回调
- `feishu_list_chats.py`：列出会话并打印 `chat_id`（配置辅助）
- `feishu_push.py`：飞书 interactive 卡片与晨报推送
- `feishu_ws_ensure.py`：确保长连接进程在 shell 退出后仍常驻
- `feedback.py`：飞书卡片反馈命令解析
- `ingest.py`：YouTube RSS 等采集
- `pipeline_service.py`：单次「采集 → 简报 → 推送」服务入口
- `preferences.py`：👍/👎 偏好聚合与读写
- `ranking.py`：排序与规则打分
- `scheduler.py`：APScheduler 定时与 7×24 入口
- `scoring_profile.json`：评分阈值与关键词配置
- `storage.py`：SQLite 去重与持久化
- `summarization.py`：LLM 多栏摘要

### `tests/`

- `test_feishu_events.py`：飞书事件与长连接相关单测
- `test_feishu_app_send.py`：飞书会话列表聚合（分页/去重）单测
- `test_feishu_push.py`：飞书推送相关单测
- `test_preferences.py`：偏好读写与聚合单测
- `test_ranking_and_scoring.py`：排序与评分单测
- `test_storage.py`：存储层单测

### `data/`

- `youtube_channels.json`：YouTube RSS 频道白名单（随仓库提交）
- 同目录下 `vc_agent.db`、`preferences.json` 等为运行时生成，默认不入库

---

## 常见问题

### Q1：报网络错误或拉不到 YouTube

先确认 VPN/网络是否可访问 `youtube.com`，再执行 `bash run.sh doctor` 排查。

### Q2：有采集但简报入选很少

可能是过滤阈值偏高或关键词覆盖不足，可调整评分配置和关键词规则。

### Q3：摘要质量不稳定

通常是 LLM Key 未配置或模型不可用，先检查 `.env` 中密钥与模型配置。

### Q4：企业里搜不到机器人 / 成员看不到应用

飞书自建应用需在开发者后台「版本管理与发布」中**正式发布**并通过**企业管理员审核**后，普通成员才能在客户端里看到并拉机器人进群；仅保存开发配置时往往只有开发者本人可见。

---

## 设计说明

飞书多群能力与路线图见上文 **「飞书推送」**；整体架构、采集策略与简报格式见 [`design.md`](design.md)。
