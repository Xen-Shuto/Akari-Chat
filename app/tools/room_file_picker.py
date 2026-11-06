"""Bounded native picker; choosing a file never imports or alters it."""
import json,subprocess,sys,time
from pathlib import Path
from .managed_process import ProcessGroup

def _pick_path(closing, *arguments):
 if closing():raise ValueError('終了処理中です。')
 child=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).with_name('room_file_picker_worker.py')),*arguments],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 group=None
 try:
  group=ProcessGroup(child);deadline=time.monotonic()+600
  while True:
   if closing():raise ValueError('終了処理のためファイル選択を中止しました。')
   if time.monotonic()>=deadline:raise ValueError('ファイル選択の待機時間を超えました。もう一度選択してください。')
   try:output,_=child.communicate(timeout=.2);break
   except subprocess.TimeoutExpired:pass
  if child.returncode or len(output)>262144:raise ValueError('ファイル選択を完了できませんでした。')
  try:data=json.loads(output)
  except (ValueError,UnicodeError):raise ValueError('ファイル選択の結果を確認できませんでした。') from None
  if not isinstance(data,dict) or data.get('error'):raise ValueError('ファイル選択を完了できませんでした。')
  value=data.get('path')
  if value is None:return None
  if not isinstance(value,str) or not Path(value).is_absolute() or not Path(value).is_file():
   raise ValueError('選択したファイルを確認できませんでした。')
  return Path(value)
 finally:
  if child.poll() is None:child.kill();child.communicate()
  if group:group.close()


def pick_room_config(closing=lambda:False):
 path=_pick_path(closing)
 if path is not None and path.name!='room_config.json':
  raise ValueError('room_config.jsonを選択してください。')
 return {'path':str(path) if path is not None else None}


def pick_avatar_file(initial_folder, closing):
 return _pick_path(closing, '--avatar', str(initial_folder))
