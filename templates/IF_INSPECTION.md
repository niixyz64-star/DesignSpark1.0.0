# iF 页面检查（2026-11-08）

浏览器已确认给定列表页选中 Award year 2026，显示 90 Projects；初始显示 18 件。

接口探测方案已移除，目前使用 Playwright 浏览器采集。

已核实的样本：https://ifdesign.com/en/winner-ranking/project/zito/785101
- 名称：ZITO!
- 副标题：Educational Tool for Arboviruses Prevention
- 年份：2026，来自 SVG #Award_Year
- 主题：03 Goodhealth + Well-Being，来自 button[class*="--tag--"]
- 学校：Politecnico di Milano
- 设计者：Adrian García Alvarez
- 画廊：两张不同图片（DOM 存在重复项，需要 URL 去重），位于 ifdalivestorage.blob.core.windows.net 的 /user-uploads/ 路径。

已将上述详情选择器作为默认值，但尚未在线验证 Python 下载。外部图片域名仍需独立检查访问规则，不能简单跳过检查。
