<div align="center">

# NetGuard

### 自进化代理节点管家 · Self-Evolving Proxy Node Curator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](docs/ARCHITECTURE.md)
[![Code: 2500+](https://img.shields.io/badge/Code-2500%2B%20lines-blue.svg)](pipeline/)
[![Deps: 2 binaries](https://img.shields.io/badge/Deps-2%20binaries-orange.svg)](docs/INSTALL.md)

**语言 / Languages:** [简体中文](#简体中文) · [English](#english) · [日本語](#日本語) · [한국어](#한국어) · [Deutsch](#deutsch) · [Русский](#русский)

</div>

---

## ⚡ 一句话说清 / In one line

**中文**

> 它自动帮你找节点、筛掉重复的、真实测速,**每天重跑一遍**。
> 但实测告诉我们:**免费节点池是死的** —— 1436 个节点里,能真正完成一次请求的是 **0 个**。
> 所以它的真正用途不是"白嫖节点",而是**把你已经花钱买的那个订阅,管好、用好**。

**English**

> It finds nodes, drops the duplicates, benchmarks real throughput, and **redoes the whole cycle every day**.
> But we measured it: **the free node pool is dead** — of 1436 nodes, the number that could complete a single real request was **0**.
> So its real job isn't "free nodes". It's **keeping the subscription you already pay for, in good shape**.

**下面有实测数据 / Measured data below:** 1436 节点 → 0 可用 · 415 个免费项目 = 同一批死节点的重打包
**怎么让它真正变快 / How to make it actually fast:** 填一行自己的订阅链接,就这一件事。

---

## 这是什么 / What this is

一个 Windows 托盘程序，自动维护一个**免费代理节点池**：从 GitHub 和 Telegram 公开频道采集订阅 → 指纹去重 → 并发筛选 → 测速优选 → 只留最快的那些 → 每天自动重复。

A Windows tray application that maintains a pool of free proxy nodes. It harvests
subscriptions from GitHub and public Telegram channels, deduplicates them by node
fingerprint, screens them concurrently, measures real throughput, keeps only the
fast ones, and repeats the whole cycle every day — without you touching anything.

**它和现成工具的区别 / What makes it different**

大多数同类工具（包括我们调研的 7 个开源项目）都要求你**自己提供订阅链接**，然后只负责处理。NetGuard 多做了一件事：**去全网找新源**，并且只收真正有价值的。

Most tools in this space — including all seven open-source projects we studied —
require you to supply the subscription URLs yourself. NetGuard adds one thing:
it goes looking for new sources on its own, and only keeps the ones that prove
worth keeping.

| | 常见工具 | NetGuard |
|---|---|---|
| 源从哪来 | 你手动填 | GitHub 搜索 + Telegram 采集，**每天自动发现** |
| 重复源 | 全都加载 | 按节点指纹算重叠率，**>80% 直接判定转载并剔除** |
| 源质量 | 不知道 | 每轮记功，**按实际产出率排名** |
| 死节点 | 每次都重测 | 连挂 3 次拉黑 7 天，连活 2 次解封 |
| **国内外分流** | 手写几十个域名 | **`GEOSITE,CN` 覆盖 11 万条**，随内核每日更新 |
| **下载加速** | 只加速浏览器访问 | **按用途分组**：网页 / 下载 / 流媒体各自选最快的节点 |
| **节点验证** | 测延迟和带宽 | **加一层真实连通性** —— 21 个测速达标的节点里只有 3 个真能用 |
| 你正在用网 | 照样跑，抢带宽 | **先检测空闲，最多等 3 分钟** |
| 出问题 | 变个颜色 | **持续 1 分钟主动弹通知** |

---

## 分流与加速：具体怎么做的

### 国内外自动分流

```
国内  →  DIRECT（直连）        实测 0.15-0.36 秒
国外  →  PROXY（走代理节点）
```

关键在于**不用手写域名**：

```yaml
- GEOSITE,CN,DIRECT                  # 内核里展开成 111,224 条
- GEOIP,CN,DIRECT                    # 国内 IP 兜底
- GEOSITE,geolocation-!cn,PROXY      # 27,254 条国外域名
- MATCH,PROXY
```

`geosite.dat` 随 mihomo 内核分发（约 4 MB），首次运行时自动下载最新版本，
**不需要订阅任何第三方规则集**。

### 按用途选节点

「最快」不是一个数。一个节点对打开网页最快，不代表对下载 2GB 文件最快。

| 预设 | 优化什么 | 用在 |
|---|---|---|
| **默认** | 全部节点里延迟最低 | 日常 |
| **网页最快** | 首字节延迟 | 刷网页、看文档 |
| **下载最快** | 持续带宽 | 克隆仓库、下载大文件 |
| **流媒体稳定** | 不掉帧 | 看视频 |
| **优选组** | 上一轮测速达标 | 通用 |

托盘右键 →「用途切换」一键切换。

规则里已经做了针对性分配：

```yaml
- DOMAIN-SUFFIX,release-assets.githubusercontent.com,DOWN   # GitHub 大文件
- GEOSITE,youtube,MEDIA                                    # 视频站
```

### 三层验证（这个很关键）

很多工具只测延迟和带宽。但实测发现：

```
21 个节点通过「延迟 + 带宽」测试
     ↓
只有 3 个能真正完成 google.com 请求
```

**这 18 个节点的测速显示 0.55MB/s，但真连上去什么都打不开。**
它们会回应延迟探测、能跑下载，但 TLS 握手过不去。

所以 NetGuard 加了第三层：写入优选组前，逐个验证真实请求。
**最终留下的每一个节点，都是实测能用的。**

---

## ⚠️ 先读这一段：关于免费节点的实测结论

本项目默认使用公开免费节点。**但免费节点池基本是死的**，这不是猜测，是实测：

| 来源 | 节点数 | 能完成下载 | 通过率 |
|---|---|---|---|
| ermaozi/get_subscribe（★9350） | 653 | 0 | 0% |
| free-nodes/clashfree | 568 | 0 | 0% |
| free18/v2ray（★5624） | 215 | 0 | 0% |
| **合计** | **1436** | **0** | **0%** |

另外从 GitHub 批量扒取的 **415 个**免费节点项目，都是同一批死节点的重新打包。

还有两个关键事实：

- **延迟是谎话**：延迟最低的节点（184ms）往往完全不通。`url-test` 测的是握手往返，
  不代表能穿透。本项目因此坚持做**真实传输测试**，而不是看延迟。
- **节点存活以秒计**：验证可用的节点，30 秒后就死了。

### 结论：想让程序真正变快，只需要一件事

**把你自己的订阅填进去。** 程序所有自动化能力都是现成的——自动扒源、去重、评分、
真实测速、秒级重选——但它无法突破源头。**免费池里没有快节点。**

### 怎么填（三步）

1. 托盘右键 → **「我的订阅（填了才快）」** → 用记事本打开
2. 把你的订阅链接粘进去（用 **Clash 格式**的链接），保存
3. 不用重启。托盘右键 → 状态详情 → 「立即重新筛选」

文件格式随便写：

```
# 以 # 开头是注释
https://你的机场.com/api/v1/client/subscribe?token=xxxx
我的机场 = https://你的机场.com/api/v1/client/subscribe?token=xxxx
```

> **注意**：请用机场提供的 **Clash / Clash Meta 订阅链接**，
> 不是 Base64 或 V2Ray 通用链接。多数机场在订阅页面能选客户端类型。

**这里填的源永远不会被自动禁用、永远不会被去重逻辑删掉。** 即使连续失败也不会关掉它。

### 没有订阅怎么办

| 方案 | 成本 | 说明 |
|---|---|---|
| 付费机场 | 约 10-30 元/月 | 最省事。选口碑老店，注意跑路风险 |
| 自建 VPS | 约 5 美元/月 | 最可控。GitHub 上有成熟一键脚本 |

两条路都是把订阅链接填进上面那个文件，程序立刻从"0% 可用"变成"你的稳定节点"。

---

## 快速开始 / Quick start

### 方式一：单文件 EXE（推荐给非开发者）

**下载 `NetGuard.exe`，放到任何位置，双击。** 不需要装 Python，不需要装任何依赖。

```
第一次运行（约 90 MB 下载，只需一次）
    └─ 自动从 GitHub Releases 拉取 mihomo 内核 + clash-speedtest 测速器
    └─ 数据落在 %LOCALAPPDATA%\NetGuard

之后
    └─ 托盘出现灰色小圆点
    └─ 自动开始下载内核，下载完变绿色
    └─ 右键 → 「立即重新筛选（不等空闲）」
    └─ 8-10 分钟后托盘变绿，右键可以看到节点和速度
```

**为什么能做到"随便放"**：EXE 里不含任何可变数据。二进制、节点池、
日志全部写在 `%LOCALAPPDATA%\NetGuard\`，**不放EXE 旁边**。

所以你可以把它放在 U 盘里、放在只读的 Program Files 里、
甚至放在别人的电脑上 —— 都一样跑。

> 放进别人的电脑前请看 [DISCLAIMER.md](DISCLAIMER.md)。

### 方式二：源码运行（想改代码的人）

```powershell
git clone <this-repo> netguard
cd netguard

powershell -ExecutionPolicy Bypass -File install.ps1 -Download   # 下依赖
powershell -ExecutionPolicy Bypass -File pipeline\pipeline.ps1    # 跑一次筛选
powershell -ExecutionPolicy Bypass -File install.ps1 -InstallTasks # 托盘+每日任务
```

**依赖 / Requirements**

- Windows 10 / 11
- Python 3.9+（仅源码模式需要）
- 会自动下载 `mihomo`（代理内核）+ `clash-speedtest`（测速器）

### 自己编译 EXE

```powershell
git clone <this-repo> netguard && cd netguard
pip install pyinstaller pystray pillow pyyaml
python build-exe.py
# -> NetGuard.exe  （就在项目根目录）
```

> 打包脚本是 Python 而不是 PowerShell —— PS 会把 `--add-data=SRC:DEST`
> 这种参数里的冒号吃掉，PyInstaller 直接报 "Wrong syntax"。踩过一次。

---

## 它是怎么工作的 / How it works

```
     ┌─── 采集 ────┐      ┌── 判重 ──┐     ┌─ 筛选 ─┐    ┌─ 测速 ─┐
     │ GitHub 搜索  │      │ 节点指纹  │     │ 并发探测│    │ 真实带宽│
     │ Telegram 频道 │ ───▶ │ 重叠率判定 │ ──▶ │ 5789→57 │ ──▶ │ →20    │
     └─────────────┘      └──────────┘     └────────┘    └───────┘
        每天 05:15          每天 04:45         5 秒          8 分钟
                          每天 04:30
     ┌────────────────────────────────────────────────────────────────┐
     │  记忆层：源产出评分 + 节点信誉库（连挂拉黑 / 连活解封）              │
     └────────────────────────────────────────────────────────────────┘
                                     ↓
                        PROXY → FAST（8-20 个优选节点）
                                  → AUTO（5000+ 全量兜底）
```

**三阶段筛选 / The three stages**

| 阶段 | 做什么 | 耗时 | 结果 |
|---|---|---|---|
| 1 | mihomo 并发探测全部节点的延迟 | **5 秒** | 5789 → 57 |
| 2 | 按指纹提取完整节点定义 | 2 秒 | 57 → 40 候选 |
| 3 | clash-speedtest 实测下载带宽 | 8 分钟 | 40 → 20 |

第 3 步慢，但它是**唯一**能测出真实带宽的方法 —— 延迟低不代表速度快，免费节点尤其如此（我们实测过同一个节点从 704 KB/s 掉到 44 KB/s）。

Stage 3 is slow but it is the only way to measure real throughput. Low latency
does not mean high bandwidth — we watched the same node drop from 704 KB/s to
44 KB/s within an hour.

---

## 托盘菜单 / Tray menu

无界面、无主窗口。**右键小圆点就是全部操作。**

```
状态：代理正常
当前节点：🇨🇦 CA 001 | ⬇️ 0.55MB/s
实时速度：↑ 120 KB/s   ↓ 3.42 MB/s
自动优化：下载优先（高带宽）—— bulk download 4.8 MB/s
──────────────────────
✓ 系统代理
✓ 开机自启
──────────────────────
我的订阅（填了才快）        ← 想变快就点这里
──────────────────────
状态详情            ▸
  立即重新筛选
  重启内核
  关闭所有连接
  ─────
  我的订阅状态
  节点来源统计
  分流自检
  查看报告
  运行日志
──────────────────────
退出
```

**日常只会用到两个开关 + 一个订阅入口**，其余都在「状态详情」里。

### 图标直接显示下载速度

<img src="docs/icon-16px.png" width="480" alt="tray icons at actual size">

Windows **没有 API 允许在托盘图标旁边显示文字** —— 那个位置属于资源管理器。
所有托盘软件（包括 Clash Verge）都是把速度画进图标位图里的。

NetGuard 的图标就是**当前下载速度的数字**，颜色代表状态：

| 图标 | 含义 |
|---|---|
| 绿点 | 正常，空闲 |
| `1.1M` 绿字 | 正在下载，速度 1.1 MB/s |
| `8.2M` 黄字 | 正在筛选测速，同时下了东西 |
| 红点 | 核心挂了或连不上 |

**上传速度和节点名在悬停提示里**（鼠标移上去就能看到完整信息）。

> 这个布局试了三版才定下来：64px 双数字 → 16px 下糊成一团；
> 32px 双数字 → 圆点被压成椭圆；32px 单数字 → 才既清楚又不变形。

### 自动优化：节点自己跟着你做什么而变

不用手动选"下载模式"。它看流量就知道：

| 你在做什么 | 它切到 | 依据 |
|---|---|---|
| 刷网页 | `WEB` | 单次请求，延迟优先 |
| 下载大文件 | `DOWN` | 持续流量 > 2 MB/s，带宽优先 |
| 看视频 | `MEDIA` | 规则里视频站直连 MEDIA |
| 空闲 | `FAST` | 通用优选 |

判断每 20 秒跑一次（采样是 6 秒，但决策不用这么频繁，否则一次大流量波动就切一次）。

### 分流自检

「状态详情 → 分流自检」会临时起一个测试内核，实测：

```
国内站必须能连通  → baidu 0.19s / taobao 0.26s / bilibili 0.22s
国外站必须被拦下  → google / github / wikipedia 全部 block
```

**结论是实测出来的，不是"配置里写了应该有"。**

---

## 每日自动流水线 / The nightly chain

| 时间 | 任务 | 干什么 |
|---|---|---|
| 03:30 | PipelineDeep | 全量筛选（**先检测空闲**） |
| 04:30 | TgHarvest | Telegram 频道 → 滚动池 |
| 04:45 | SourcePrune | 源去重，剔转载 |
| 05:15 | GitHubDiscover | 全网搜新源 → 判重入库 |
| 每 5 分钟 | Watchdog | 内核挂了自动拉起 |
| 登录时 | Core / Tray / Pipeline | 起内核、起托盘、跑一次 |

时间错开是故意的 —— 全挤在一起会互相抢带宽。

---

## 项目结构 / Layout

```
netguard/
├── install.ps1              下载依赖 + 注册任务
├── pipeline/
│   ├── paths.py             所有路径的唯一来源（无任何硬编码）
│   ├── pipeline.ps1         ★ 主流程编排
│   ├── discover.ps1         GitHub 搜新源
│   ├── core-ctl.ps1         内核起停
│   ├── proxy-watchdog.ps1   自愈
│   ├── status.ps1           状态诊断
│   └── run_pipeline.cmd     计划任务包装器
├── intel/                   ★ 判断力层
│   ├── source_intel.py      指纹 · 重叠率 · 源评分 · 节点信誉
│   ├── manage_sources.py    源库管理（refresh / admit / show）
│   ├── tg_harvest.py        Telegram 公开页采集
│   ├── idle.py              空闲检测
│   ├── record_yield.py      每轮记功
│   ├── speed_result.py      测速结果回灌信誉库
│   ├── report.py            生成三份报告
│   ├── extract_fast.py      提取完整节点定义
│   └── clean_fast.py        剥离 clash-speedtest 的冗余键
├── tray/nettray.py          托盘程序（33 项菜单）
├── config/
│   ├── sources.json         源库
│   └── config.template.yaml 内核配置模板
└── docs/
    ├── ARCHITECTURE.md      设计决策与取舍
    ├── INSTALL.md           安装详解
    ├── PRIVACY.md           隐私与安全
    └── PITFALLS.md          踩过的坑
```

---

## 隐私 / Privacy

**这个程序不收集、不上传、不遥测任何东西。** 它只在本机操作文件，只跟订阅源和 mihomo 内核通信。

- GitHub token 只从环境变量 `GITHUB_TOKEN` 读取，**从不读磁盘文件**
- 不含任何分析、统计、崩溃上报代码
- 运行时数据（节点池、评分、信誉）全部留在 `config/` 本地
- 源库里的订阅地址都是公开仓库，不含任何个人凭据

详见 [docs/PRIVACY.md](docs/PRIVACY.md)。

---

## 贡献 / Contributing

欢迎。但请先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 里的取舍记录——
这个项目最容易被改坏的地方是**过度扩张源库**（看起来更强，实际更慢更差）。

---

## 致谢 / Credits

本项目**没有**重新发明轮子，站在这些项目肩上：

| 项目 | 用途 |
|---|---|
| [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) | 代理内核，Go |
| [faceair/clash-speedtest](https://github.com/faceair/clash-speedtest) | 并发测速，内嵌内核 |
| [clash-verge-rev](https://github.com/clash-verge-rev/clash-verge-rev) | 托盘菜单设计参考（27 项） |
| [beck-8/subs-check](https://github.com/beck-8/subs-check) | 流水线设计参考 |

也感谢所有免费节点源的维护者。他们是真正的无偿基础设施。

---

## 免责声明 / Disclaimer

**请务必阅读 [DISCLAIMER.md](DISCLAIMER.md)。**

简而言之：本项目只操作**公开的免费代理节点**，不提供任何付费服务，
不保证可用性，**使用者需自行遵守所在司法辖区的法律**。

---

<a name="简体中文"></a>
## 简体中文

**它做什么** —— 自动维护免费代理节点池。采集 → 去重 → 筛选 → 测速 → 只留最快的，每天自动重复。

**为什么不一样** —— 现有工具（包括我们调研的 7 个开源项目）都要你手动填订阅链接。NetGuard 自己去全网找新源，并且只收真正有价值的：按节点指纹算重叠率，>80% 判定转载直接剔除；每轮记录各源的实际产出并排名；死节点连挂 3 次拉黑 7 天。

**怎么用** —— 跑 `install.ps1 -Download` 下内核，`pipeline.ps1` 跑一次筛选，`install.ps1 -InstallTasks` 装托盘和每日任务。

**已知限制** —— 免费节点速度波动极大（实测 29 KB/s ↔ 1.66 MB/s），这是物理宿命不是 bug。要稳定高速只能自备付费订阅（加个 URL 进 `config/sources.json` 即可）。源评分需要多轮数据才有统计意义，前几天别急着下结论。

---

<a name="english"></a>
## English

**What it does** — Automatically maintains a pool of free proxy nodes:
harvest → dedupe → screen → speed-test → keep only the fastest, repeating
every day on its own.

**Why it's different** — Existing tools (including all seven open-source
projects we studied) make you paste subscription URLs yourself. NetGuard goes
looking for new sources on its own and keeps only what proves valuable:
overlap between sources is measured by node fingerprint, anything above 80%
is judged a re-post and dropped; each source is scored on nodes it actually
delivered; nodes that die three times get blacklisted for seven days.

**How to use** — Run `install.ps1 -Download` to fetch the binaries, run
`pipeline.ps1` once, then `install.ps1 -InstallTasks` for the tray and the
nightly chain.

**Known limits** — Free node throughput swings wildly (we measured 29 KB/s
and 1.66 MB/s from the same node). That is physics, not a bug. For stable
speed, bring your own paid subscription (drop the URL into
`config/sources.json`). Source scoring needs several rounds before it means
anything — give it a few days before drawing conclusions.

---

<a name="日本語"></a>
## 日本語

**何をするもの** — 無料プロキシノードのプールを自動管理します。
収集 → 重複排除 → 筛选 → 速度測定 → 速いものだけ残す、毎日自動で繰り返します。

**何が違うか** — 既存のツール（調査した7つのオープンソースプロジェクトも
すべて）がサブスクリプションURLの手動入力を求めます。NetGuard は自ら新しい
ソースを探し、有用なものだけを残します。ノードフィンガープリントでソース間の
重複率を算出し、80%超は転載とみなして除外。各ソースが実際に供給したノード数を
記録して評価。3回連続で落ちたノードは7日間ブラックリストにします。

**使い方** — `install.ps1 -Download` でバイナリを取得、
`pipeline.ps1` を1回実行、`install.ps1 -InstallTasks` でトレイと
夜間のスケジュールを登録。

**既知の制限** — 無料ノードの速度は激しく変動します（同一ノードで
29 KB/s と 1.66 MB/s を実測）。これは物理的制約でありバグではありません。
安定した速度が必要な場合は有料サブスクリプションを
`config/sources.json` に追加してください。ソース評価は数回の実行データが
揃うまで有意な判断材料になりません。

---

<a name="한국어"></a>
## 한국어

**무엇을 하나** — 무료 프록시 노드 풀을 자동으로 관리합니다.
수집 → 중복 제거 → 선별 → 속도 측정 → 빠른 것만 유지, 매일 자동 반복.

**무엇이 다른가** — 기존 도구들(조사한 7개 오픈소스 프로젝트 포함)은 모두
구독 URL을 직접 넣으라고 요구합니다. NetGuard는 스스로 새 소스를 찾고,
쓸모 있는 것만 남깁니다. 노드 지문으로 소스 간 중복률을 계산해 80% 초과는
재게시로 판단하고 제외합니다. 각 소스가 실제로 제공한 노드 수를 기록해
평가하고, 3회 연속 실패한 노드는 7일간 블랙리스트에 넣습니다.

**사용 방법** — `install.ps1 -Download`으로 바이너리 다운로드,
`pipeline.ps1` 한 번 실행, `install.ps1 -InstallTasks`로 트레이와
야간 작업 등록.

**알려진 한계** — 무료 노드의 속도는 크게 변동합니다(같은 노드에서
29 KB/s와 1.66 MB/s를 측정). 이는 물리적 제약이며 버그가 아닙니다.
안정적인 속도가 필요하면 유료 구독을 `config/sources.json`에
추가하세요. 소스 평점은 여러 라운드의 데이터가 쌓여야 의미를 갖습니다.

---

<a name="deutsch"></a>
## Deutsch

**Was es tut** — Verwaltet automatisch einen Pool kostenloser Proxy-Knoten:
Sammeln → Deduplizieren → Filtern → Geschwindigkeit messen → nur die
schnellsten behalten, täglich automatisch wiederholt.

**Der Unterschied** — Bestehende Werkzeuge (auch alle sieben untersuchten
Open-Source-Projekte) verlangen, dass man die Abonnement-URLs selbst
einträgt. NetGuard sucht selbst nach neuen Quellen und behält nur, was sich
bewährt: Die Überlappung zwischen Quellen wird über Knoten-Fingerprints
gemessen, alles über 80 % gilt als Repost und wird verworfen. Jede Quelle
wird danach bewertet, wie viele Knoten sie tatsächlich geliefert hat.
Knoten, die dreimal hintereinander sterben, werden für sieben Tage gesperrt.

**Verwendung** — `install.ps1 -Download` lädt die Binärdateien,
`pipeline.ps1` einmal ausführen, `install.ps1 -InstallTasks` für Tray und
Nachtlauf.

**Bekannte Grenzen** — Die Geschwindigkeit kostenloser Knoten schwankt
stark (gemessen: 29 KB/s bis 1.66 MB/s bei demselben Knoten). Das ist
Physik, kein Fehler. Für stabile Geschwindigkeit braucht man ein eigenes
Abo (URL in `config/sources.json` eintragen). Die Quellenbewertung braucht
mehrere Durchläufe, bevor sie Aussagekraft hat.

---

<a name="русский"></a>
## Русский

**Что это делает** — Автоматически поддерживает пул бесплатных прокси-узлов:
сбор → дедупликация → отбор → измерение скорости → остаются только самые
быстрые, цикл повторяется каждый день сам.

**Чем отличается** — Существующие инструменты (включая все семь
исследённых open-source проектов) требуют, чтобы вы сами вставили URL
подписок. NetGuard сам ищет новые источники и оставляет только полезные:
перекрытие между источниками считается по отпечатку узла, всё свыше 80 %
считается репостом и отбрасывается. Каждый источник оценивается по тому,
сколько узлов он реально дал. Узлы, умершие три раза подряд, блокируются
на семь дней.

**Использование** — `install.ps1 -Download` качает бинарники,
`pipeline.ps1` запускает один цикл, `install.ps1 -InstallTasks` ставит
трей и ночное расписание.

**Известные ограничения** — Скорость бесплатных узлов скачет сильно
(измерено: от 29 КБ/с до 1,66 МБ/с на одном узле). Это физика, а не баг.
Для стабильной скорости нужен собственный платный сервис (добавьте URL в
`config/sources.json`). Оценка источников становится осмысленной только
после нескольких запусков.

---

## 许可 / License

[MIT](LICENSE) © NetGuard contributors

**但请注意 [DISCLAIMER.md](DISCLAIMER.md) 里的免责条款 —— MIT 许可不涵盖你使用代理节点的行为。**