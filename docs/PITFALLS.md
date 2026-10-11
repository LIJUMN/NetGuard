# PITFALLS · 踩过的坑

这些是开发这个项目时真实踩到的问题。**记下来是为了让下一个人不重复浪费时间。**

---

## PowerShell 5.1 的三个坑

### 1. `@(... | ConvertFrom-Json)` 把整个数组当成 1 个元素

```powershell
$sources = @(Get-Content $f -Raw | ConvertFrom-Json)
$sources.Count            # -> 1   即使文件里有 10 个对象！
$sources[0].enabled       # -> $null
```

**正确写法**：

```powershell
$raw = Get-Content $f -Raw | ConvertFrom-Json
$sources = @($raw)
```

这个 bug 让 10 个订阅源变成 1 个，排查了很久 —— 因为**它不报错**，
只是安静地什么都不做。

*This one cost hours because it fails silently.*

### 2. `.ps1` 必须纯 ASCII，否则语法崩

PowerShell 5.1 按 **GBK** 读无 BOM 的文件。中文注释里的字节会被解释成
其他字符，**引号被吃掉**，报莫名其妙的语法错误：

```
表达式或语句中包含意外的标记"s') }
哈希文本不完整。
```

**规则：所有 `.ps1` 里不要出现非 ASCII 字符。** 中文只留在 `.py` 里
（Python 3 默认 UTF-8，没这个问题）。

### 3. `Invoke-RestMethod` 会毁掉 UTF-8

读 mihomo API 时，含 emoji 的节点名被按 ISO-8859-1 解码 → 乱码 →
按名字匹配节点定义时 69 个里只匹配上 23 个。

**正确写法**：

```powershell
curl.exe -s -o $tmpFile $url
$txt = [IO.File]::ReadAllText($tmpFile, [Text.Encoding]::UTF8)
$d = $txt | ConvertFrom-Json
```

---

## 测速的两个坑

### 4. 健康检查端点必须真正墙外

一开始用 `gstatic.com/generate_204` 做健康检查。**但它在国内直连就能返回 204**，
于是大量根本没翻墙的国内节点被误判为健康。

```
假阳性 111 个  vs  真实可用 74 个
```

换成 `google.com/generate_204` 后才准。

*Anything reachable without a proxy will look "healthy" if you use it as a
health-check endpoint.*

### 5. 低延迟 ≠ 高带宽 ≠ 连得通

**这是本项目最贵的一个教训。**

三个维度是两两独立的：

| 维度 | 怎么测 | 实测存活 |
|---|---|---|
| 延迟 | 并发 delay 探测，5-13 秒 | 6300 → 80 |
| 带宽 | clash-speedtest 下载测试 | 80 → 21 |
| **连通性** | 真实请求 | **21 → 3** |

实测同一个节点，1 小时内从 704 KB/s 掉到 44 KB/s。
延迟 96ms 的节点带宽可能只有 44 KB/s —— 用户实际体验是搜索页面要 25 秒。

而更糟的是第三层：**14 个节点在延迟和带宽测试里全绿，
真连 Google 时却超时** —— TLS 握手过不去。

不验证连通性会怎样？用户选一个显示"0.55MB/s"的节点，
点开任何国外网站都是转圈。**而托盘是绿的**，因为"核心在跑"。

> **每次改动筛选流程，都必须重跑这三层。** 只测两层会让你以为自己好了。

---

### 5b. 用核心的并发探测，不要自己循环

第一版连通性验证是逐个把节点设成当前节点，然后 curl 一下。**两个问题：**

1. **有副作用** —— 每次切换都打断用户正在用的连接
2. **结果全错** —— 切完立刻发请求用的还是旧连接，实测 **0/20 可用**

正确做法是让内核自己并发测它的 FAST 组：

```
GET /group/FAST/delay?url=<HEALTH_URL>&timeout=8000
```

一个请求测完所有节点，不碰当前选择。

*Promoting each node in turn breaks the live connection every time, and it
measured 0/20 because the request raced the switch.*

---

## 分流相关

### 5c. 不要手写国内域名列表

最早的分流规则手写了 30 个国内域名。**后果是国内几百个站点里漏掉的全部
绕远路走代理** —— 一个在国内 0.15 秒能开的页面，绕一圈变3 秒。

现在只用一行：

```yaml
- GEOSITE,CN,DIRECT     # 内核里展开成 111,224 条
```

`geosite.dat` 随 mihomo 内核分发（4 MB），**不需要订阅第三方规则集**。
实测 `GEOSITE,geolocation-!cn` 也有 27,254 条。

**教训：能用内核内置数据解决的，不要自己维护列表。**

---

### 5d. 自检配置时要把所有代理组重定向

`split_check.py` 验证分流时，把规则里的 `PROXY`/`DOWN` 全替换成 `DIRECT`，
`MATCH` 换成 `REJECT`。这样：

- 国内站能通 ⟹ **只可能**是 DIRECT 规则命中了
- 国外站不通 ⟹ **只可能**是 geodata 规则匹配了它

**没有代理组参与，就不会"碰巧成功"。**

忘了重定向的后果：mihomo 报 `proxy [DOWN] not found`，
而我一度以为是配置写错了 —— 其实是自检脚本没清理干净。

---

## 托盘相关

### 托盘图标旁边放不了文字 —— 这是 Windows 的限制

用户问能不能在托盘小圆点旁边直接显示实时网速。**不能，系统没这个 API。**

那个位置属于资源管理器，只显示其他应用的图标。Clash Verge、Nezha、
everything —— 所有托盘软件都是把速度画进**图标位图**里的。

试了三版才定下来，每版都按真实的 16px 渲染来验证：

| 方案 | 16px 实际效果 |
|---|---|
| 64px 画上下两个速度 | 糊成一团，不可读 |
| 32px 画上下两个速度 + 圆点 | **圆点被压成椭圆**，数字重叠 |
| 32px 只画下载速度数字 | 清楚，空闲时退回圆点 |

教训：**托盘图标必须按 16px 设计，不是按你预览时的尺寸设计。**
第一版我在 64px 预览里看着挺好，缩到 16px 才发现是浆糊。

### 8 秒的健康检查阈值会把慢节点误判成断网

托盘显示"无法连接外网"，但实测 Google 返回 204。原因是耗时：

```
google.com/generate_204  7.6 秒     ← 阈值 8 秒，勉强过
google.com/generate_204 11.1 秒     ← 阈值 8 秒，误报
```

免费节点本来就慢。**阈值卡在实测值旁边 = 必然误报。**
现在放宽到 20 秒，并且加了两个端点做冗余（一个不通换另一个）。

*An 8-second health-check budget sitting next to a 7.6-second measurement is
a coin flip. Free nodes are slow; the check has to tolerate that.*

### 自动选组的判断顺序

第一版 auto_tune 先查"是否持续 12 秒"，导致 **5 MB/s 的下载被判成网页请求**——
第一次采样时计时器还没到。改成：**先看绝对量级，再看持续时间**。

大流量本身就是明确的信号，不需要等它持续。

---

## mihomo 配置的两个坑

### 6. reload 对新增 proxy-provider 不一定生效

改了 provider 列表后热重载，provider 可能静默从列表里消失（`0 nodes`）。
**改 provider 列表必须重启内核**，不能只 reload。

### 7. clash-speedtest 的输出不能直接当 provider 文件

它的 `-output` 写的是**完整 clash 配置**（含 `proxy-providers` 键），
mihomo 的 `proxy-provider` 拒绝加载这种文件。

必须先剥成纯 `proxies:`（见 `intel/clean_fast.py`）。

另外它的 `-min-download-speed` **默认 5 MB/s**，免费节点根本达不到，
必须调到 0.05 左右。

---

## 打包与计划任务

### 8. PyInstaller 打 pystray 要加 hidden-import

```
pyinstaller --hidden-import pystray._win32
```

### 8b. PowerShell 会吃掉 `--add-data` 的冒号

想用 `.ps1` 写打包脚本，结果：

```
pyinstaller: error: argument --add-data: Wrong syntax, should be --add-data=SOURCE:DEST
```

**但命令行直接跑是好的** —— 问题出在 PowerShell 对
`--add-data=C:\path\file:.` 这种参数的解析。

**解法：打包脚本改用 Python**（`build-exe.py`）。Python 里传给子进程的
参数没有中间层，没有引号问题。

顺带两个细节：
- `--add-data` 的源路径要用**绝对路径** —— 给了 `--paths` 之后，
  PyInstaller 解析相对路径的基准会变，spec 里就找不到文件了
- `--paths` 用来让 `pipeline/` 和 `intel/` 能被 import（它们是包，
  不是数据文件）

### 9. schtasks 的嵌套引号

`/TR` 里带 `-Command "..."` 会解析失败。**用 `.cmd` 包装脚本绕开**。

### 10. pystray MenuItem 签名

```python
pystray.MenuItem(text, action)        # 不是 MenuItem(action)
pystray.Menu(text, action)            # Menu 没有 __len__
```

动态刷新的菜单项要继承 `MenuItem` 并重写 `__str__`。

### 11. Windows 托盘画不了文字

想在图标上显示速度，只能**画进位图**（`PIL.ImageDraw`）。
参考 Clash Verge 用 WebSocket 推流的思路，但落地方案不同。

---

## 后台任务

### 12. 后台任务被硬杀会留下锁

pipeline 用 mutex 防重入，同时写 `pipeline.lock` 给托盘判断"忙"。
**进程被强制杀死时 `finally` 块不会执行** → 锁文件残留 → 托盘永远显示黄色。

**规则：锁文件要有陈旧清理。** 现在超过 30 分钟自动清。

### 13. 别把 Python 路径硬编码

`C:\Users\<用户名>\...` 硬编码进去，换机器就废。
用 `sys.executable` 或 `$env:NETGUARD_PYTHON`，`nettray.py` 里用
`sys.frozen` 判断是不是打包后的 EXE。

---

## 泛泛的坑

### 14. 源评分不能直接平均比率

```python
avg_yield = mean([51, 1]) / mean([607, 4368])    # -> 5100%
```

比率要**先平均分子，再除以分母**。这个 bug 让报告里显示「产出率 5100%」。

### 15. 别在样本不足时做减法

第一轮 `anaer` 产出 0，可能是运气。**禁用一个源需要多轮证据**，
现在只禁用「重叠率确认是转载」的。

> 宁可留着垃圾源跑慢一点，也不要误杀好源导致断网。

---

## 最重要的一条

### 16. 别被"源越多越强"骗了

爬几千个免费节点站看着很美，但：

```
源库 10 → 45，节点池只涨 20%，测速时间翻 4 倍
```

**源库膨胀 = 看起来变强，实际更慢更差。**

所有新增采集路径都必须过判重闸门
（`manage_sources.py admit`，80% 重叠率判转载）。这条是整个项目的价值主张，
破坏了它就什么都没了。