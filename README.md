# Red Dot Design Concept 爬虫

默认年份 2026，自动发现 Design Concept 全部主题大类，逐类完整分页采集。

```powershell
python -m pip install -r requirements.txt
python reddot_crawler.py categories
python reddot_crawler.py discover
python reddot_crawler.py fetch
```

2026-11-07 保存的官网分类快照有 34 个筛选项：31 个主题大类，以及 Concept、Next Gen Award、Ready For Market 三个阶段筛选。脚本按主题建目录，将阶段单独保存到 metadata.json，不当作主题大类重复采集。运行时读取实时分类树，category_manifest.json 记录当次大类数量及列表，空类别建立目录但不请求详情。

目录：data/2026/Concept Design/大类名称/细分类名称/作品名称-ID/{metadata.json, README.md, image/}。细分类来自官网 category 字段竖线前的文字；竖线后的项目阶段单独保存。同一作品属于不同主题时，在对应主题目录分别归档。

默认不限数量，跳过当前目录图片完整的作品，失败图片下次重试。--refresh 强制更新；--limit 3 表示每个大类最多考虑前三件（含跳过）。只跑建筑大类：python reddot_crawler.py fetch --category /12/13/。可用 --detail-category 指定细分类，默认不筛选。

index.json/index.csv 汇总新层级中所有本地作品，包含 archive_path；CSV 占用时改存 index.latest.csv。旧 works 样本不移动、不删除、不计入汇总。每类 discovery-*.json 为列表快照；last_errors.json 为最近处理类别的失败，category_errors.json 汇总全分类运行失败的类别。

模板 templates/work.md 控制作品档案格式。不生成 notes.md、source.html。图片保存于 image/，下载状态记录于 metadata.json。简介来自 meta description，可能短于正文。运行时检查 robots.txt，默认请求间隔三秒；403/429 停止。官网结构变化时需调整解析器。

所有新输出（分类快照、作品目录和汇总索引）均位于 data/2026/Concept Design/。旧目录不会自动迁移；如需复用已采集作品，请将旧的大类文件夹移动到 Concept Design 内，再启动采集。


## 多年份

只采集 2025：python reddot_crawler.py fetch --year 2025
只采集 2026：python reddot_crawler.py fetch --year 2026
依次采集两年：python reddot_crawler.py fetch --years 2025 2026
--years 优先于 --year，省略年份参数仍默认为 2026。
数据分别写入 data/2025/Concept Design/ 与 data/2026/Concept Design/，分类目录每年独立读取官网结果。
同一运行复用网络连接与 robots 规则，SSL/连接中断和超时有限重试三次；分类网络失败记入 category_errors.json 并继续其他类别，结束时报告失败。证书校验保持启用。

## 按奖项运行

Red Dot（已验证的入口）：
```powershell
python reddot_crawler.py fetch --years 2025 2026
```

iF Student Award（实验入口，尚未完成在线验证）：
```powershell
python if_crawler.py discover
python if_crawler.py fetch --limit 3
```

iF 官网本次直接请求返回 The request is blocked。此入口仅提供 HTML 候选链接发现和待核验档案，不保证分页完整、年份或获奖身份，不作为生产采集器。遇到封锁或没有详情链接将停止；不要绕过验证。需在可正常打开官网的环境检查列表加载请求后，补齐接口、筛选和详情解析。yearId=0 的含义尚未核实，不等同于 2025 或 2026。iF 数据暂存 data/IF Student Award/Unverified/，不混入 Red Dot 档案。

## 统一 main 入口

main.py 提供 RedDotCrawler、IFStudentCrawler 两个独立类，以及 run_reddot()、run_if() 两个函数。实例化或导入不会采集，只有调用 run/main 才执行。

```powershell
python main.py reddot fetch --years 2025 2026
python main.py if discover
python main.py if fetch --limit 3
```

iF 仍为待在线验证的实验实现，封装调整不代表其分页和全量采集已验证。原 reddot_crawler.py / if_crawler.py 直接运行方式仍有效。

## iF 浏览器模式（2026-11-08）

旧的 iF requests 访问层已替换为 Playwright 可见 Chromium，读取渲染后的 DOM，外部图片域名单独检查 robots。保持 TLS 校验，不使用 stealth，不自动处理验证码；403 或无法核实访问规则时停止。依赖首次安装：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py if fetch --year 2026 --limit 1 --project-url "https://ifdesign.com/en/winner-ranking/project/zito/785101"
```

运行时会弹出独立浏览器，结束后关闭；不是接管你现有的 Chrome 登录窗口。结果在 data/IF/2026/大类/作品ID/，包含 image、metadata.json、README.md。默认单件试采。完整90件的动态分页仍未实现验证，不应使用 --limit 0 就宣称抓全。2025/2026 年份通过详情徽标核验；列表 yearId=0 不应用于推断任意年份。

Red Dot 使用 requests 请求列表 JSON 和详情 HTML，不执行 JavaScript；iF 使用真实浏览器执行 JavaScript，再读取 DOM，速度更慢、内存占用更高，但适合动态页面。浏览器模式不保证消除403。当前仅做语法和命令参数检查，尚未端到端采集验证。

## 无需下载 Chromium

iF 现默认 --browser msedge，使用本机已安装 Edge；也可 --browser chrome。只需安装 Python playwright 包，不必执行 playwright install chromium。只有 --browser chromium 需要下载 Playwright 浏览器。它使用独立浏览器会话，不读取日常浏览器登录资料，不保证消除官网403。

## iF 2026 全量（最新）

python main.py if fetch --year 2026

默认不限数量，使用本机 Edge，滚动 #resultList 自动加载直到收齐官网总数，默认核对90件。--limit 1 仅试采一件（全年度报告会标为未完成），--refresh 强制更新已完整作品。每件校验详情年份与类别。完整的本地作品会跳过；失败作品记入 errors.json，run_report.json 列出 missing_or_incomplete，仅全部有资料和图片时 complete 才为 true。index.json 汇总所有本地档案。

输出 data/IF/2026/大类/作品名-ID/{image/,metadata.json,README.md}。动态分页已通过浏览器观察验证18到36件的增长；完整90件的端到端下载尚未运行。旧文档中“只支持候选/默认单件”的说明由本节替代。

## iF 下载稳定性修复

作品网页仍由 Playwright 加载；公开图片资产改用流式 HTTP 下载，避免 Playwright APIRequestContext 等待完整大图超过60秒。下载前检查资产域名规则，401/403/429停止，不绕过访问限制。

默认图片读超时120秒（指无数据传输的等待时间），失败重试3次，可通过 --image-timeout 180 --image-retries 3 调整。每件作品在下载前立即生成 metadata.json 和 README.md；每张图片完成后更新，失败不丢整件资料。图片临时写入.part，通过长度和Pillow图片校验后才改为正式文件。未成功图片会记录 download_failed，后续重跑补齐，已有图片尽量复用。

建议先 python main.py if fetch --year 2026 --limit 3 验证，再移除 --limit 采集全年度。仅更新代码、未启动全量爬取。本地模拟超时重试和保存检查已通过。

## iF 2025 和 2026（最新）

python main.py if fetch --year 2025
python main.py if fetch --year 2026

脚本从官网读取对应 Award year 的真实链接后导航，按所选年份读取总数，详情继续核验获奖年份。2026-11-08 浏览器核实：2025筛选yearId=1，100 Projects；2026筛选yearId=0，90 Projects。总数默认不写死；如需显式核对，可传 --expected-count 100（2025）或90（2026）。

2025输出在 data/IF/2025/大类/作品名-ID/，2026在对应2026目录。--refresh重新提取并下载，省略则补齐未完成作品。此次只修改并本地检查，没有启动全年采集。
