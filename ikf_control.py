# -*- coding: utf-8 -*-
"""
iKF-Nano 电脑端 BLE 控制工具（协议破解版）

协议来源：通过 bugreport 的 btsnoop HCI 日志完整逆向了手机 App 与耳机的通信。
帧结构：  FF <SEQ> <LEN> <CMD+PAYLOAD...> AA      （LEN = CMD+PAYLOAD 字节数，无校验位）
GATT 通道：服务 0x00FE
    写命令  -> 特征 0x00F1 (handle 0x0012, write)
    收回执  <- 特征 0x00F2 (handle 0x0014, notify)   CCCD 0x0015 写 01 00 开启
命令字：
    09 MODE LEVEL   设置降噪模式   MODE: 01=降噪 02=正常(关) 03=通透
                                   LEVEL: 降噪时 00=自适应 01=轻度 02=均匀 03=重度
    4d 00/01        设置 LDAC      00=关(AAC/SBC) 01=开(LDAC)
    fa 09           查询降噪模式   回执 09 MODE LEVEL
    fa 4d           查询 LDAC      回执 4d 00/01
    fa 0c           查询电量       回执 0c <百分比> ...
回执：每条命令先回 ACK 帧 FF SEQ 01 FE AA；查询类再跟一条数据帧。

⚠ 耳机 BLE 控制通道为单连接：手机 App 需先断开/退出，电脑才能接管。
⚠ 切换 LDAC 会短暂断连并把降噪重置为“正常”。

用法：
  python ikf_control.py status                 # 读当前 降噪/LDAC/电量
  python ikf_control.py anc off                # 正常(关闭降噪)
  python ikf_control.py anc transparency       # 通透
  python ikf_control.py anc adaptive           # 降噪-自适应
  python ikf_control.py anc light              # 降噪-轻度
  python ikf_control.py anc balanced           # 降噪-均匀
  python ikf_control.py anc deep               # 降噪-重度
  python ikf_control.py ldac on|off            # LDAC 开关
  python ikf_control.py raw "ff 01 03 09 01 03 aa"   # 原样发帧
依赖：bleak  (pip install bleak)
"""
import sys, asyncio, argparse
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from bleak import BleakClient, BleakScanner

DEFAULT_MAC = "B0:F0:0C:90:F0:73"

# 模式/档位映射（由日志逆向确认）
ANC_MODES = {"off": 0x02, "normal": 0x02, "transparency": 0x03, "ambient": 0x03}
ANC_LEVELS = {"adaptive": 0x00, "light": 0x01, "balanced": 0x02, "deep": 0x03}
MODE_NAME = {0x01: "降噪", 0x02: "正常(关)", 0x03: "通透"}
LEVEL_NAME = {0x00: "自适应", 0x01: "轻度", 0x02: "均匀", 0x03: "重度"}


class IKF:
    def __init__(self, mac):
        self.mac = mac
        self.client = None
        self.seq = 0x10            # 序列号计数（与手机一样递增即可）
        self.wr_uuid = None        # 0x00F1 写特征
        self.ntf_uuid = None       # 0x00F2 通知特征
        self.pending = {}          # seq -> asyncio.Future（等数据回执）
        self.last_frames = []

    # ---------- 帧 ----------
    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    @staticmethod
    def frame(seq, payload):
        # payload = CMD+PAYLOAD 字节
        return bytes([0xFF, seq, len(payload)]) + payload + bytes([0xAA])

    # ---------- 连接 ----------
    async def connect(self):
        self.client = BleakClient(self.mac, timeout=25)
        await self.client.connect()
        # 在 0x00FE 服务里定位 写/通知 特征（按 UUID 末段 f1/f2 识别，兼容 16/128 位表示）
        for svc in self.client.services:
            su = svc.uuid.lower().replace("-", "")
            if su.endswith("00fe") or su.startswith("000000fe"):
                for ch in svc.characteristics:
                    cu = ch.uuid.lower().replace("-", "")
                    if cu.endswith("00f1") or cu.startswith("000000f1"):
                        self.wr_uuid = ch.uuid
                    elif cu.endswith("00f2") or cu.startswith("000000f2"):
                        self.ntf_uuid = ch.uuid
        # 兜底：若没按服务匹配到，则全局找
        if not self.wr_uuid or not self.ntf_uuid:
            for svc in self.client.services:
                for ch in svc.characteristics:
                    cu = ch.uuid.lower().replace("-", "")
                    if cu.endswith("00f1"):
                        self.wr_uuid = self.wr_uuid or ch.uuid
                    elif cu.endswith("00f2"):
                        self.ntf_uuid = self.ntf_uuid or ch.uuid
        if not self.wr_uuid or not self.ntf_uuid:
            raise RuntimeError(f"未定位到控制特征 写={self.wr_uuid} 通知={self.ntf_uuid}")
        await self.client.start_notify(self.ntf_uuid, self._on_notify)
        return self

    async def disconnect(self):
        try:
            await self.client.disconnect()
        except Exception:
            pass

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, *a):
        try:
            await self.client.disconnect()
        except Exception:
            pass

    # ---------- 回执 ----------
    def _on_notify(self, _handle, data):
        b = bytes(data)
        self.last_frames.append(b)
        # 帧: FF SEQ LEN PAYLOAD.. AA
        if len(b) >= 5 and b[0] == 0xFF and b[-1] == 0xAA:
            seq = b[1]; ln = b[2]; payload = b[3:3 + ln]
            if payload == b"\xfe":
                return  # ACK，忽略
            fut = self.pending.pop(seq, None)
            if fut and not fut.done():
                fut.set_result(payload)

    async def send(self, payload, wait=False, timeout=2.0):
        """发一帧。wait=True 时等待对应 seq 的数据回执。"""
        seq = self._next_seq()
        frm = self.frame(seq, payload)
        fut = None
        if wait:
            fut = asyncio.get_event_loop().create_future()
            self.pending[seq] = fut
        await self.client.write_gatt_char(self.wr_uuid, frm)
        if wait:
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                self.pending.pop(seq, None)
                return None
        return None

    # ---------- 高级命令 ----------
    async def set_anc(self, mode, level=0x03):
        # mode: 0x01 降噪(带 level) / 0x02 正常 / 0x03 通透
        if mode == 0x01:
            payload = bytes([0x09, 0x01, level & 0x03])
        else:
            payload = bytes([0x09, mode, 0x00])
        await self.send(payload, wait=False)

    async def set_ldac(self, on):
        await self.send(bytes([0x4D, 0x01 if on else 0x00]), wait=False)

    async def query_anc(self):
        r = await self.send(bytes([0xFA, 0x09]), wait=True)
        if r and len(r) >= 3 and r[0] == 0x09:
            return r[1], r[2]
        return None, None

    async def query_ldac(self):
        r = await self.send(bytes([0xFA, 0x4D]), wait=True)
        if r and len(r) >= 2 and r[0] == 0x4D:
            return r[1]
        return None

    async def query_battery(self):
        r = await self.send(bytes([0xFA, 0x0C]), wait=True)
        if r and len(r) >= 2 and r[0] == 0x0C:
            return r[1]
        return None


async def do_status(dev):
    m, l = await dev.query_anc()
    await asyncio.sleep(0.15)
    ld = await dev.query_ldac()
    await asyncio.sleep(0.15)
    bt = await dev.query_battery()
    if m is not None:
        s = MODE_NAME.get(m, f"0x{m:02X}")
        if m == 0x01:
            s += f"-{LEVEL_NAME.get(l, l)}"
        print(f"  降噪模式 : {s}")
    else:
        print("  降噪模式 : (无回执)")
    print(f"  LDAC     : {'开' if ld == 1 else ('关' if ld == 0 else '(无回执)')}")
    print(f"  电量     : {bt}%" if bt is not None else "  电量     : (无回执)")


def parse_hex(s):
    s = s.strip().replace("0x", " ").replace(",", " ")
    try:
        return bytes(int(x, 16) for x in s.split())
    except ValueError:
        return None


async def main():
    ap = argparse.ArgumentParser(description="iKF-Nano BLE 控制工具（协议破解版）")
    ap.add_argument("cmd", help="status/anc/ldac/raw")
    ap.add_argument("arg", nargs="?", help="anc:off|transparency|adaptive|light|balanced|deep  ldac:on|off  raw:hex")
    ap.add_argument("--mac", default=DEFAULT_MAC)
    args = ap.parse_args()

    cmd = args.cmd.lower()
    async with IKF(args.mac) as dev:
        print(f"已连接 {args.mac}  写={dev.wr_uuid}")
        if cmd == "status":
            await do_status(dev)
        elif cmd == "anc":
            a = (args.arg or "").lower()
            if a in ANC_MODES:
                await dev.set_anc(ANC_MODES[a])
                print(f"已发送: 降噪模式 -> {a}")
            elif a in ANC_LEVELS:
                await dev.set_anc(0x01, ANC_LEVELS[a])
                print(f"已发送: 降噪-{a} ({LEVEL_NAME[ANC_LEVELS[a]]})")
            else:
                print(f"未知档位: {a}  可选 off/transparency/adaptive/light/balanced/deep")
        elif cmd == "ldac":
            a = (args.arg or "").lower()
            if a in ("on", "1", "true"):
                await dev.set_ldac(True); print("已发送: LDAC 开 (会短暂断连, 降噪重置为正常)")
            elif a in ("off", "0", "false"):
                await dev.set_ldac(False); print("已发送: LDAC 关")
            else:
                print("用法: ldac on|off")
        elif cmd == "raw":
            b = parse_hex(args.arg or "")
            if b:
                await dev.client.write_gatt_char(dev.wr_uuid, b)
                print(f"已原样发送 {len(b)}B: {b.hex(' ')}")
            else:
                print("无效 hex")
        else:
            print("未知命令")
        await asyncio.sleep(0.6)  # 留出时间收回执/观察


if __name__ == "__main__":
    asyncio.run(main())
