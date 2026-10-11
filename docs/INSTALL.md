# Installation · 安装

## 前置条件 / Requirements

| | |
|---|---|
| 操作系统 | Windows 10 / 11（x64） |
| Python | 3.9+ —— **仅后台脚本需要** |
| 磁盘 | ~200 MB（二进制 + geo 数据） |
| 网络 | 能访问 GitHub（国内建议开代理） |

托盘本身可以打包成单文件 EXE，那时目标机器不需要装 Python。
但后台筛选流程（Python 写的）仍然需要。

---

## 三步安装 / Three steps

### 1. 下载依赖（只跑一次）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Download
```

拉取 `mihomo`（代理内核）、`clash-speedtest`（测速器）、geo 数据，约 90 MB。

**国内网络**：`install.ps1` 内置了镜像回退顺序（GitHub 直连 → gh-proxy →
ghfast.top），通常无需手动配置。

### 2. 跑一次筛选

```powershell
powershell -ExecutionPolicy Bypass -File pipeline\pipeline.ps1
```

耗时约 8-10 分钟（其中 8 分钟是真实带宽测速）。
期间可用 `powershell -File pipeline\status.ps1` 查看进度。

### 3. 装上托盘和每日任务

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -InstallTasks
```

**以管理员身份运行**，否则计划任务权限不足。

注销再登录，整条链路生效。

---

## 只想用托盘？/ Tray only

如果你不想要每日自动任务，只想有个状态灯：

```powershell
python tray\nettray.py
```

或在托盘右键勾「开机自启」。

**但请手动跑一次 `pipeline.ps1`** —— 否则托盘只会显示红色。

---

## 可选：GitHub token

`discover`（搜新源）不设 token 也能跑，用未认证 API（60 请求/小时）。
设了 token 配额更高：

```powershell
# 仅当前会话
$env:GITHUB_TOKEN = "ghp_xxx"

# 持久（推荐，因为计划任务在另一个进程里跑）
[Environment]::SetEnvironmentVariable('GITHUB_TOKEN', 'ghp_xxx', 'User')
```

---

## 可选：付费订阅

免费节点速度波动极大（实测 29 KB/s ↔ 1.66 MB/s）。
要稳定就自己加个订阅，`config\sources.json` 里加一条：

```json
{
  "name": "my_provider",
  "url": "https://my-provider.com/subscribe?token=YOUR_TOKEN",
  "enabled": true,
  "fails": 0,
  "added": "manual"
}
```

下次跑 `pipeline.ps1` 时会自动拉取、判重、测速、纳入轮换。

> ⚠️ **带 token 的 URL 不要提交到版本库。**

---

## 可选：打包成 EXE

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole --name NetGuard --hidden-import pystray._win32 tray\nettray.py
```

产物在 `NetGuard.exe`，目标机器不需要装 Python。
（但后台脚本仍需要 Python —— 见上面的说明。）

---

## 卸载 / Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
```

然后删掉整个目录，并还原系统代理：

```powershell
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' ProxyEnable 0
```

---

## 常见问题 / Troubleshooting

**托盘不出现**
```powershell
powershell -File pipeline\status.ps1     # 看内核在不在
python tray\nettray.py                   # 前台运行，看报错
```

**一直显示红色「核心未运行」**
```powershell
powershell -File pipeline\core-ctl.ps1 -Restart
```

**筛选很慢 / 一直跑不完**
正常，stage-3 实测带宽就是慢。可以先减量试跑：
```powershell
powershell -File pipeline\pipeline.ps1 -TopSpeed 15 -SkipSources
```

**搜不到新源**
多半是 GitHub API 限流。设 token 即可。

**节点很少或为 0**
免费节点生态当前状态就这样 —— 实测 6300 个节点里只有 1.2% 能翻墙。
多跑几天，或加付费订阅。

**PowerShell 报中文乱码 / 语法错误**
PowerShell 5.1 按 GBK 读无 BOM 的 `.ps1`。本项目所有 `.ps1` 已强制纯 ASCII。
如果你自己加了中文，改用 `.ps1` + UTF-8 BOM，或把中文移到 Python 里。