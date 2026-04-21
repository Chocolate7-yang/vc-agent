# VC 信息聚合 Agent

一个面向投资研究场景的自动化信息流系统：持续采集 AI / 芯片 / 机器人赛道内容，自动过滤并生成每日简报，支持飞书推送与反馈学习。

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
set -a && source .env && set +a
PYTHONPATH=src .venv/bin/python -m vc_agent.feishu_events
```

   - 终端出现 `connected to wss://...` 后，在开发者后台完成：
   - 事件配置 -> 订阅方式“长连接” -> 验证成功 -> 保存
   - 回调配置 -> 订阅方式“长连接” -> 验证成功 -> 保存 -> 添加回调 `card.action.trigger`
   - 若报 `connecting through a SOCKS proxy requires python-socks`，先安装：

```bash
source .venv/bin/activate
pip install python-socks
```

5. 填写 `FEISHU_RECEIVE_ID`（群聊 `chat_id`）

```bash
set -a && source .env && set +a
PYTHONPATH=src python -m vc_agent.feishu_list_chats
```

复制输出中 `oc_...` 的 `chat_id`，写入 `.env` 的 `FEISHU_RECEIVE_ID`


### 3) 一键启动

a. 测试飞书立即推送（立刻执行一轮“收料 -> 简报 -> 飞书”）：

```bash
bash run.sh start
```

b. 每日推送（7x24）
```bash
bash run.sh 7x24 
```
默认策略：

- 每 4 小时跑一次收料
- 每天 08:00（`Asia/Shanghai`）生成晨报并推送到飞书

如果要测试定时推送，可临时指定测试时刻（不改 `.env`）：

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
- 调度运行：单次执行 + 7x24 常驻调度

## 数据源状态

- `YouTube`：已接入主流程
- `Twitter/X`：预留 TODO，当前未接入主流程
- `公众号`：预留 TODO，当前未接入主流程

## 项目结构

- `design.md`：系统设计文档
- `run.sh`：统一启动脚本
- `src/vc_agent/agent.py`：主流程与规则
- `src/vc_agent/pipeline_service.py`：采集与简报服务入口
- `src/vc_agent/scheduler.py`：调度器入口
- `src/vc_agent/storage.py`：SQLite 持久化
- `src/vc_agent/preferences.py`：偏好学习
- `src/vc_agent/feedback.py`：反馈命令

---

## 常见问题

### Q1：报网络错误或拉不到 YouTube

先确认 VPN/网络是否可访问 `youtube.com`，再执行 `bash run.sh doctor` 排查。

### Q2：有采集但简报入选很少

可能是过滤阈值偏高或关键词覆盖不足，可调整评分配置和关键词规则。

### Q3：摘要质量不稳定

通常是 LLM Key 未配置或模型不可用，先检查 `.env` 中密钥与模型配置。

---

## 设计说明

详细架构、采集策略和简报格式见 `design.md`。
