# Architecture · 架构与设计决策

这份文档记录**为什么这么设计**，包括被否决的方案。
代码能说明"怎么做"，这里说明"为什么不那样做"。

---

## 1. 核心问题 / The actual problem

免费代理节点的生态是**金字塔**，不是散布的：

```
        ┌──────────────┐  少数源头：机场免费版、个人节点农场
        │   源头        │  每天新增几十个
        └───────┬──────┘
                │  转载
        ┌───────▼──────┐  几十个聚合源 ← 我们在这一层
        │   聚合层      │  GitHub 仓库 / Telegram 聚合频道
        └───────┬──────┘
                │  转载
        ┌───────▼──────┐  几千个网站/频道，90% 是转载聚合层
        │   底层        │
        └──────────────┘
```

**爬底层几乎没有边际收益。** 实测：`freeclashnode.com` 给出 35 个订阅 URL，
看起来很多，但它们大概率指向同一批 GitHub 仓库 —— 源库从 10 涨到 45，
节点池可能只涨 20%，而每次测速时间翻 4 倍。

**这就是"全网扒源"是个陷阱的原因**：它让你以为在变强，实际是在堆垃圾。

Climbing the bottom layer has near-zero marginal return. We measured this:
one aggregator site listed 35 subscription URLs that almost certainly pointed
back at the same handful of GitHub repos. Growing the library 10 → 45 would
have added ~20% more nodes at 4x the screening cost.

**所以我们只爬中间层，并且强制判重。**

---

## 2. 五层设计 / The five layers

### L1 采集 · Harvest
- **GitHub**（`discover.ps1`）— 搜索仓库 + 探测常见订阅路径
- **Telegram**（`tg_harvest.py`）— 爬 `t.me/s/<channel>` 公开预览页，无需登录

实测发现 **Telegram 频道节点量极小（3-15 个）但很新鲜；GitHub 聚合源量大但陈旧。**
两者互补，所以设计成**合并成一个滚动池 `tg_pool`**（单一源）而不是每频道独立成源 ——
源库只 +1 而不是 +N。

*Telegram channels carry very few nodes (3-15) but they are fresh; GitHub
aggregators carry thousands but go stale. Complementary, so they are merged
into one rolling pool rather than N separate sources.*

### L2 判重 · Dedupe

节点身份用**指纹**判定，不是名字：

```python
fingerprint = sha1(server + port + uuid_or_password_or_cipher)
```

只用 `server:port` 太粗：多路复用中继常共用端口（误判重复），
同一节点换端口又会被当成全新的（认不出）。加入凭据后两个问题同时解决。

实测效果：
```
mahdibland_eternity  100% ⊂ mahdibland_merge   → 剔除
ripao                100% ⊂ ermaozi            → 剔除
chengaopan            99.4% ∩ peasoft          → 剔除
peasoft               97%  ∩ chengaopan         → 剔除

10 源 → 4 源，节点只掉 9.1%
```

**关键：判重闸门对所有采集路径一视同仁。** GitHub 搜索和 Telegram 采集
都必须过同一道门 —— 否则 discover 会变成那个把库撑爆的元凶。

### L3 筛选 · Screen
分两个阶段，原因见 [第 4 节](#4-为什么分三个阶段)。

### L4 测速 · Measure
用 [`faceair/clash-speedtest`](https://github.com/faceair/clash-speedtest)，
它内嵌 mihomo 内核、并发测速、支持丢包过滤、直接输出筛选后的配置。
**测速这块我们一行没写。**

### L5 记忆 · Remember

两个持久化的库：

**`source_stats.json`** —— 每轮记功
```
ermaozi              27/607    4.45%   ← 唯一高产
mahdibland_merge      0/4368   0.00%   ← 占 79% 加载量，产出 0
```

这是**最值钱的功能**。上线第一天就炸出了真相：以前每次跑都在为
4368 个死节点白费时间，而我们根本不知道。

**`node_reputation.json`** —— 节点信誉
- 连挂 3 次 → 拉黑 7 天
- 连活 2 次 → 自动解封
- 记录实测速度，用于跨轮排序

---

## 3. 被否决的方案 / What we rejected and why

| 方案 | 为什么不做 |
|---|---|
| **爬几千个免费节点站** | 边际收益≈0，95% 是转载。实测 `nodefree.org` 只给 2 个 URL，`freeclashnode.com` 给 35 个但都指向同一批仓库 |
| **爬 Telegram 私有/需鉴权频道** | 需要 API 凭据，收益不确定，会让整个流程变脆 |
| **用 mihomo 起 N 个 listener 并行测速** | 我们做了，发现不如直接用 clash-speedtest —— 它已经内嵌内核，进程通信开销为零 |
| **Go 内嵌 mihomo 当库** | `subs-check` 这么做，性能最好。但改造成本高、迭代慢。测速已有现成方案，自研无意义 |
| **爬 APK / 逆向订阅地址** | 用户提供过一个 `com.wrongchao.v2vpn` 的 APK。所有静态路径失败（订阅走 Firebase 动态下发，REST API 强制 OAuth2），抓包链路虽已跑通但用户放弃。**结论：逆向第三方 App 的订阅不适合放进开源项目** |
| **单次快照定胜负** | 实测同一节点 1 小时内从 704 KB/s 掉到 44 KB/s。**单次数据会骗人** |
| **按产出率自动禁用源** | 危险。第一轮 `anaer` 产出 0 可能只是运气。**证据不足时做减法比不做更危险** —— 现在只禁用「重叠率确认是转载」的源 |
| **Web 面板 / GUI 界面** | 用户明确要求「没界面、全靠右键」。加了就违背需求 |
| **托盘点击切换节点** | 同上。用户要的是「状态灯」，不是「操作面板」 |

---

## 4. 为什么分这些阶段 / Why these stages

因为**延迟低 ≠ 带宽大 ≠ 连得通**。实测数据：

```
6300 个节点
  └─ 并发延迟探测（5-13 秒）      → 40-80 个"能响应"
      └─ 提取定义 + 实测带宽        → 20 个"跑得动"
          └─ 连通性验证（新增）      → 3-8 个"真能用"
```

**第三层是踩坑加上的。** 一次真实运行里，21 个通过带宽测试的节点中
**只有 3 个能完成 `google.com/generate_204` 请求**。其余的会回应延迟探测、
能跑下载，但真连 Google 时超时 —— TLS 握手过不去。

如果不加这层，用户会选到一个"测速显示 0.55MB/s"的节点，然后发现什么都打不开。

**这个 bug 极其隐蔽**，因为三道测试各自都是绿的。

| 不做 | 会怎样 |
|---|---|
| 不用 Google 端点做健康检查 | `gstatic.com/generate_204` 在国内**直连就能返回 204**，会把根本没翻墙的国内节点误判为健康（实测假阳性 111 vs 真实 74） |
| 不测带宽只用延迟 | 用户拿到"延迟 96ms 但只有 44 KB/s"的节点，搜索页面要 25 秒 |
| **不做连通性验证** | 用户拿到"测速 0.55MB/s 但连不上任何国外站"的节点 |
| 不做三次采样 | 单次快照骗人 |

---

## 4b. 分流：一条规则顶 11 万条 / Split routing

**实测数据**：`GEOSITE,CN` 在内核里展开成 **111,224 条**记录。

之前这个项目的分流是手写 30 个域名。后果是：国内几百个站点里，
**漏掉的全部绕远路走代理** —— 一个在国内 0.15 秒能开的页面，
绕一圈变成 3 秒。

现在：

```yaml
rules:
  - GEOSITE,CN,DIRECT          # 111,224 条国内域名
  - GEOIP,CN,DIRECT            # 兜底国内 IP
  - GEOSITE,geolocation-!cn,PROXY   # 27,254 条国外域名
  - MATCH,PROXY
```

**关键：geosite.dat 随内核分发（4 MB），不需要订阅任何规则集。**
它是 mihomo 官方维护的数据，bootstrap 时自动下载最新版本。

国内站实测 **0.15-0.36 秒**，国外站走代理。

---

## 4c. 用途分组：为什么"最快"不是一个数 / Purpose-built groups

一个节点对"打开网页"最快，不代表对"下载 2GB 文件"最快。
反之亦然。所以不追求单一的"最快"，而是按用途分组：

| 组 | 优化目标 | 探测间隔 | 用在哪 |
|---|---|---|---|
| `WEB` | 首字节延迟 | 300s | 刷网页、看文档 |
| `DOWN` | 持续带宽 | 600s | 克隆仓库、下载压缩包 |
| `MEDIA` | 不掉帧 | 300s | 看视频、听音乐 |
| `FAST` | 上一轮测速达标 | 60s | 通用优选 |
| `AUTO` | 全部节点里延迟最低 | 120s | 兜底 |
| `FALLBACK` | 永不为空 | 120s | 最后一道 |

**`interval` 故意设得很长**（DOWN 是 600 秒）。这些组是为了带宽，
每 60 秒重探一次会白白吃掉用户正在下载的带宽。

规则里把大文件下载直接指向 `DOWN` 组：

```yaml
- DOMAIN-SUFFIX,githubusercontent.com,DOWN
- DOMAIN-SUFFIX,release-assets.githubusercontent.com,DOWN
```

---

## 5. 空闲检测 / Idle detection

凌晨 03:30 的全量筛选有 8 分钟会占带宽。**如果那时你正在用网，它会抢。**

做法：采样 mihomo 的流量计数器两次，间隔 3 秒。移动超过 4.5 MB
（约 1.5 MB/s 持续吞吐）判定「你在用网」，最多轮询 18 次（约 3 分钟），
仍繁忙则**放弃本轮**，保留下次机会。

开机后 3 分钟那次会跳过这个检查 —— 那时候你多半刚坐下，正是最该跑的时机。

---

## 6. 可移植性 / Portability

**目标：单个 EXE 扔到任何 Windows 机器，双击就能用，什么都不用装。**

这一条比看起来难，因为它要求同时满足三件事：

| 要求 | 做法 |
|---|---|
| 不依赖外部 Python | 筛选流程用 **Python 重写**（`intel/pipeline.py`），不再 shell out 到 `python` 命令 |
| 不依赖旁边的文件 | PyInstaller 打包时带的是**代码**，不是数据。所有可变状态写到 `%LOCALAPPDATA%\NetGuard\` |
| 不依赖固定路径 | `pipeline/paths.py` 是唯一路径来源，支持 `NETGUARD_ROOT` / `NETGUARD_HOME` 覆盖 |

**PyInstaller 的 `_MEIPASS` 是关键**，它指向一个临时解压目录：

```python
BUNDLE = getattr(sys, "_MEIPASS", "")   # 只读，含全部代码和种子数据
HOME   = "%LOCALAPPDATA%\\NetGuard"     # 可写，放一切会变的东西
```

于是同一个二进制在两种模式下都能跑：

| | 源码模式 | EXE 模式 |
|---|---|---|
| 代码来自 | 仓库目录 | `_MEIPASS` 临时目录 |
| 长任务 | 起子进程跑 `python intel/x.py` | **线程直接跑**（模块已在内存里） |
| 数据写入 | 仓库目录 | `%LOCALAPPDATA%\NetGuard` |
| 首次运行 | 手动 `install.ps1 -Download` | `intel/bootstrap.py` 自动拉 |

**二进制不打包**（90 MB 且会过期）。首次运行时 `bootstrap.py` 从
GitHub Releases 拉 `mihomo` 和 `clash-speedtest`，国内走镜像回退。
这也顺带解决了"分发的内核版本过期"问题 —— 每次拿的都是最新的。

---

## 7. 托盘设计 / Tray design

参照 [`clash-verge-rev`](https://github.com/clash-verge-rev/clash-verge-rev) 的
`core/tray/menu_def.rs`（27 项）并扩展成 33 项。

**Windows 托盘无法在图标上画文字** —— Clash Verge 用 WebSocket 推流
显示上下行速度。我们用位图绘制：在图标上下画两条横条，长度代表流量。
悬停 tooltip 同时显示数值。

状态灯只有四态，不做多余信息：

| 状态 | 颜色 | 含义 |
|---|---|---|
| 正常 | 实心绿 | - |
| 忙 | 绿+白心，半透明 | 正在筛选 |
| 故障 | 实心红 | 内核挂或连不上 |
| 未启动 | 灰色空心 | 内核没起来 |

---

## 7. 已知技术债 / Known tech debt

| 问题 | 状态 |
|---|---|
| 源评分样本不足 | 前几轮数据没有统计意义，需 3-5 天 |
| 信誉库需 3 轮才生效 | 设计上的安全阀：宁可多测一次也不误杀 |
| stage-3 串行测速占 90% 时间 | 已评估，暂不优化 |
| 无单元测试 | 端到端验证优先，这项目只有一个用户 |
| EXE 27.5 MB | Python 打包固有代价。Go 重写可压到 8 MB，但成本高收益低 |
| 免费节点速度波动 | **物理宿命，非程序缺陷** |

---

## 8. 贡献前请读 / Before contributing

这个项目**最容易被改坏的地方是过度扩张源库**。

看起来「多爬点源」是明显的增强，但：
- 源库膨胀 → 每次加载更多 → 测速更慢
- 重复源没有价值 → 95% 的"新源"是转载

**任何新增采集路径，必须接同一道判重闸门**（`manage_sources.py admit`）。
绕过它会破坏整个系统的价值主张。

如果你的 PR 会增加源数量，请附上判重逻辑和实测的重叠率数据。