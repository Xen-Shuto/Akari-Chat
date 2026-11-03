"""No app/data imports. Parse static PDF text from stdin inside a disposable process."""
import base64,io,json,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1]
wheel=APP/'runtime/pdf_packages/pypdf-6.21.2-py3-none-any.whl'
sys.path[:0]=[str(wheel),str(APP/'runtime/pdf_packages/typing_extensions-4.16.0-py3-none-any.whl')]

def main():
 from pypdf import PdfReader
 # Decompression guard supported by the pinned pypdf version.
 import pypdf.filters
 if hasattr(pypdf.filters,'ZLIB_MAX_OUTPUT_LENGTH'):pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH=16*1024*1024
 raw=base64.b64decode(sys.stdin.buffer.read(1500000),validate=True)
 if len(raw)>1048576:raise ValueError('size')
 reader=PdfReader(io.BytesIO(raw),strict=True)
 if reader.is_encrypted or len(reader.pages)>100:raise ValueError('encrypted_or_pages')
 parts=[];size=0
 for index,page in enumerate(reader.pages):
  body=page.extract_text() or '';size+=len(body)
  if size>200000:raise ValueError('text_limit')
  parts.append('[Page '+str(index+1)+']\n'+body)
 text='\n\n'.join(parts).strip()
 if not any((part.split(']\n',1)[1] if ']\n' in part else '').strip() for part in parts):raise ValueError('no_text')
 print(json.dumps({'text':text,'pages':len(reader.pages)},ensure_ascii=True))
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'error':'PDF本文を抽出できませんでした。暗号化・画像のみ・ページ数/本文上限・形式を確認してください。'}));sys.exit(1)
