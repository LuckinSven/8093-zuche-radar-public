<div align="center">

# 神州车型雷达

**先看全城，再反向找车。**

一个面向个人使用的自托管租车车型观察工具，记录车型、网点、租期和价格变化，帮助你从“我想租什么车”出发寻找可租方案。

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-c8ef8e.svg)](LICENSE)

</div>

![神州车型雷达首页](docs/images/homepage.png)

## 它能做什么

| 能力 | 说明 |
| --- | --- |
| 全城车型扫描 | 按明确租期遍历已同步网点，汇总可租车型、候选网点和报价差异 |
| 永久车型库 | 保存曾经发现过的车型，只增加、不自动删除，持续更新标签和最近发现时间 |
| 按车型找车 | 从车型库选择感兴趣的车，反向查询未来周末的租期和可取网点 |
| 跨城找车 | 按精确车型编号、多组租期和多个城市比较异地取还车方案 |
| 历史与导出 | 保存扫描任务、成功失败情况和历史变化，并支持 CSV 导出 |
| 地图缓存 | 可选接入百度地图地点检索，结果保存在本地以减少重复消耗额度 |
| AI 车型补全 | 可选接入 OpenAI 兼容接口，逐车型补充能源大类和细分标签 |

系统目前以广州为主要使用场景，但城市、网点和扫描任务的数据结构保留了扩展空间。

## 快速开始

需要安装 Docker 和 Docker Compose。

```bash
git clone https://github.com/LuckinSven/8093-zuche-radar-public.git
cd 8093-zuche-radar-public
cp .env.example .env
```

编辑 `.env`，至少填写一个仅用于本机数据库的强密码：

```dotenv
POSTGRES_PASSWORD=请替换为随机生成的强密码
```

启动服务：

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8093/healthz
```

浏览器打开 `http://服务器局域网IP:8093`。健康检查返回以下内容表示应用和数据库已连接：

```json
{"service":"zuche-radar","status":"ok","database":"ok"}
```

## 推荐使用流程

1. 在“设置”中同步神州开放城市和目标城市网点。
2. 在首页选择取还时间，手动启动全城车型扫描。
3. 到“扫描记录”查看任务完整度，到“全城车型”查看当前租期结果。
4. 把感兴趣的车型加入个人标签，再使用“按车型找车”反查未来租期。
5. 需要地图检索或能源补全时，再按需配置百度地图或 OpenAI 兼容接口。

## 数据原则

- “可租”表示本次查询至少返回一个实际候选网点。
- “未找到”只在相关任务完整结束且没有结果时成立。
- 任务仍在运行、停止、中断或部分失败时统一显示“扫描不完整”。
- 页面价格来自上游列表字段，只用于比较，不是最终结算价。
- 原始响应和报价明细默认保留 60 天；永久车型库长期保留。
- 系统不保存神州账号、Cookie 或登录会话。

<details>
<summary><strong>扫描状态、采样与保存规则</strong></summary>

- 广州全城扫描使用 `AVAILABLE`、`NOT_FOUND`、`INCOMPLETE` 区分可租、完整未找到和扫描不完整。
- 网点报价明细和压缩原始响应保留 60 天，任务摘要和永久车型库长期保留。
- 按车型找车默认采样未来 4 个周末，先查询周六开始的 24/48 小时租期，再对候选网点补查小时窗口。
- 相同车型、网点和准确租期的成功请求可在 2 小时内复用。估算请求超过 2000 次时降低并发，超过 5000 次时拒绝创建。
- 第一版按广州同城还车查询；按车型找车任务、样本和报价保留 60 天。
- 页面价格来自上游列表字段，不是最终结算价，最终库存、价格和取还车资格以官方下单页为准。

</details>

## 可选服务

百度地图和 AI 补全默认都不要求启用。API Key 通过系统设置保存到本机 PostgreSQL，读取接口只返回脱敏状态。不要把真实密钥写入源码、`.env.example`、截图或 Issue。

AI 补全支持 OpenAI 兼容的 `/chat/completions` 接口，默认逐车型处理；地图检索结果优先读取本地永久缓存，以降低第三方接口用量。

<details>
<summary><strong>AI 补全的执行与恢复规则</strong></summary>

- “补全待处理车型”只处理能源未知、低置信或缺少细分的车型；“重新识别全部车型”用于更换模型后的整体复核。
- 任务提供停止、继续和重试失败操作，只有高置信且结论一致的结果才会写入车型库，遵循“未知不猜测”。
- 页面记录每次任务的处理数量、失败情况和 Token 用量。清除密钥会同时停用 AI 补全。
- Docker 重启后，等待或运行中的任务会标记为已中断，需要用户手动继续，不会自动消耗接口额度。

</details>

## 技术栈

- FastAPI、Jinja2 与原生 JavaScript
- PostgreSQL 16、SQLAlchemy 与 Alembic
- APScheduler 后台任务
- Docker Compose 单机部署
- Pytest 回归测试

## 开发与测试

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

生产数据和测试数据应使用不同数据库。不要在生产数据库上运行会清空表结构的测试命令。

## 安全边界

本项目当前没有用户登录、权限隔离、限流或公网攻击防护，只适合可信局域网和个人环境。不要把端口直接映射到公网。

数据库备份可能包含业务数据和第三方 API Key，必须存放在仓库目录之外。更多说明见 [SECURITY.md](SECURITY.md)。

## 免责声明

本项目是个人研究和技术交流工具，与神州租车、百度地图及其他第三方服务无隶属或合作关系。使用者应自行遵守相关服务条款、接口限制和当地法律法规，并在实际下单前到官方渠道确认车型、库存、价格、取还车资格和费用。

## License

[MIT License](LICENSE) © 2026 LuckinSven
