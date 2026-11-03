"""Optional transient tray balloon, no OS settings, no shell commands, no text interpolation."""
import ctypes
import os
import threading
from ctypes import wintypes

class Notice(ctypes.Structure):
 _fields_=[('cbSize',wintypes.DWORD),('hWnd',wintypes.HWND),('uID',wintypes.UINT),('uFlags',wintypes.UINT),('uCallbackMessage',wintypes.UINT),('hIcon',wintypes.HANDLE),('szTip',wintypes.WCHAR*128),('dwState',wintypes.DWORD),('dwStateMask',wintypes.DWORD),('szInfo',wintypes.WCHAR*256),('uTimeout',wintypes.UINT),('szInfoTitle',wintypes.WCHAR*64),('dwInfoFlags',wintypes.DWORD),('guidItem',ctypes.c_ubyte*16),('hBalloonIcon',wintypes.HANDLE)]

def show_notice(title,message):
 if os.name!='nt':return False
 user=ctypes.WinDLL('user32',use_last_error=True);shell=ctypes.WinDLL('shell32',use_last_error=True)
 user.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HANDLE,wintypes.HINSTANCE,wintypes.LPVOID];user.CreateWindowExW.restype=wintypes.HWND
 user.DestroyWindow.argtypes=[wintypes.HWND];user.DestroyWindow.restype=wintypes.BOOL
 shell.Shell_NotifyIconW.argtypes=[wintypes.DWORD,ctypes.POINTER(Notice)];shell.Shell_NotifyIconW.restype=wintypes.BOOL
 # Use a worker-owned invisible static window; cleanup on the same owning thread.
 result=[];ready=threading.Event()
 def work():
  hwnd=user.CreateWindowExW(0,'STATIC','Akari notification',0,0,0,0,0,None,None,None,None)
  data=Notice();data.cbSize=ctypes.sizeof(data);data.hWnd=hwnd;data.uID=1;data.uFlags=0x10;data.szInfo=message[:255];data.szInfoTitle=title[:63];data.dwInfoFlags=1;data.uTimeout=10000
  ok=bool(hwnd and shell.Shell_NotifyIconW(0,ctypes.byref(data)));result.append(ok);ready.set()
  try:
   if ok:threading.Event().wait(12)
  finally:
   if ok:shell.Shell_NotifyIconW(2,ctypes.byref(data))
   if hwnd:user.DestroyWindow(hwnd)
 thread=threading.Thread(target=work,daemon=True,name='akari-notification');thread.start();ready.wait(2)
 return bool(result and result[0])
