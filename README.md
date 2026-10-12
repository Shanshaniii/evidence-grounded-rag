# Evidence-Grounded RAG / 证据溯源问答

可运行的实验项目交付。将原文外存、模型证据选择、原始回答、程序元数据、定位风险和独立语义评估分开。
当前发布r6证据卡/schema核心与便携脱敏demo；r6真实业务尚未验证。已独立评分的r5基线严格通过0/3，不能宣称成熟准确问答产品。

## 运行
Python 3.10+，仅标准库。
```powershell
python app.py
python -m unittest discover -s tests -v
python app.py --output outputs/demo.json
```
默认合成fixture，真实模型调用为0；不会下载、训练或启动模型。
可选单次真实调用，使用自己的既有Ollama实例：
```powershell
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "your-installed-model-tag"
python app.py --live --case path/to/authorized-case.json --output outputs/real.json
```
不包含私有连接配置。便携demo适配器只验证过本地fixture，不能冒称它等同私有环境中独审过的生产授窗sender。单请求无自动重试；发生超时先确认后端终止，再决定下一次调用。

## 功能及责任
- 源内短证据卡：按问题词法及首/末段选取，记录原文范围与字面日期；原文完整由使用者保存。
- 单一schema生成提示与验证器，避免隐藏限制；仅一份简短回答，不生成重复claim.text。
- 正文与标题命名空间分离，引用逐字回读；程序metadata与模型事件判断分开。
- 保留原回答，不自动改日期、替换引文或程序补正确答案；定位通过仍需独立语义审核。

## 真实结果与限制
基线r5：3正式+1烟测、零重试，结构3/3，机械可用1/3，必要3/9、严格0/3；完整记录见docs/evaluation.json。
主要未解决：额外主体/业务扩写、未决回答与肯定断言矛盾、引用改写及事件年份推断。r6用证据卡、字面日期约束和单份简短回答改变生成职责，离线验证不算新准确率。
正式3题共4880输入/1584输出token、50.702秒；烟测加载独立，费用无可信依据为null。小开发样本不支持泛化或单变量因果结论。

## 交付范围
本仓库提供脱敏源码、合成例、测试和真实聚合结论。原文、私有运行日志与审查凭据不上传；数据格式及来源指引见docs/DATA_GUIDE.md。
适合展示AI coding、证据可追溯、结构契约、失败分析及质量成本评估；不能简历写成高准确率合规判断、泛化通过或生产上线。
