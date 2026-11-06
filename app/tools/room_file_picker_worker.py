"""Windows native open-file dialog in a console-free, disposable process."""
import ctypes,json,os,sys
from uuid import UUID
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

def guid(value):
    return (ctypes.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)


def com_method(interface, slot, result, *arguments):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(vtable[slot])


def check_hresult(result):
    if result < 0:
        raise OSError(f'Windows file dialog HRESULT 0x{result & 0xffffffff:08x}')


def pick_avatar(initial_folder):
    """SetFolder pins the starting folder even when the shell remembers another one."""
    if os.name != 'nt':
        raise ValueError('Windowsのファイル選択に対応しています。')
    pointer = ctypes.c_void_p
    hresult = ctypes.c_long
    ole = ctypes.WinDLL('ole32')
    shell = ctypes.WinDLL('shell32')
    ole.CoInitializeEx.argtypes = [pointer, w.DWORD]
    ole.CoInitializeEx.restype = hresult
    ole.CoCreateInstance.argtypes = [pointer, pointer, w.DWORD, pointer, ctypes.POINTER(pointer)]
    ole.CoCreateInstance.restype = hresult
    ole.CoTaskMemFree.argtypes = [pointer]
    ole.CoTaskMemFree.restype = None
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, pointer, pointer, ctypes.POINTER(pointer)]
    shell.SHCreateItemFromParsingName.restype = hresult
    check_hresult(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    dialog, folder, selected, path = pointer(), pointer(), pointer(), pointer()
    try:
        clsid = guid('DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7')
        iid_dialog = guid('D57C7288-D4AD-4768-BE02-9D969532D960')
        iid_item = guid('43826D1E-E718-42EE-BC55-A1E261C37BFE')
        check_hresult(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid_dialog), ctypes.byref(dialog)))
        check_hresult(shell.SHCreateItemFromParsingName(initial_folder, None, ctypes.byref(iid_item), ctypes.byref(folder)))

        class Filter(ctypes.Structure):
            _fields_ = [('name', w.LPCWSTR), ('pattern', w.LPCWSTR)]

        filters = (Filter * 1)(Filter('表情画像・動画', '*.png;*.jpg;*.jpeg;*.gif;*.mp4;*.webm'))
        # IFileDialog: SetFileTypes, SetOptions, SetFolder, SetTitle, Show, GetResult.
        check_hresult(com_method(dialog, 4, hresult, w.UINT, ctypes.POINTER(Filter))(dialog, len(filters), filters))
        check_hresult(com_method(dialog, 9, hresult, w.DWORD)(dialog, 0x02001848))
        check_hresult(com_method(dialog, 12, hresult, pointer)(dialog, folder))
        check_hresult(com_method(dialog, 17, hresult, w.LPCWSTR)(dialog, '登録するアバターの表情を選択'))
        result = com_method(dialog, 3, hresult, w.HWND)(dialog, None)
        if result & 0xffffffff == 0x800704c7:  # ERROR_CANCELLED
            return None
        check_hresult(result)
        check_hresult(com_method(dialog, 20, hresult, ctypes.POINTER(pointer))(dialog, ctypes.byref(selected)))
        # IShellItem::GetDisplayName, SIGDN_FILESYSPATH.
        check_hresult(com_method(selected, 5, hresult, w.DWORD, ctypes.POINTER(pointer))(selected, 0x80058000, ctypes.byref(path)))
        return ctypes.wstring_at(path)
    finally:
        if path:
            ole.CoTaskMemFree(path)
        for interface in (selected, folder, dialog):
            if interface:
                com_method(interface, 2, w.ULONG)(interface)  # IUnknown::Release
        ole.CoUninitialize()


if __name__=='__main__':
 try:result={'path':pick_avatar(sys.argv[2]) if sys.argv[1:2]==['--avatar'] else pick()}
 except Exception:result={'error':'ファイル選択を完了できませんでした。パスを直接入力してください。'}
 print(json.dumps(result,ensure_ascii=True),flush=True)
