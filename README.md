# iKF-Nano 电脑端控制软件（学习项目）

面向 iKF-Nano 系列蓝牙降噪耳机的 Windows 图形化控制工具。仅用于个人设备的控制适配与学习研究。

> **Keywords / 关键词：** `iKF` `iKF-Nano` `iKF controller` `iKF 耳机` `iKF earbuds` `bluetooth ANC headphones` `noise cancelling earbuds` `Active Noise Cancellation` `LDAC` `BLE GATT` `Windows desktop app` — 蓝牙降噪耳机、主动降噪、通透模式、降噪档位、LDAC 高音质、电量显示

![图标](ikf_icon.ico)

## 下载（直接使用，无需 Python）

**➡️ [点此一键下载 iKF-Nano.exe](https://github.com/xuaxio/iKF-Nano-Controller/releases/latest/download/iKF-Nano.exe)**  （约 31 MB，Windows 10/11）

点开即下载，无需 Python、无需安装任何依赖，下载后双击就能用（这就是本控制软件的可执行文件，GitHub 会自动把中文名规范成 `iKF-Nano.exe`）。

- 首次运行若弹出「Windows 已保护你的电脑」，点 **更多信息 → 仍要运行**（exe 未购买代码签名证书，属正常提示）
- 想选其他版本：到 [Releases 页面](https://github.com/xuaxio/iKF-Nano-Controller/releases) 手动下载

## 功能

| 功能 | 说明 |
|---|---|
| 降噪控制 | 正常 / 通透 / 降噪三种模式，降噪下可选 自适应 / 轻度 / 均匀 / 重度 四档强度 |
| LDAC 开关 | 一键切换 LDAC 高音质模式 |
| 状态显示 | 实时显示当前降噪状态、LDAC 状态、电量 |
| 设置记忆 | 记住上次的降噪档位与 LDAC 设置，重连/重启后自动恢复 |
| 自动重连 | 检测到蓝牙断连后自动重新连接，并恢复记忆设置 |
| 后台驻留 | 关闭窗口最小化到系统托盘，随时恢复 |
| 开机自启 | 可选开机静默后台运行，自动连接耳机并恢复设置 |

## 使用

```
python ikf_app.py                    # 打开图形界面
python ikf_app.py --autostart        # 静默后台运行
python ikf_control.py status         # 命令行读状态
python ikf_control.py anc deep       # 命令行切降噪-重度
python ikf_control.py ldac on        # 命令行开 LDAC
```

图形界面下直接点选即可，发送后自动读回确认。

## 协议概览

适配 iKF-Nano 系列耳机基于 BLE GATT 的控制指令，指令通过写特征下发、通知特征回读。

## 使用注意

- **BLE 控制通道为单连接**：电脑连接时，手机 App 需先断开；反之亦然
- **切换 LDAC 会短暂断连**，并会把降噪重置为"正常"，本工具会自动重连并恢复记忆的降噪档位

## 更新日志

### v0.1.1 — 2026-09-28
- **修复开机时降噪档位多次来回跳**：记忆恢复改为「先读回真实状态 → 逐项比对 → 只下发有差异的指令」，不再无条件重发 LDAC、不再重复补设降噪
- 修复关闭 LDAC 后状态不刷新、蓝牙已断连仍显示「已连接」：新增每 3 秒状态轮询 + 断连看门狗（自动重连并恢复设置）
- 修复打包运行时 `sys.stdout` 为 `None` 导致启动崩溃
- 修复窗口图标退回 Tk 默认图标（打包未包含图标资源）
- 修复记忆配置分家：开机自启统一指向 exe，消除两套配置漂移
- 修复「重新连接」按钮不可靠：消除与看门狗的连接竞争、断连后延迟 0.6 秒再重连、增加「连接中…」状态反馈
- 移除界面上的「断开/连接」按钮（与后台自动连接定位重复）

### v0.1.0 — 2026-09-26
- 首个发布版本
- 降噪控制：正常 / 通透 / 降噪（自适应 / 轻度 / 均匀 / 重度）
- LDAC 高音质开关
- 实时状态与电量显示
- 设置记忆：重连 / 重启后自动恢复上次档位
- 自动重连、系统托盘驻留、开机自启动
- 图形界面（`ikf_app.py`）与命令行工具（`ikf_control.py`）
- 自定义图标（黑底白字 1KF）

> 完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 声明

- 本项目仅用于**学习、研究和技术交流**，请仅用于你自己合法拥有的设备。
- **禁止商业使用**、禁止用于任何营利或商业目的。
- 本项目与耳机品牌方及其官方 App 无任何合作或授权关系，不包含、不分发任何官方私有资源。
- 使用本软件所造成的任何后果由使用者自行承担。
- 如涉及任何权利归属问题，请权利人联系，本项目将立即下架相关内容。

## 环境需求

- 运行 exe：Windows 10/11
- 源码运行：Python 3.9+，`pip install bleak pystray pillow`