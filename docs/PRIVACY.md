# Privacy & Security · 隐私与安全

## 这个程序收集什么 / What this collects

**什么都不收集。**

NetGuard **没有**遥测、没有分析、没有崩溃上报、没有网络回传。
它只在本机读写文件，只跟两类外部通信：

| 通信对象 | 目的 | 发出的内容 |
|---|---|---|
| 订阅源（GitHub / Telegram） | 拉取节点配置 | 普通 HTTPS GET，附带你的出口 IP |
| `mihomo` 本地 API（127.0.0.1:9090） | 读状态、写配置 | 本机回环，不出网 |
| GitHub API | 搜新源 | **仅当设置了 `GITHUB_TOKEN`** |

**换句话说：你的 IP 会暴露给你订阅的每一个源站 —— 这是免费代理的固有属性，
不是本项目收集的。** 如果你在意，用付费订阅。

---

## 凭据处理 / Credential handling

### GitHub token —— 只从环境变量读

```powershell
$env:GITHUB_TOKEN = "ghp_xxx"     # 推荐：仅当前会话
[Environment]::SetEnvironmentVariable('GITHUB_TOKEN', 'ghp_xxx', 'User')  # 持久
```

**代码里不会、也不应有任何地方从磁盘文件读 token。**

> 早期版本曾从某个本地凭据文件读取 token —— 那是个人环境的产物，
> 开源版本已彻底移除。这条规则写进了 [ARCHITECTURE.md](ARCHITECTURE.md) 的红线。

**没有设置 token 也能跑** —— 退回 GitHub 未认证 API（60 请求/小时，
够 `discover` 每天跑一次）。

`.gitignore` 已屏蔽 `.env`、`*.token`、`secrets*.json`。

### 内核 API

`mihomo` 的控制接口在 `127.0.0.1:9090`，**仅监听回环**。
`config.template.yaml` 里已设置 `secret: ""`，即不启用鉴权 ——
因为只在本机。如果你要把控制端口暴露到局域网，**务必设置 secret**。

### 系统代理

程序会修改 `HKCU\...\Internet Settings` 的 `ProxyEnable` / `ProxyServer`。
这是代理类工具的常规做法。托盘右键可以随时关掉，或：

```powershell
# 还原
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' ProxyEnable 0
```

---

## 凭据来源的风险 —— 请务必理解

**这是本项目最大的安全风险，不是代码问题，是使用模式的问题。**

你通过本软件连接的每个代理节点，**都由陌生人运营**。

| 风险 | 说明 |
|---|---|
| **流量被读取** | 你的 HTTPS 流量对节点运营者可见。**绝不要**用它登录网银、输密码、传敏感数据 |
| **出口 IP 绑定** | 你连接它的那一刻，你的出口 IP 就与它关联。运营者若违法，**追查到的是你** |
| **节点投毒** | 免费节点生态存在专门投放追踪节点的攻击者 |
| **中间人** | 节点运营者能看到 HTTPS 元数据（域名、时间、流量大小），除非你用 TLS  pinning |
| **随时消失** | 运营者可以随时下线或换IP，你的连接会中断 |

**本项目能做的**：去重（减少接触面）、评分（优先选活的）。
**本项目不能做的**：验证节点是否干净 —— 那需要你自己信任它。

> 请阅读 [../DISCLAIMER.md](../DISCLAIMER.md) 第 2 节的完整说明。

---

## 内核完整性 / Binary integrity

`install.ps1 -Download` 从 GitHub Releases 拉取 `mihomo` 和 `clash-speedtest`。

**当前实现不校验哈希。** 这是一个已知缺口，我们选择先保持简单。
如果你在意完整性，可以手动验证：

```powershell
Get-FileHash .\bin\mihomo\mihomo.exe -Algorithm SHA256
# 与 https://github.com/MetaCubeX/mihomo/releases 的发布说明比对
```

**欢迎 PR 补上哈希校验** —— 这是个明确的改进点，见
[ARCHITECTURE.md](ARCHITECTURE.md) 的技术债列表。

---

## 目录里哪些东西不进版本库 / What is never committed

`.gitignore` 屏蔽了：

```
bin/                        下载的二进制
config/config.yaml         自动生成的配置
config/subs/*.yaml         订阅内容（可能含敏感 URL）
config/*.json (运行时)      评分、信誉、状态
logs/  reports/            运行时输出
.env  *.token  secrets*    凭据
```

**提交前请自己再检查一遍**：如果你往 `config/sources.json` 里加了
带 token 的**付费订阅链接**（常见 URL 参数如 `?token=xxx`），
**那属于私有凭据，不要提交**。

---

## 报告安全问题 / Reporting a security issue

如果你在**代码本身**里发现真实的安全问题（凭据泄露、注入、恶意逻辑），
请通过 GitHub 的私密渠道报告，**不要开公开 Issue**。

---

**总结：这个项目不碰你的数据。但你使用的第三方节点会 ——
这是免费代理无法回避的固有风险，本项目只是让它分布得更均匀一点。**

**Summary: this project does not touch your data. The third-party nodes you
connect through will. That is inherent to free proxies, and no tooling can
change it.**