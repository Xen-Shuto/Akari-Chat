"""Static generated/reference image headers; no codecs or metadata rewriting.

WebP container: https://developers.google.com/speed/webp/docs/riff_container
This checks container bounds and declared dimensions, not compressed pixel decoding.
"""
from .multimodal import dimensions as legacy_dimensions

EXTENSIONS={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}


def detected_mime(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if raw.startswith(b'\xff\xd8'):return 'image/jpeg'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':return 'image/webp'
    raise ValueError('PNG/JPEG/静止WebPの画像データを確認できません。')


def webp_dimensions(raw):
    def fail():raise ValueError('静止WebPの画像構造・寸法を確認できません。')
    if len(raw)<20 or raw[:4]!=b'RIFF' or raw[8:12]!=b'WEBP' or int.from_bytes(raw[4:8],'little')+8!=len(raw):fail()
    offset=12;canvas=None;frame=None;count=0
    while offset<len(raw):
        count+=1
        if count>256 or offset+8>len(raw):fail()
        kind=raw[offset:offset+4];size=int.from_bytes(raw[offset+4:offset+8],'little');start=offset+8;end=start+size;following=end+(size&1)
        if following>len(raw) or size&1 and raw[end]!=0:fail()
        data=raw[start:end]
        if kind in (b'ANIM',b'ANMF'):raise ValueError('アニメーションWebPは生成画像・参照画像に使えません。静止画にしてください。')
        if kind==b'VP8X':
            if offset!=12 or canvas is not None or size!=10 or data[0]&0xC1 or data[1:4]!=b'\0\0\0':fail()
            if data[0]&2:raise ValueError('アニメーションWebPは生成画像・参照画像に使えません。静止画にしてください。')
            canvas=(1+int.from_bytes(data[4:7],'little'),1+int.from_bytes(data[7:10],'little'))
        elif kind==b'VP8 ':
            if frame is not None or size<11 or data[0]&1 or data[3:6]!=b'\x9d\x01\x2a':fail()
            frame=(int.from_bytes(data[6:8],'little')&0x3fff,int.from_bytes(data[8:10],'little')&0x3fff)
        elif kind==b'VP8L':
            if frame is not None or size<6 or data[0]!=0x2f:fail()
            bits=int.from_bytes(data[1:5],'little')
            if bits>>29:fail()
            frame=(1+(bits&0x3fff),1+((bits>>14)&0x3fff))
        offset=following
    if frame is None or not all(frame) or canvas is not None and canvas!=frame:fail()
    return frame


def dimensions(raw,mime):
    return webp_dimensions(raw) if mime=='image/webp' else legacy_dimensions(raw,mime)
