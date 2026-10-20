# -*- coding: utf-8 -*-
"""
Windows 音频输出设备工具（纯 ctypes + winreg 实现，不依赖任何第三方库）

为什么需要这个模块：
    iKF-Nano 控制软件只负责 BLE 控制通道（降噪/LDAC/电量），
    而放歌走的是经典蓝牙 A2DP，由 Windows 自己决定声音从哪个设备出。
    切换 LDAC 会让整条蓝牙链路断开重连，链路一断，Windows 眼里那个音频
    端点就"消失"了，默认输出会回落到扬声器；重连后 Windows 并不保证把
    默认输出自动切回耳机，于是就出现"耳机连着但声音从扬声器出来"。
    本模块提供一键把默认播放设备切回耳机的能力。

对外接口：
    list_render_endpoints()        列出所有播放设备及其真实状态（读注册表）
    find_headset_endpoint(kw)      找到处于活动状态的耳机播放端点（优先 A2DP）
    get_default_output()           读当前默认播放设备（IMMDeviceEnumerator）
    set_default_output(id)         把默认播放设备切到指定端点（IPolicyConfig）
    switch_to_headset(kw)          一键切回耳机，返回 (是否成功, 说明文字)

自测：
    python win_audio.py            列出设备、默认输出，并尝试切回耳机
"""
import ctypes
import winreg

# ----------------------------------------------------------------------------
# 注册表位置与属性键
# ----------------------------------------------------------------------------
RENDER_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
PKEY_FRIENDLY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"   # 设备名，如「耳机」
PKEY_DEVICE_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"     # 设备描述，如「iKF-Nano空间音频版」

# DeviceState 取值含义（注册表 DeviceState 字段）
STATE_NAME = {1: "活动", 2: "已禁用", 4: "不存在", 8: "未插入"}

# ----------------------------------------------------------------------------
# COM 常量
# ----------------------------------------------------------------------------
CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
CLSID_IPolicyConfig = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
IID_IPolicyConfig = "{F8679F50-850A-41CF-9C72-430F290290C8}"

CLSCTX_ALL = 23
E_RENDER = 0                       # 数据流方向：播放
E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS = 0, 1, 2   # 三种角色的默认设备都要设

_ole32 = ctypes.windll.ole32
_com_ready = False


class _GUID(ctypes.Structure):
    """Win32 GUID 结构体"""
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    """把 '{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}' 字符串转成 _GUID 结构"""
    s = text.strip().strip("{}")
    d1, d2, d3, rest = s.split("-")[0], s.split("-")[1], s.split("-")[2], s.split("-")[3] + s.split("-")[4]
    g = _GUID()
    g.Data1 = int(d1, 16)
    g.Data2 = int(d2, 16)
    g.Data3 = int(d3, 16)
    for i in range(8):
        g.Data4[i] = int(rest[i * 2:i * 2 + 2], 16)
    return g


def _ensure_com():
    """确保当前线程已初始化 COM（STA 模式，第一次调用时执行）"""
    global _com_ready
    if not _com_ready:
        try:
            _ole32.CoInitializeEx(None, 2)   # 2 = COINIT_APARTMENTTHREADED
        except Exception:
            pass
        _com_ready = True


def _create(clsid, iid):
    """CoCreateInstance 创建 COM 对象，返回接口指针 c_void_p"""
    _ensure_com()
    p = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(_guid(clsid)), None, CLSCTX_ALL,
                                 ctypes.byref(_guid(iid)), ctypes.byref(p))
    if hr != 0 or not p:
        raise OSError("CoCreateInstance 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
    return p


def _vtbl(p):
    """取 COM 接口的虚表（接口指针指向虚表指针）"""
    return ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))


def _release(p):
    """调用虚表第 2 号槽（Release）释放 COM 对象"""
    try:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vtbl(p)[2])(p)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 读注册表：列出播放端点
# ----------------------------------------------------------------------------
def list_render_endpoints():
    """列出所有播放（输出）端点。

    返回 dict 列表，每项含：guid / id(CoreAudio 端点 ID) / state / name / desc
    state==1 表示该端点当前处于活动状态（即设备真的连着）。
    """
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, RENDER_ROOT) as root:
            count = winreg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    guid = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, guid) as k:
                        state = winreg.QueryValueEx(k, "DeviceState")[0]
                        name = desc = ""
                        try:
                            with winreg.OpenKey(k, "Properties") as pk:
                                for key, target in ((PKEY_FRIENDLY_NAME, "name"), (PKEY_DEVICE_DESC, "desc")):
                                    try:
                                        v = winreg.QueryValueEx(pk, key)[0]
                                    except OSError:
                                        v = ""
                                    if target == "name":
                                        name = v
                                    else:
                                        desc = v
                        except OSError:
                            pass
                        # CoreAudio 端点 ID 的固定格式：{0.0.0.00000000}.{端点GUID}
                        out.append({"guid": guid, "id": "{0.0.0.00000000}." + guid,
                                    "state": state, "name": name, "desc": desc})
                except OSError:
                    continue
    except OSError:
        pass
    return out


def find_headset_endpoint(keyword="iKF"):
    """在"活动"的播放端点里找耳机，优先 A2DP 立体声端点。

    排除结尾带 Hands-Free 的免手持（通话）端点 —— 那个是麦克风通话链路，
    音质差，不是用来听歌的。
    """
    kw = keyword.lower()
    active = [e for e in list_render_endpoints()
              if e["state"] == 1 and kw in (e["desc"] + e["name"]).lower()]
    if not active:
        return None
    a2dp = [e for e in active if "hands-free" not in e["desc"].lower()]
    return (a2dp or active)[0]


# ----------------------------------------------------------------------------
# COM：读默认播放设备 / 设置默认播放设备
# ----------------------------------------------------------------------------
def get_default_output_id():
    """读当前默认播放设备的 CoreAudio 端点 ID，失败返回 None。

    走 IMMDeviceEnumerator::GetDefaultAudioEndpoint（虚表第 4 号槽）。
    """
    try:
        p = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    except Exception:
        return None
    dev = ctypes.c_void_p()
    try:
        vt = _vtbl(p)
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(vt[4])
        if fn(p, E_RENDER, E_CONSOLE, ctypes.byref(dev)) != 0 or not dev:
            return None
        # IMMDevice::GetId（虚表第 5 号槽），返回 CoTaskMem 分配的字符串
        fn_get = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_wchar_p))(_vtbl(dev)[5])
        sid = ctypes.c_wchar_p()
        if fn_get(dev, ctypes.byref(sid)) != 0:
            return None
        value = sid.value
        _ole32.CoTaskMemFree(ctypes.cast(sid, ctypes.c_void_p))  # 必须释放，否则轮询会漏内存
        return value
    except Exception:
        return None
    finally:
        if dev:
            _release(dev)
        _release(p)


def get_default_output():
    """返回当前默认播放设备的端点信息（dict），失败返回 None。"""
    did = get_default_output_id()
    if not did:
        return None
    guid = did.split(".")[-1]          # {0.0.0.00000000}.{GUID} 取末段 GUID
    for e in list_render_endpoints():
        if e["guid"].lower() == guid.lower():
            e = dict(e)
            e["id"] = did
            return e
    return {"guid": guid, "id": did, "state": 1, "name": "", "desc": ""}


def set_default_output(endpoint_id):
    """把默认播放设备切到指定端点（三种角色都设），成功返回 True。

    走 IPolicyConfig::SetDefaultEndpoint（虚表第 13 号槽）。
    该接口是 Windows 内部接口，未公开但长期可用；失败时返回 False 不抛异常。
    """
    if not endpoint_id:
        return False
    try:
        p = _create(CLSID_IPolicyConfig, IID_IPolicyConfig)
    except Exception:
        return False
    try:
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_wchar_p,
                                ctypes.c_int)(_vtbl(p)[13])
        ok = True
        for role in (E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS):
            if fn(p, endpoint_id, role) != 0:
                ok = False
        return ok
    except Exception:
        return False
    finally:
        _release(p)


# ----------------------------------------------------------------------------
# 便于界面调用的一键切换
# ----------------------------------------------------------------------------
def switch_to_headset(keyword="iKF"):
    """一键把默认播放设备切回耳机。返回 (是否成功, 说明文字)"""
    ep = find_headset_endpoint(keyword)
    if not ep:
        return False, f"没找到处于活动状态的耳机播放设备（关键字：{keyword}），请确认耳机已连接"
    if not set_default_output(ep["id"]):
        return False, "切换默认播放设备失败（系统策略接口未生效），请在「设置→系统→声音」里手动选择耳机"
    return True, f"已把系统默认播放设备切到「{ep['desc'] or ep['name']}」"


def is_headset_default(keyword="iKF"):
    """当前默认播放设备是不是耳机"""
    cur = get_default_output()
    ep = find_headset_endpoint(keyword)
    if not cur or not ep:
        return False
    return cur["guid"].lower() == ep["guid"].lower()


if __name__ == "__main__":
    print("=== 全部播放端点 ===")
    for e in list_render_endpoints():
        print(f"  [{STATE_NAME.get(e['state'], hex(e['state'])):>4}] {e['name']}  |  {e['desc']}  |  {e['guid']}")

    cur = get_default_output()
    print("\n=== 当前默认播放设备 ===")
    print(f"  {cur['name']}  |  {cur['desc']}  |  {cur['guid']}" if cur else "  (读取失败)")

    ep = find_headset_endpoint("iKF")
    print("\n=== 耳机端点 ===")
    print(f"  {ep['desc']}  |  {ep['guid']}" if ep else "  (未找到活动的耳机端点)")

    print("\n=== 试切到耳机 ===")
    ok, msg = switch_to_headset("iKF")
    print(f"  ok={ok}  {msg}")
