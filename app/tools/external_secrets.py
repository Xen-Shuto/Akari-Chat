"""Current-user Windows DPAPI; secrets are never exposed through configuration reads."""
import base64
import ctypes
import os
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def _crypt(raw,protect):
    if os.name!='nt':raise ValueError('外部連携の認証情報保存はWindows DPAPIを必要とします。')
    buf=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):raise ValueError('認証情報の保護/復号に失敗しました。保存時のWindowsユーザーを確認してください。')
    try:return ctypes.string_at(out.data,out.size)
    finally:kernel.LocalFree(out.data)

def protect(value):return base64.b64encode(_crypt(value.encode('utf-8'),True)).decode('ascii')
def unprotect(value):return _crypt(base64.b64decode(value),False).decode('utf-8') if value else ''
