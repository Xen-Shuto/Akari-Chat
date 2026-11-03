"""Terminate only a helper's owned process tree through an unnamed Windows job."""
import ctypes,os
from ctypes import wintypes
class Basic(ctypes.Structure):
 _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
class IO(ctypes.Structure):_fields_=[(k,ctypes.c_ulonglong) for k in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
class Limits(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
class ProcessGroup:
 def __init__(self,process):
  self.handle=None
  if os.name!='nt':return
  api=ctypes.WinDLL('kernel32',use_last_error=True);self.api=api
  api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];api.CreateJobObjectW.restype=wintypes.HANDLE
  api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];api.SetInformationJobObject.restype=wintypes.BOOL
  api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];api.AssignProcessToJobObject.restype=wintypes.BOOL
  api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
  handle=api.CreateJobObjectW(None,None);limits=Limits();limits.basic.flags=0x2000
  if not handle or not api.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(handle,wintypes.HANDLE(int(process._handle))):
   if handle:api.CloseHandle(handle)
   process.kill();process.wait();raise ValueError('専用ワーカーのプロセス管理を開始できません。')
  self.handle=handle
 def close(self):
  if self.handle:self.api.CloseHandle(self.handle);self.handle=None
