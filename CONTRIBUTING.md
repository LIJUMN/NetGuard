# Contributing · 贡献指南

感谢你愿意参与。**这个项目最容易被改坏的地方，恰好是最容易顺手改的地方**，
所以先读这一页。

---

## 最重要的红线 / The one hard rule

**任何新增的采集路径，必须接同一道判重闸门。**

```python
ok, verdict, detail = manage_sources.admit(name, path)
if not ok:
    return  # 转载，不入库
```

绕过它会破坏整个项目的价值主张。理由：

```
源库  10 → 45   （看起来更全）
节点池 +20%     （大部分是重复的）
每次测速时间 ×4 （重复的也要过一遍延迟探测）
```

**看起来变强了，实际更慢更差。** 这是这个项目最容易犯的错。

如果你的 PR 会增加源，请附上：

- 判重逻辑的调用点
- 实测的重叠率数据（用 `manage_sources.py show`）
- 如果绕过了 80% 阈值，请解释为什么这个源是例外

---

## 开发流程 / Workflow

```powershell
git clone <repo> && cd netguard

# 依赖（只会下一次）
.\install.ps1 -Download

# 改完先验证语法
python -m compileall -q intel tray
powershell -File .\pipeline\status.ps1

# 端到端
.\pipeline\pipeline.ps1 -SkipSources -TopSpeed 15    # 快速试跑
.\pipeline\pipeline.ps1                            # 完整跑

# 生成报告看效果
python intel\report.py
```

---

## 代码风格 / Style

| | |
|---|---|
| Python | 4 空格，函数与文档注释用英文 |
| **`.ps1` 文件** | **必须纯 ASCII**（PowerShell 5.1 按 GBK 读，会崩） |
| 中文 | 只能出现在 `.py` 和 `.md` 里 |
| 行宽 | 100 字符 |
| 注释 | 解释**为什么**，不解释**做什么** |

CI 会强制检查 `.ps1` 的 ASCII 约束。

---

## 提交前自查 / Before you open a PR

```powershell
python -m compileall -q intel tray pipeline      # 必须过
python intel\report.py                            # 必须能跑
git status --short                                # 看有没有误提交
```

**特别检查**：

- [ ] 没有 `config/subs/*.yaml`（订阅内容）
- [ ] 没有 `bin/`（90 MB 二进制）
- [ ] 没有 `.env` / `*.token`
- [ ] 如果改了 `config/sources.json`，确认没有**带 token 的付费订阅 URL**
- [ ] 新增采集路径接了判重闸门

CI 里有一个 secret scan 会失败于任何被提交的凭据。

---

## 好的 PR 长什么样 / Good PRs

- **说明实测数据**，不只是说"我改了"。比如"这个源产出率 4.45%"比"我加了个源"有用得多
- **记录被否决的方案**。如果你试过某个思路但它不行，写下来 —— 别人不用再试一遍
- **一次只做一件事**。重构 + 新功能 + 格式化混在一起，review 很难做

---

## 不接受的 PR / What we will decline

- ❌ **增加源数量但不接判重**（见红线）
- ❌ **加 GUI 面板** —— 用户明确要「无界面、全靠右键」
- ❌ **加节点手动切换菜单** —— 用户要的是状态灯，不是操作面板
- ❌ **把二进制提交进仓库** —— 90 MB，`install.ps1` 会下载
- ❌ **收集任何遥测** —— 见 [PRIVACY.md](PRIVACY.md)

---

## 安全问题 / Security issues

**不要开公开 Issue。** 用 GitHub 的私密安全通告渠道。

---

## Code of Conduct

参与即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。