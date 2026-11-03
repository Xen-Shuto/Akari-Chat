"""Cancellable PDF text extraction in the explicit app-local worker."""
import base64,json,subprocess,sys,time
from pathlib import Path

def extract_pdf(raw,checkpoint=lambda:None,timeout=12):
 app=Path(__file__).resolve().parents[1];python=app/'runtime/python.exe';worker=Path(__file__).with_name('pdf_worker.py')
 if any(not (app/'runtime/pdf_packages'/name).is_file() for name in ('pypdf-6.21.2-py3-none-any.whl','typing_extensions-4.16.0-py3-none-any.whl')):raise ValueError('PDF抽出用ライブラリがありません。tools/setup_pdf.pyを実行してください。')
 checkpoint()
 child=subprocess.Popen([str(python if python.is_file() else Path(sys.executable)),'-I','-B',str(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 try:
  started=time.monotonic();data=base64.b64encode(raw)
  while True:
   checkpoint()
   if time.monotonic()-started>timeout:raise ValueError('PDF本文抽出の待機時間を超えました。')
   try:output,_=child.communicate(data,timeout=.1);break
   except subprocess.TimeoutExpired:data=None
  checkpoint();result=json.loads(output)
  if child.returncode or result.get('error'):raise ValueError(result.get('error','PDF抽出に失敗しました。'))
  return result
 finally:
  if child.poll() is None:child.kill();child.communicate()
