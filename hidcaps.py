"""Print HID report sizes per interface (Windows). pip install hidapi"""
import ctypes
from ctypes import wintypes
import hid

VID, PID = 0x0C45, 0x80B1

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hidd = ctypes.WinDLL("hid", use_last_error=True)

k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
hidd.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hidd.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hidd.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
hidd.HidP_GetCaps.restype = ctypes.c_long


class CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17)] + [
        (n, ctypes.c_ushort) for n in (
            "NumberLinkCollectionNodes", "NumberInputButtonCaps",
            "NumberInputValueCaps", "NumberInputDataIndices",
            "NumberOutputButtonCaps", "NumberOutputValueCaps",
            "NumberOutputDataIndices", "NumberFeatureButtonCaps",
            "NumberFeatureValueCaps", "NumberFeatureDataIndices")]


INVALID = ctypes.c_void_p(-1).value

for d in hid.enumerate(VID, PID):
    if d["usage_page"] < 0xFF00:
        continue
    path = d["path"].decode()
    h = k32.CreateFileW(path, 0, 3, None, 3, 0, None)  # query access, share r/w
    if h is None or h == INVALID:
        print(f"{path}: open failed ({ctypes.get_last_error()})")
        continue
    pp = ctypes.c_void_p()
    if hidd.HidD_GetPreparsedData(h, ctypes.byref(pp)):
        caps = CAPS()
        hidd.HidP_GetCaps(pp, ctypes.byref(caps))
        print(f"iface {d['interface_number']}  page {caps.UsagePage:#06x}  "
              f"IN={caps.InputReportByteLength}  OUT={caps.OutputReportByteLength}  "
              f"FEATURE={caps.FeatureReportByteLength}  "
              f"(in value caps={caps.NumberInputValueCaps}, "
              f"in button caps={caps.NumberInputButtonCaps})")
        hidd.HidD_FreePreparsedData(pp)
    k32.CloseHandle(h)
