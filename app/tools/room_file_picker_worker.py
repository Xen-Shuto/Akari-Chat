"""Windows native open-file dialog in a console-free, disposable process."""
import ctypes,json,os
from ctypes import wintypes as w

def pick():
 if os.name!='nt':raise ValueError('Windowsのファイル選択に対応しています。パスを直接入力してください。')
 class OpenFileName(ctypes.Structure):
  _fields_=[('lStructSize',w.DWORD),('hwndOwner',w.HWND),('hInstance',w.HINSTANCE),('lpstrFilter',w.LPCWSTR),('lpstrCustomFilter',w.LPWSTR),('nMaxCustFilter',w.DWORD),('nFilterIndex',w.DWORD),('lpstrFile',w.LPWSTR),('nMaxFile',w.DWORD),('lpstrFileTitle',w.LPWSTR),('nMaxFileTitle',w.DWORD),('lpstrInitialDir',w.LPCWSTR),('lpstrTitle',w.LPCWSTR),('Flags',w.DWORD),('nFileOffset',w.WORD),('nFileExtension',w.WORD),('lpstrDefExt',w.LPCWSTR),('lCustData',w.LPARAM),('lpfnHook',ctypes.c_void_p),('lpTemplateName',w.LPCWSTR),('pvReserved',ctypes.c_void_p),('dwReserved',w.DWORD),('FlagsEx',w.DWORD)]
 buffer=ctypes.create_unicode_buffer(32768);buffer.value='room_config.json';dialog=OpenFileName();dialog.lStructSize=ctypes.sizeof(dialog)
 dialog.lpstrFile=ctypes.cast(buffer,w.LPWSTR);dialog.nMaxFile=len(buffer);dialog.lpstrFilter='room_config.json\0room_config.json\0JSONファイル (*.json)\0*.json\0\0';dialog.nFilterIndex=1
 dialog.lpstrTitle='取り込むキャラクターのroom_config.jsonを選択';dialog.Flags=0x00080000|0x00001000|0x00000800|0x00000008
 api=ctypes.WinDLL('comdlg32',use_last_error=True);api.GetOpenFileNameW.argtypes=[ctypes.POINTER(OpenFileName)];api.GetOpenFileNameW.restype=w.BOOL;api.CommDlgExtendedError.restype=w.DWORD
 if api.GetOpenFileNameW(ctypes.byref(dialog)):return buffer.value
 if api.CommDlgExtendedError():raise ValueError('ファイル選択を開けませんでした。パスを直接入力してください。')
 return None

if __name__=='__main__':
 try:result={'path':pick()}
 except Exception:result={'error':'ファイル選択を完了できませんでした。パスを直接入力してください。'}
 print(json.dumps(result,ensure_ascii=True),flush=True)
