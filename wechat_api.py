"""微信API封装 - token管理、图片上传、草稿创建"""
import json
import os
import time
import mimetypes
import urllib.request

from config import load_config

TOKEN_CACHE_FILE = "/tmp/wechat_typeset_token.json"


def get_access_token():
    """获取微信access_token，带缓存"""
    cfg = load_config()
    app_id = cfg.get("wechat_app_id", "")
    app_secret = cfg.get("wechat_app_secret", "")

    if not app_id or not app_secret:
        return None

    # 尝试缓存
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, 'r') as f:
                cached = json.load(f)
            if cached.get("access_token") and cached.get("expire_time", 0) > time.time() + 300:
                return cached["access_token"]
    except Exception:
        pass

    # 请求新token
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
            if "access_token" in data:
                try:
                    with open(TOKEN_CACHE_FILE, 'w') as f:
                        json.dump({"access_token": data["access_token"],
                                   "expire_time": data.get("expires_in", 7200) + time.time()}, f)
                except Exception:
                    pass
                return data["access_token"]
    except Exception:
        pass
    return None


def upload_image(token, image_path):
    """上传图片到微信素材库，返回 (media_id, url)"""
    if not os.path.exists(image_path):
        return None, None

    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/jpeg"

    # PNG转JPG（微信封面图要求）
    actual_path = image_path
    if image_path.lower().endswith('.png'):
        try:
            from PIL import Image
            img = Image.open(image_path)
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            jpg_path = image_path.rsplit('.', 1)[0] + '_tmp.jpg'
            img.save(jpg_path, 'JPEG', quality=95)
            actual_path = jpg_path
            mime_type = "image/jpeg"
        except Exception:
            pass

    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    boundary = '----WxFormBoundary7MA4YWxkTrZu0gW'
    filename = os.path.basename(actual_path)

    with open(actual_path, 'rb') as f:
        file_data = f.read()

    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f'Content-Type: {mime_type}\r\n\r\n'
    ).encode('utf-8') + file_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')

    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    }, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            if "media_id" in data:
                return data["media_id"], data.get("url")
    except Exception:
        pass
    return None, None


def create_draft(token, title, author, digest, content, thumb_media_id):
    """创建公众号草稿"""
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    payload = {
        "articles": [{
            "title": title,
            "author": author,
            "digest": digest[:120] if digest else "",
            "content": content,
            "content_source_url": "",
            "thumb_media_id": thumb_media_id,
            "show_cover_pic": 1,
            "need_open_comment": 1,
            "only_fans_can_comment": 0
        }]
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if "media_id" in result:
                return result
    except Exception:
        pass
    return None
