# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased] — 首次公开

初始版本，功能如下。

### 核心流程

- **三阶段筛选流水线**（`pipeline/pipeline.ps1`）
  - stage-1：mihomo 并发探测全部节点延迟（5789 → 57，5 秒）
  - stage-2：按指纹提取完整节点定义（57 → 40，2 秒）
  - stage-3：clash-speedtest 实测下载带宽（40 → 20，8 分钟）
  - 全流程约 496 秒

- **托盘状态灯**（`tray/nettray.py`）
  - 四态：正常 / 忙 / 故障 / 未启动
  - 图标内绘制实时上下行速度
  - 33 项右键菜单，6 组
  - 单文件 EXE 可选（PyInstaller）

### 自我进化层（`intel/`）

- **节点指纹去重**：`sha1(server + port + uuid/password/cipher)`
  比单纯 `server:port` 更准 —— 解决多路复用共用端口的误判，
  以及节点换端口后认不出的问题。

- **源去重与剪枝**：重叠率 > 80% 判定转载并剔除
  实测把 10 个源剪到 4 个，节点只掉 9.1%。

- **源质量评分**（`source_stats.json`）
  每轮记录各源实际提供的可用节点数，按产出率排名。
  **这是本项目最有价值的功能** —— 它揭示了哪些源在浪费加载量。

- **节点信誉库**（`node_reputation.json`）
  连挂 3 次 → 拉黑 7 天；连活 2 次 → 自动解封。

- **空闲检测**（`intel/idle.py`）
  采样核心流量计数器，3 秒内移动超 4.5 MB 判为「用户正在用网」，
  最多等 3 分钟再放弃本轮。

- **自我进化报告**（`intel/report.py`）
  生成源质量榜 / 节点信誉榜 / 系统总览三份文本报告。

### 采集器

- **GitHub 源发现**（`pipeline/discover.ps1`）
  10 组关键词搜索 + 探测 23 个常见订阅路径 + 多镜像回退。
  **所有候选必须过判重闸门才入库。**
  GitHub token 只从环境变量读取。

- **Telegram 采集**（`intel/tg_harvest.py`）
  爬 `t.me/s/<channel>` 公开预览页（无需登录，需走代理），
  解析 vmess/vless/trojan/ss/hysteria2 链接。
  合并为单一滚动池 `tg_pool` 而非每频道独立成源。

### 安装与运维

- `install.ps1`：下载依赖（4 个镜像回退）+ 注册 8 个计划任务
- `pipeline/status.ps1`：状态诊断
- `pipeline/proxy-watchdog.ps1`：每 5 分钟自愈
- 每日流水线错开执行：03:30 筛选 → 04:30 TG → 04:45 去重 → 05:15 搜源

### 文档

- `README.md` — 中英日韩德俄六语种
- `docs/ARCHITECTURE.md` — 设计决策与**被否决的方案**
- `docs/PITFALLS.md` — 16 个真实踩过的坑
- `docs/PRIVACY.md` — 隐私与凭据处理
- `docs/INSTALL.md` — 安装详解与故障排查
- `DISCLAIMER.md` — 免责声明（含 GPL-3.0 组合分发说明）
- `SECURITY.md` — 安全策略
- `CONTRIBUTING.md` — 贡献指南

### CI

`.github/workflows/ci.yml`：
- Python 字节码编译
- PowerShell 语法解析
- **`.ps1` ASCII 约束检查**（PowerShell 5.1 的 GBK 陷阱）
- 用 mihomo 官方校验器验证生成的配置
- **凭据扫描**：任何被提交的 token 都会让构建失败

---

## 已知限制

诚实记录，避免期望落空：

| 限制 | 说明 |
|---|---|
| 免费节点速度波动极大 | 实测 29 KB/s ↔ 1.66 MB/s。物理宿命，非程序缺陷 |
| 源评分需要多轮数据 | 前几天没有统计意义，不要急着下结论 |
| 信誉库需 3 轮才生效 | 设计上的安全阀：宁可多测一次也不误杀好节点 |
| stage-3 占全程 90% 时间 | 唯一的真实带宽验证，已评估暂不优化 |
| 二进制未校验哈希 | `install.ps1` 不验证 SHA256，已列入 SECURITY.md 待改进 |
| 无单元测试 | 端到端验证优先 |
| 仅支持 Windows | 大量 PowerShell + Win32 API 依赖 |