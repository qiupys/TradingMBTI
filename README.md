# K线时光机 · 韭菜体检中心

> 用十局**真实历史行情**测出你的「炒股 MBTI」——你只管操作，AI 告诉你情绪值多少钱。

## 这是什么

一个炒股行为体检小游戏：

1. **自评画像**——先给自己打个 MBTI 标签（风险承受 / 决策速度 / 信息依赖 / 盈亏处理）
2. **十局实盘复盘**——从 40 只不同波动率的真实标的池随机抽 10 局（长江电力到天齐锂业），每局一段真实历史 K 线，你只能看到入场前一个月的走势和当时的新闻，从 day 1 开始操盘
3. **行为体检**——追高买入、恐慌日卖出、止损离场、卖飞、处置效应……每一笔操作都会被打上行为标记，并计算「情绪税」（对比满仓躺平基准）
4. **AI 深度分析**——把十局操作记录交给大模型，输出你的实测 MBTI（vs 自评的漂移）+ 逐段复盘
5. **体检证书**——Canvas 绘制的可下载长图，十局明细 + 评级阶梯 + MBTI，可直接转发

数据全部来自真实历史行情（baostock 日 K，官方 pctChg 口径定点复权），不含任何未来函数。

## 技术架构

- **零依赖服务端**：`server.py` 纯 Python 标准库（http.server + sqlite3），无框架无 pip 依赖
- **纯函数游戏引擎**：`game-core.js` 无 DOM 依赖的交易数学/结算/评分/发牌引擎，浏览器与 Node 测试共用同一份代码
- **前端单文件**：`index.html` 内联全部 CSS/JS，Canvas 2x 超采样绘制 K 线图与证书，日/夜两套独立配色（日间蓝 / 夜间琥珀黄）
- **AI 通道**：优先 OpenAI 兼容 HTTP 直连，回退 CodeBuddy CLI 子进程，两者皆不可用时本地规则引擎兜底
- **测试**：pytest（服务端/数据/HTTP 冒烟/静态资源）+ node:test（游戏引擎），共 **52 项**

## 快速开始

```bash
# 1. 构建/更新行情数据（需 baostock）
python fetch_data.py   # 拉取 10 只标的宽区间日K → data/kline_raw2.json
python build.py        # 组装十局剧本 → data.js

# 2. 启动服务
python server.py       # 默认 http://127.0.0.1:8741

# 3. 跑测试
python -m pytest -q                    # 37 项
node --test tests/game-core.test.mjs   # 15 项
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` / `HOST` | `8741` / `127.0.0.1` | 服务监听地址 |
| `TT_DB` | `./timetrader.db` | SQLite 用户/战绩库路径 |
| `CODEBUDDY_API_KEY` | 空 | CodeBuddy CLI 通道的 key（与设备绑定） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | OpenAI 兼容 HTTP 直连通道，三者齐备时优先使用 |

## 生产部署

部署于腾讯云轻量服务器（线上地址不对外公开）：nginx 反代 → systemd 服务 `timetrader`（python server.py，仅监听 127.0.0.1，`EnvironmentFile` 注入 key，PATH 需含 node 供 CLI 通道使用）。更新流程：`git pull && systemctl restart timetrader`。HTML 响应头 `Cache-Control: no-store`，发版即刷新生效。

## 目录结构

```
├── index.html        # 前端单文件（页面/CSS/JS/Canvas 图表与证书）
├── game-core.js      # 纯函数游戏引擎（浏览器与 Node 测试共用）
├── server.py         # 标准库 HTTP 服务（静态资源 + 用户/战绩 API + AI 通道）
├── build.py          # 十局剧本组装（原始K线 → data.js）
├── fetch_data.py     # baostock 行情拉取（不复权原始价 + 官方 pctChg）
├── data.js           # 构建产物：十局标的/窗口/新闻/行情
└── tests/            # pytest + node:test，共 52 项
```

## 免责声明

本项目仅用于行为金融学教育与娱乐，不构成任何投资建议。历史行情回放不代表未来收益，「满仓躺平」基准的存在就是为了告诉你：大多数操作的情绪成本有多高。
