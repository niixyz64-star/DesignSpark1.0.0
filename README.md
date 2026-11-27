# DesignSpark

面向设计者的本地案例作品库：定向采集获奖作品、保存图片与知识卡片，通过本地网页分类浏览、检索和阅读。

已实现采集与本地浏览闭环；AI 检索、自动翻译、知识迁移和设计启发 Agent 属于后续计划。项目进展见 [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)。

## 快速开始

建议 Python 3.10+，在项目目录运行：

```powershell
python -m pip install -r requirements.txt
python library_server.py
```

访问 http://127.0.0.1:8765 。不需要数据库或云端服务器，作品只在本机读取。界面采用黑白配色、图片保留原色，支持分类树、关键词搜索、来源/年份/奖项筛选、卡片详情和图片画廊。

Windows 可通过 `open_designspark.pyw` 后台启动并打开浏览器，需要已安装 Python 并关联 .pyw；桌面快捷方式应使用本机 pythonw.exe 和该脚本路径。快捷方式不适合直接跨电脑复制。

## 采集命令

```powershell
python main.py reddot categories --year 2025
python main.py reddot fetch --year 2025 --category "Home Furniture" --limit 3
python main.py reddot fetch --year 2025 --category "Home Furniture" --award "Red Dot"
python main.py if fetch --year 2025 --award student
python main.py if fetch --year 2026 --award best
```

- 红点：直接请求官网列表和详情，实时解析全部类别目录及名称，不依赖旧 data 识别类别。曾观察到 31 个主题大类，以实际返回为准；排除三个项目阶段。支持大类、细类、官方奖项精确匹配，每年跨类别数量上限。奖项过滤发生在列表取得后。
- iF：Playwright 浏览器采集，默认已安装的 Edge；使用 Chromium 时先运行 `python -m playwright install chromium`。支持年份及普通学生奖/Best of the Year。兼容 `--category 03`，但在打开详情后筛选，无法避免访问其他作品。
- 年份：当前年及前两年，2026 年对应 2024—2026。官网没有独立年份选项时明确停止；参数支持不等于全部年份已线上验收。
- 增量：复用完整图片、补采缺图；`--refresh` 刷新内容与图片。卡片 `<!-- user-notes -->` 后的笔记保留。支持重跑复用，不等于网络分页断点恢复。
- 保留证书和 robots 检查，不自动处理验证码；访问限制、分页停滞或改版需要排查。

## 数据与目录

```text
data/
├── RedDot-Design Concept/年份/大类/细分类/Smile Chair-RedDot/
│   ├── image/
│   └── 卡片.md
└── IF/年份/类别/作品名-编号-student或best/
    ├── image/
    └── 卡片.md
```

红点作品名保留大小写，不含编号，普通奖后缀 RedDot，其他奖项保留官方名称。内部编号仍用于识别；同名不同作品提示冲突，避免覆盖。卡片以 JSON 格式的 YAML frontmatter 保存结构化信息，正文链接本地图片。分类索引、作品索引和任务报告保存在对应目录。

网页兼容旧 metadata.json/README.md，默认扫描 data 和 fresh-*，同来源、年份、编号的副本展示合并。新增作品后点击刷新。旧目录不会自动搬迁。

```powershell
python library_server.py --root "D:\我的作品库" --port 8766
python -m unittest discover -s tests -v
```

## 文件职责

| 文件 | 用途 |
|---|---|
| main.py | 两个采集器统一入口 |
| reddot_crawler.py / if_crawler.py | 官网采集 |
| archive_store.py | 卡片、图片校验和索引 |
| library_server.py / library_web/ | 本地只读网页 |
| open_designspark.pyw / designspark.ico | Windows 启动脚本与图标 |
| tests/ | 离线回归测试 |

网页目前只读，搜索是关键词匹配，不是 AI 语义搜索。官网全量覆盖需逐年逐奖项验证；简介主要来自 meta description，可能短于官网正文。旧数据缺少奖项时保持未标注，不猜测为普通奖。

仓库不包含作品数据、图片、日志或凭证。获奖作品版权属于权利人，公开完整图片与原文需另核实授权。本项目尚未额外授予代码开源许可证。
