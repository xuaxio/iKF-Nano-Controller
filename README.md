# iKF-Nano 电脑端控制软件（学习项目）

面向 iKF-Nano 系列蓝牙降噪耳机的 Windows 图形化控制工具。仅用于个人设备的控制适配与学习研究。

> **Keywords / 关键词：** `bluetooth ANC headphones` `noise cancelling earbuds` `Active Noise Cancellation` `LDAC` `BLE GATT` `Windows desktop app` `iKF earbuds controller` — 蓝牙降噪耳机、主动降噪、通透模式、降噪档位、LDAC 高音质、电量显示

![图标](ikf_icon.ico)

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

## 声明

- 本项目仅用于**学习、研究和技术交流**，请仅用于你自己合法拥有的设备。
- **禁止商业使用**、禁止用于任何营利或商业目的。
- 本项目与耳机品牌方及其官方 App 无任何合作或授权关系，不包含、不分发任何官方私有资源。
- 使用本软件所造成的任何后果由使用者自行承担。
- 如涉及任何权利归属问题，请权利人联系，本项目将立即下架相关内容。

## 环境需求

- 运行 exe：Windows 10/11
- 源码运行：Python 3.9+，`pip install bleak pystray pillow`