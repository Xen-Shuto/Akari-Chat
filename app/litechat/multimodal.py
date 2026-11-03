"""Local image validation and OpenAI-compatible content parts."""
import base64
import binascii
import re
import struct

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_CHAT_BYTES = 18 * 1024 * 1024


def dimensions(raw, mime):
    if mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[12:16] == b"IHDR" and len(raw) >= 33:
        return struct.unpack(">II",raw[16:24])
    if mime == "image/jpeg" and raw.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 255:
                break
            while offset < len(raw) and raw[offset] == 255:
                offset += 1
            if offset >= len(raw): break
            marker=raw[offset]; offset+=1
            if marker in (0xD8,0x01) or 0xD0 <= marker <= 0xD7: continue
            if marker in (0xD9,0xDA): break
            length=int.from_bytes(raw[offset:offset+2],"big")
            if length < 2 or offset+length > len(raw): break
            if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF) and length >= 8:
                height,width=struct.unpack(">HH",raw[offset+3:offset+7])
                return width,height
            offset+=length
    raise ValueError("PNGまたはJPEGの画像データを確認できません。")


def decode_images(images):
    if not isinstance(images,list) or len(images) > MAX_IMAGES:
        raise ValueError("画像は1回に4枚まで添付できます。")
    result=[]
    total=0
    for image in images:
        if not isinstance(image,dict): raise ValueError("画像データが不正です。")
        data=image.get("data","")
        if not isinstance(data,str) or len(data) > MAX_IMAGE_BYTES*4//3+128:
            raise ValueError("画像は1枚5MiBまでです。")
        match=re.fullmatch(r"data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=\r\n]+)",data)
        if not match: raise ValueError("画像はPNG/JPEGのデータとして添付してください。")
        try:
            raw=base64.b64decode(match[2],validate=True)
        except (ValueError,binascii.Error):
            raise ValueError("画像のbase64データが不正です。") from None
        if not raw or len(raw) > MAX_IMAGE_BYTES: raise ValueError("画像は1枚5MiBまでです。")
        total+=len(raw)
        if total > MAX_TOTAL_BYTES: raise ValueError("添付画像の合計は12MiBまでです。")
        width,height=dimensions(raw,match[1])
        if not 0 < width <= 8192 or not 0 < height <= 8192 or width*height > 16777216:
            raise ValueError("画像が大きすぎます。長辺2048px程度に縮小してください。")
        name=image.get("name","画像")
        if not isinstance(name,str): raise ValueError("画像の名前が不正です。")
        name=re.split(r"[/\\]",name)[-1]
        name="".join(c for c in name if ord(c)>=32)[:120] or "画像"
        result.append({"name":name,"mime":match[1],"raw":raw,"size":len(raw),"width":width,"height":height})
    return result


def content(text, images):
    if not images: return text
    parts=[{"type":"text","text":text or "この画像について教えて。"}]
    for image in images:
        encoded=base64.b64encode(image["raw"]).decode("ascii")
        parts.append({"type":"image_url","image_url":{"url":f'data:{image["mime"]};base64,{encoded}'}})
    return parts
