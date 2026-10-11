# DISCLAIMER · 免责声明

**请在下载、编译、运行本项目之前完整阅读本文件。**
**Read this entirely before downloading, building, or running this project.**

---

## 1. 项目性质 / Nature of the project

NetGuard 是一个**网络工具**，它做一件事：自动收集互联网上**公开可访问**的免费代理节点，
测试它们的速度，并自动配置你的系统使用其中最快的那些。

NetGuard is a **network utility**. It does one thing: it collects **publicly
reachable** free proxy nodes from the internet, measures their speed, and
automatically configures your system to use the fastest ones.

**它不提供、不运营、不托管任何代理服务器。**
It does **not** operate, host, or provide any proxy servers.

---

## 2. 你必须自己承担的风险 / Risks you assume entirely

### 2.1 你使用的不是你自己的服务

**你通过本软件连接的代理服务器，全部由陌生人运营。**

**Every proxy server you reach through this software is operated by a stranger.**

这意味着：

- **你的流量可能被读取、记录、篡改。** 你在代理里的所有 HTTPS 流量理论上
  都被对方（以及中间链路上的任何人）可见。你**绝不应该**通过它登录网银、
  输入密码、传输任何敏感数据。
- **对方可以随时下线、变卖、或用于任何目的**，包括但不限于违法活动。
  一旦你连接了它，你的出口 IP 就与它绑定，**你可能因此被追责**。
- **节点可能被投毒。** 免费节点生态里存在专门投放追踪节点的群体。
  本项目有去重与评分机制，但**无法保证你拿到的节点是干净的**。

- **Your traffic can be read, logged, or tampered with.** Whoever runs the
  node can see what you do through it. **Never** use a free proxy to reach
  banking, enter passwords, or move anything sensitive.
- **The operator can go away, sell your access, or use it for anything at
  all.** Your exit IP is bound to that node, and you may be held responsible
  for what someone else does with it.
- **Nodes can be poisoned.** There are actors who deliberately publish
  tracking nodes. Dedup and scoring reduce exposure but **cannot guarantee
  a clean node.**

### 2.2 你所在地可能禁止这件事

**代理、VPN、绕过网络限制在很多司法辖区是合法的，在另一些是犯罪行为。**

**你必须自己确认所在地法律，以及你的 ISP / 单位 / 学校的规定。**

Proxies and circumvention tools are **legal in some jurisdictions and criminal
in others.** Some employers and institutions prohibit them outright. **It is
your sole responsibility** to know the rules where you live and work.

违反当地法律使用本软件产生的后果，由使用者自行承担，**作者不承担任何责任**。

### 2.3 免费节点的本质

免费节点的实际体验由第三方决定。本项目实测中，同一批节点的吞吐量在
**29 KB/s 与 1.66 MB/s 之间跳变**，原因是节点随时可能限速、超售、
下线或换IP。

Free nodes are provided as-is by third parties. In our own measurements the
same set of nodes swung between **29 KB/s and 1.66 MB/s**, because operators
throttle, oversell, rotate IPs, and shut down without notice.

**本项目不能保证可用性、速度、匿名性或任何服务水平。**
This project **provides no guarantee** of availability, speed, anonymity, or
any level of service.

---

## 3. 不做什么 / What this project does not do

为了避免误解，明确列出：

| | |
|---|---|
| ❌ | 不提供付费订阅，不出售节点，不运营任何中转 |
| ❌ | 不解密、不劫持、不篡改任何 HTTPS 流量 |
| ❌ | 不注入、不修改系统其他部分（除系统代理设置，可一键还原） |
| ❌ | 不收集、不上传任何遥测数据（见 [PRIVACY.md](PRIVACY.md)） |
| ❌ | 不含后门、矿工、流量劫持代码 |
| ❌ | 不绕过付费墙，不解锁付费内容，不用于 DRM 规避 |

本项目仅供**技术学习、网络研究、以及在合法范围内的个人使用**。

For **technical study, network research, and legitimate personal use only**.

---

## 4. 用户自己的责任 / Your responsibilities

使用本软件即表示你确认：

1. 你已阅读并理解 [第 2 节](#2-你必须自己承担的风险--risks-you-assume-entirely)
2. 你遵守所在地法律、单位与学校的规定
3. 你不会用它访问非法内容、绕过付费墙、或侵犯他人权利
4. 你理解**通过免费代理传输的任何数据都可能被第三方读取**
5. 你自愿承担全部风险，包括账号封禁、IP 被封、法律追究

---

## 5. 第三方组件 / Third-party components

本项目使用了以下开源项目（各自版权与条款见其仓库）：

| 组件 | 版权 |
|---|---|
| [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) | GPL-3.0 |
| [faceair/clash-speedtest](https://github.com/faceair/clash-speedtest) | GPL-3.0 |
| [pystray](https://github.com/mitsuhiko/python-pystray) | BSD-3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |

**注意**：mihomo 与 clash-speedtest 采用 **GPL-3.0**。若你分发包含它们的
二进制或链接产物，**必须同样遵守 GPL-3.0 的源码开放义务**。本仓库自身以
MIT 许可提供，但组合后的完整分发**受 GPL-3.0 约束**。

> mihomo and clash-speedtest are GPL-3.0. If you redistribute anything that
> bundles or links them, you must honour the GPL-3.0 source-disclosure
> requirement. This repository is MIT, but a combined distribution is
> **governed by GPL-3.0**.

---

## 6. 免责 / No warranty

本项目按 **"现状"** 提供，**不作任何明示或暗示的担保**，包括但不限于
适销性、特定用途适用性、不侵权。

**在任何情况下，作者与贡献者不对任何直接、间接、附带、特殊或后果性损害
承担责任**，包括但不限于数据丢失、网络中断、账号封禁、法律纠纷、财产损失。

**建议：不要把本项目当作唯一的网络访问手段。** 它是辅助工具，不是基础设施。

The software is provided **"as is"**, without warranty of any kind. In no
event shall the authors be liable for any damages arising from its use,
including data loss, outages, account bans, legal trouble, or financial loss.

**Do not rely on this project as your only means of network access.**

---

## 7. 节点来源 / Node sources

`config/sources.json` 中的订阅地址指向**公开 GitHub 仓库**，这些仓库由
各自维护者独立运营，**NetGuard 不控制、不审核、不为其内容负责**。

维护者可以随时删除、替换或下架内容。请自行核查每个来源的可靠性与安全性。

**如果你不信任某个来源，从 `config/sources.json` 里删掉它。** 这不是可选项，
这是你的权利。

---

## 8. 报告问题 / Reporting issues

如果你发现**安全问题**（例如代码中确实存在凭据泄露、恶意代码注入等），
请通过 GitHub 私密渠道报告，而不是公开 Issue。

If you find a genuine **security issue** in the code itself, please report it
privately rather than opening a public issue.

---

**再次强调：使用免费代理的风险始终由你自己承担。本项目不改变这一点，
它只是让这个风险分布得更均匀一些。**

**The risk of using free proxies is always yours alone. This project does not
change that — it only distributes that risk a little more evenly.**