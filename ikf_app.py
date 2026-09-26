# -*- coding: utf-8 -*-
"""
iKF-Nano 电脑端控制软件（图形版）
功能：
  1. 控制降噪档位 / 通透 / LDAC / 查询状态
  2. 记忆上次设置：像手机 App 一样，连接成功后自动恢复上次的降噪档位与 LDAC
  3. 开机自启：注册到 Windows 启动项，开机静默运行并自动连接耳机
  4. 后台驻留：关闭窗口最小化到系统托盘，可随时恢复
协议：FF <SEQ> <LEN> <CMD+载荷> AA，写特征 0x00F1 / 通知 0x00F2（见 ikf_control.py）
依赖：bleak, pystray, pillow（pip install bleak pystray pillow）
用法：
  python ikf_app.py                 # 正常启动（打开窗口）
  python ikf_app.py --autostart     # 开机静默启动（自动连接并最小化到托盘）
"""
import sys, os, json, threading, queue, asyncio, time
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from ikf_control import IKF, ANC_MODES, ANC_LEVELS, MODE_NAME, LEVEL_NAME

APP_NAME = r"IKFControlApp"

def resource_path(name):
    """打包后资源在 _MEIPASS 临时目录，脚本运行则在源码目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

# 配置文件：打包后放 exe 同目录，源码运行放源码目录（与 exe 分开）——
# 但两者各自独立保存，避免手动用 exe、自启却走源码导致配置分家。
# 解决方式：自启统一指向 exe，保证都用同一份 exe 配置。
if getattr(sys, "frozen", False):
    CONFIG_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "ikf_app_config.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_MAC = "B0:F0:0C:90:F0:73"

# 档位 -> (模式字, 档位字, 显示名)
LEVEL_ORDER = ["adaptive", "light", "balanced", "deep"]
LEVEL_LABEL = {"adaptive": "自适应", "light": "轻度", "balanced": "均匀(中度)", "deep": "重度"}
ANCHOR = {}

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "anc", "level": "deep", "ldac": False}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class IKFOps(threading.Thread):
    """后台线程：持有 asyncio 事件循环与耳机连接，执行控制/查询，结果经 queue 回报主线程。"""
    def __init__(self, mac, q):
        super().__init__(daemon=True)
        self.mac = mac
        self.q = q            # 主线程 queue
        self.loop = None
        self.busy = threading.Lock()
        self._client = None
        self.wr_uuid = None
        self.connected = False
        self._connecting = False

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ---- 提交协程到后台 loop ----
    def submit(self, coro, done_name=None):
        if not self.loop:
            return None
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut

    # ---- 连接 ----
    def connect(self):
        with self.busy:
            return self.submit(self._connect_coro())

    async def _connect_coro(self):
        self._connecting = True
        try:
            if self._client is not None:
                try: await self._client.disconnect()
                except Exception: pass
            c = IKF(self.mac)
            await c.connect()
            self._client = c
            self.wr_uuid = c.wr_uuid
            self.connected = True
            self.q.put(("state", True))
            self.q.put(("log", f"已连接 {self.mac}  写={c.wr_uuid}"))
            # 连接后查询一次当前状态
            await asyncio.sleep(0.3)
            await self._query_status()
        except Exception as e:
            self.connected = False
            self.q.put(("state", False))
            self.q.put(("log", f"连接失败: {type(e).__name__}: {e}"))
        finally:
            self._connecting = False

    def alive(self):
        """查询底层蓝牙连接是否真实存在（区别于本程序内部 connected 标志）。"""
        c = self._client
        if c is None:
            return False
        try:
            return bool(c.client.is_connected)
        except Exception:
            return False

    def disconnect(self):
        with self.busy:
            return self.submit(self._disconnect_coro())

    async def _disconnect_coro(self):
        try:
            if self._client is not None:
                await self._client.disconnect()
        except Exception:
            pass
        self._client = None
        self.connected = False
        self.q.put(("state", False))
        self.q.put(("log", "已断开"))

    # ---- 指令 ----
    def set_anc(self, mode, level=None):
        with self.busy:
            return self.submit(self._set_anc_coro(mode, level))

    async def _set_anc_coro(self, mode, level):
        c = self._client
        if c is None:
            self.q.put(("log", "未连接，无法发送"))
            return
        await c.set_anc(mode, level if level is not None else 0x03)
        self.q.put(("log", f"已发送降噪指令: 模式=0x{mode:02X} 档=0x{level or 0:02X}"))
        await asyncio.sleep(0.4)
        await self._query_status()

    def set_ldac(self, on):
        with self.busy:
            return self.submit(self._set_ldac_coro(on))

    async def _set_ldac_coro(self, on):
        c = self._client
        if c is None:
            self.q.put(("log", "未连接，无法发送"))
            return
        await c.set_ldac(on)
        self.q.put(("log", f"已发送 LDAC: {'开' if on else '关'} (开/关会短暂断连)"))
        await asyncio.sleep(0.5)
        await self._query_status()

    def query(self):
        with self.busy:
            return self.submit(self._query_status())

    def query_silent(self):
        """周期轮询：只刷新 UI，不刷日志、不中断正在执行的命令。"""
        if self.busy.locked():
            return None
        with self.busy:
            return self.submit(self._query_status(silent=True))

    async def _query_status(self, silent=False):
        """读取真实状态并回报。silent=True 时只刷新 UI 不刷日志（供周期轮询用）。"""
        c = self._client
        if c is None:
            return None
        try:
            m, l = await c.query_anc()
            await asyncio.sleep(0.15)
            ld = await c.query_ldac()
            await asyncio.sleep(0.15)
            bt = await c.query_battery()
            self.q.put(("status", (m, l, ld, bt)))
            if not silent:
                mode_txt = MODE_NAME.get(m, f"0x{m:02X}") if m is not None else "?"
                level_txt = LEVEL_NAME.get(l, l) if l is not None else ""
                ld_txt = "开" if ld == 1 else ("关" if ld == 0 else "?")
                bt_txt = f"{bt}%" if bt is not None else "?"
                self.q.put(("log", f"状态: {mode_txt}{'-'+str(level_txt) if m==1 else ''}  LDAC={ld_txt}  电量={bt_txt}"))
        except Exception as e:
            if not silent:
                self.q.put(("log", f"查询失败: {type(e).__name__}: {e}"))


class IKFPanel:
    def __init__(self, root, autostart=False):
        self.root = root
        root.title("iKF-Nano 控制")
        root.geometry("440x620")
        root.resizable(False, False)
        try:
            root.iconbitmap(resource_path("ikf_icon.ico"))
        except Exception:
            pass

        self.cfg = load_config()
        self._mem_applied = False   # 标记是否已应用记忆，防止 LDAC 重连后重复触发
        self.q = queue.Queue()
        self.ops = IKFOps(DEFAULT_MAC, self.q)
        self.ops.start()
        self.tray_icon = None
        self.want_apply = True   # 是否在连接后自动应用记忆设置
        self._last_reconn = time.monotonic()  # 上次自动重连时间戳（防抖，初始=启动时刻）

        self._build_ui()
        self._sync_apply_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queue()

        if autostart:
            # 自启动：后台运行，窗口最小化到托盘
            self.root.after(800, self._hide_to_tray)
        self.root.after(500, self.try_connect)
        self.root.after(1000, self._watchdog)

    # ---------- 界面 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        row = ttk.Frame(self.root); row.pack(fill="x", **pad)
        self.lbl_state = ttk.Label(row, text="● 未连接", font=("Microsoft YaHei UI", 10))
        self.lbl_state.pack(side="left")
        ttk.Button(row, text="重新连接", command=self.try_connect).pack(side="right")

        # 记忆 + 自启
        box = ttk.LabelFrame(self.root, text="设置记忆与自启动", padding=8)
        box.pack(fill="x", **pad)
        self.var_apply = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="连接后自动恢复上次的降噪/LDAC 设置（像手机记忆档位）",
                        variable=self.var_apply, command=self._sync_apply_controls).pack(anchor="w")
        self.var_auto = tk.BooleanVar(value=self._is_autostart())
        ttk.Checkbutton(box, text="开机自启动（后台运行，自动连接耳机）",
                        variable=self.var_auto, command=self._on_toggle_autostart).pack(anchor="w")

        # 降噪模式
        mode = ttk.LabelFrame(self.root, text="降噪模式", padding=8)
        mode.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value="anc")
        mrow = ttk.Frame(mode); mrow.pack(fill="x")
        self._mk_mode_btn(mrow, "正常", "off")
        self._mk_mode_btn(mrow, "通透", "transparency")
        self._mk_mode_btn(mrow, "降噪", "anc")

        # 降噪档位（仅降噪下有效）
        lv = ttk.Frame(self.root); lv.pack(fill="x", **pad)
        ttk.Label(lv, text="降噪强度:").pack(side="left")
        self.level_var = tk.StringVar(value="deep")
        for key in LEVEL_ORDER:
            ttk.Radiobutton(lv, text=LEVEL_LABEL[key], value=key,
                            variable=self.level_var, command=self._on_level).pack(side="left", padx=2)

        # LDAC
        lrow = ttk.Frame(self.root); lrow.pack(fill="x", **pad)
        ttk.Label(lrow, text="LDAC:").pack(side="left")
        self.ldac_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(lrow, text="开启 LDAC 高音质", variable=self.ldac_var,
                        command=self._on_ldac_toggle).pack(side="left")
        ttk.Label(lrow, text=" (切换会短暂断连，之后需恢复降噪)",
                  foreground="gray").pack(side="left")

        # 状态
        st = ttk.LabelFrame(self.root, text="当前状态", padding=8)
        st.pack(fill="both", expand=True, **pad)
        self.var_status = tk.StringVar(value="暂无")
        ttk.Label(st, textvariable=self.var_status, font=("Consolas", 10),
                  justify="left").pack(anchor="w")

        # 日志
        ttk.Label(self.root, text="日志").pack(anchor="w", padx=10)
        self.log = scrolledtext.ScrolledText(self.root, height=6, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        if self.ldac_var.get() != bool(self.cfg.get("ldac", False)):
            self.ldac_var.set(bool(self.cfg.get("ldac", False)))

    def _mk_mode_btn(self, parent, text, mode_key):
        b = ttk.Radiobutton(parent, text=text, value=mode_key,
                            variable=self.mode_var, command=self._on_mode)
        b.pack(side="left", padx=2)
        ANCHOR[mode_key] = b

    # ---------- 交互 ----------
    def _on_mode(self):
        key = self.mode_var.get()
        if key == "anc":
            self._apply_anc(self.level_var.get())
        elif key == "off":
            self._apply_anc("off")
        elif key == "transparency":
            self._apply_anc("transparency")

    def _on_level(self):
        if self.mode_var.get() == "anc":
            self._apply_anc(self.level_var.get())

    def _apply_anc(self, spec):
        """spec: 'off'/'transparency'/'adaptive'/'light'/'balanced'/'deep'"""
        if spec in ANC_MODES:      # off / transparency
            mode = ANC_MODES[spec]
            name = "正常" if mode == 0x02 else "通透"
            self.cfg.update({"mode": spec, "name": name})
            self.ops.set_anc(mode)
            self.q.put(("log", f"切换降噪模式 -> {name}"))
        else:                      # 降噪任意档
            level = ANC_LEVELS.get(spec, 0x03)
            self.cfg.update({"mode": "anc", "level": spec,
                             "name": "降噪-" + LEVEL_LABEL[spec]})
            self.ops.set_anc(0x01, level)
            self.q.put(("log", f"切换降噪 -> {LEVEL_LABEL[spec]}"))
        save_config(self.cfg)

    def _on_ldac_toggle(self):
        on = self.ldac_var.get()
        self.cfg["ldac"] = on
        save_config(self.cfg)
        self.ops.set_ldac(on)

    def _sync_apply_controls(self):
        self.want_apply = self.var_apply.get()

    # ---------- 连接 ----------
    def try_connect(self):
        self._log(">> 尝试连接 ...")
        self.ops.connect()

    def _watchdog(self):
        """每 3 秒：周期刷新真实状态；检测断连并自动重连（带防抖）。
        以耳机真实连接为准，避免“已断开仍显示已连接、状态停滞”的问题。"""
        try:
            if self.ops.connected:
                # 连着的状态下周期静默拉取真实状态，UI 始终反映耳机实际
                self.ops.query_silent()
            if not self.ops.alive() and not self.ops._connecting:
                # 蓝牙已断开（或从未连上），且没有正在进行的连接
                if self.ops.connected:
                    self.ops.connected = False
                    self.lbl_state.config(text="● 未连接", foreground="gray")
                    self._log("检测到蓝牙连接已断开")
                now = time.monotonic()
                if now - self._last_reconn > 5:
                    self._last_reconn = now
                    self._log(">> 自动重连 ...")
                    self.ops.connect()
        except Exception as e:
            self._log(f"守护异常: {type(e).__name__}: {e}")
        self.root.after(3000, self._watchdog)

    # ---------- 记忆应用 ----------
    def _mem_anc_spec(self):
        """把记忆配置转成降噪命令参数。返回 (mode, level)。"""
        mode = self.cfg.get("mode", "anc")
        if mode == "off":
            return (0x02, None)
        if mode == "transparency":
            return (0x03, None)
        return (0x01, ANC_LEVELS.get(self.cfg.get("level", "deep"), 0x03))

    def _send_mem_anc(self):
        mode, level = self._mem_anc_spec()
        self.ops.set_anc(mode, level)
        name = MODE_NAME.get(mode, "?")
        if mode == 0x01:
            name += "-" + LEVEL_NAME.get(level, "")
        self.q.put(("log", f"恢复降噪: {name}"))

    def _apply_memory(self):
        """连接成功且勾选记忆时恢复上次设置。
        若记忆 LDAC 为开：先开 LDAC（会断连并重置降噪），
        交给 watchdog 检测断连并自动重连，重连成功后补设降噪，最终状态 = 记忆值。"""
        # 如果是 LDAC 重连后的二次连接，只补降噪即可，回到常规
        if self._mem_applied:
            self._mem_applied = False
            self._send_mem_anc()
            return
        if not self.want_apply:
            self._log("连接成功（已关闭自动记忆，保持当前设置）")
            return
        ld = self.cfg.get("ldac", False)
        if ld:
            self._mem_applied = True
            self._log(">> 恢复记忆: 先开启 LDAC（会断连，watchdog 会自动重连后补设降噪）...")
            self.ops.set_ldac(True)
            # 兜底：若 6 秒后仍没被二次连接消费（如 LDAC 未断连），直接补一次降噪
            self.root.after(6000, self._finish_mem_ldac)
        else:
            self._mem_applied = False
            self._log(">> 恢复记忆: 应用降噪档位")
            self._send_mem_anc()

    def _finish_mem_ldac(self):
        """LDAC 已开的兜底：若重连流程未触发补降噪，且当前已连上，则直接补设一次。"""
        if self._mem_applied:
            if self.ops.alive():
                self._mem_applied = False
                self._send_mem_anc()
            # 若仍未连上，watchdog 会继续重连，届时 _apply_memory 消费 _mem_applied

    # ---------- 自启动 ----------
    def _is_autostart(self):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
            v, _ = winreg.QueryValueEx(k, APP_NAME)
            winreg.CloseKey(k)
            return True
        except Exception:
            return False

    def _on_toggle_autostart(self):
        if self.var_auto.get():
            self._enable_autostart()
        else:
            self._disable_autostart()

    def _enable_autostart(self):
        # 打包成 exe 后直接指向 exe；脚本运行则用 pythonw 静默启动
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --autostart'
        else:
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            script = os.path.abspath(__file__)
            cmd = f'"{pyw}" "{script}" --autostart'
        try:
            import winreg
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(k)
            self.var_auto.set(True)
            self._log("已开启开机自启（静默后台运行）")
        except Exception as e:
            self.var_auto.set(False)
            messagebox.showerror("自启失败", str(e))

    def _disable_autostart(self):
        try:
            import winreg
            winreg.DeleteValue(winreg.HKEY_CURRENT_USER, RUN_KEY, APP_NAME)
            self.var_auto.set(False)
            self._log("已关闭开机自启")
        except Exception:
            self.var_auto.set(False)
            self._log("取消自启（未发现记录）")

    # ---------- 托盘驻留 ----------
    def _on_close(self):
        # 关闭按钮 -> 隐藏到托盘（后台运行）
        self._hide_to_tray()

    def _hide_to_tray(self):
        self.root.withdraw()
        self._ensure_tray()

    def _ensure_tray(self):
        if self.tray_icon:
            return
        try:
            import pystray
            from PIL import Image
            img = Image.open(resource_path("ikf_icon.ico")).convert("RGB")
            img = img.resize((64, 64), Image.LANCZOS)
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._show_window, default=True),
                pystray.MenuItem("连接", self.try_connect),
                pystray.MenuItem("退出", self._quit),
            )
            self.tray_icon = pystray.Icon(APP_NAME, img, "iKF-Nano 控制", menu)
            self.tray_icon.run_detached()
            self._log("已后台驻留到托盘（点击托盘图标可恢复）")
        except Exception as e:
            self._log(f"托盘初始化失败: {e}")

    def _show_window(self, icon=None, item=None):
        self.root.after(0, self._show_window_ui)
    def _show_window_ui(self):
        self.root.deiconify()
        self._log("窗口已恢复")

    def _quit(self, icon=None, item=None):
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.root.after(0, self.root.destroy)

    # ---------- 消息泵 ----------
    def _log(self, msg):
        self.q.put(("log", msg))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "state":
                    conn = payload
                    self.lbl_state.config(text="● 已连接" if conn else "● 未连接",
                                          foreground="green" if conn else "gray")
                    if conn:
                        self._apply_memory()
                elif kind == "status":
                    m, l, ld, bt = payload
                    self._render_status(m, l, ld, bt)
                elif kind == "pending_ldac":
                    # 降噪恢复完成后再补 LDAC，保证最终降噪=记忆值
                    if self.cfg.get("ldac", False):
                        self.ops.set_ldac(True)
                elif kind == "apply_ui":
                    pass
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _render_status(self, m, l, ld, bt):
        # 同步界面勾选到耳机真实状态（程序 set 不会触发 command，安全）
        if m == 1:
            s = f"降噪模式 : 降噪-{LEVEL_NAME.get(l, l)}"
            self.mode_var.set("anc")
            ivl = {0: "adaptive", 1: "light", 2: "balanced", 3: "deep"}
            if l in ivl:
                self.level_var.set(ivl[l])
        elif m == 2:
            s = "降噪模式 : 正常 (降噪关闭)"
            self.mode_var.set("off")
        elif m == 3:
            s = "降噪模式 : 通透"
            self.mode_var.set("transparency")
        else:
            s = f"降噪模式 : 0x{m:02X}"
        if ld is not None:
            self.ldac_var.set(ld == 1)
        s += f"\nLDAC     : {'开' if ld == 1 else ('关' if ld == 0 else '?')}"
        s += f"\n电量     : {bt if bt is not None else '?'} %"
        self.var_status.set(s)
        # 把真实状态写回记忆配置，保证“记忆”始终与耳机实际一致（重启后能正确恢复）
        self._sync_cfg_to_real(m, l, ld)

    def _sync_cfg_to_real(self, m, l, ld):
        ivl = {0: "adaptive", 1: "light", 2: "balanced", 3: "deep"}
        if m == 1:
            mode, level = "anc", ivl.get(l, self.cfg.get("level", "deep"))
        elif m == 2:
            mode, level = "off", self.cfg.get("level", "deep")
        elif m == 3:
            mode, level = "transparency", self.cfg.get("level", "deep")
        else:
            return
        changed = False
        if mode != self.cfg.get("mode"):
            self.cfg["mode"] = mode; changed = True
        if m == 1 and level != self.cfg.get("level"):
            self.cfg["level"] = level; changed = True
        if ld is not None and bool(ld) != bool(self.cfg.get("ldac", False)):
            self.cfg["ldac"] = bool(ld); changed = True
        if changed:
            save_config(self.cfg)

    def _append_log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


def main():
    autostart = "--autostart" in sys.argv
    root = tk.Tk()
    IKFPanel(root, autostart=autostart)
    root.mainloop()

if __name__ == "__main__":
    main()