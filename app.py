#!/usr/bin/env python3
"""
微信公众号排版 Web 应用
- Markdown → 微信兼容行内样式HTML
- 20+套风格各异模板（参考awesome-design-md）
- 实时预览 + 样式微调
- 图片上传+URL替换
- 一键推送到公众号草稿箱
- Agent REST API
"""

import json
import re
import os
import subprocess
import time
import tempfile
import urllib.request
import mimetypes
from pathlib import Path
from flask import Flask, render_template, request, jsonify

from themes import get_all_themes

app = Flask(__name__)

# 微信公众号配置 — 凭证由前端传入（存在浏览器localStorage），服务器不留存
TOKEN_CACHE_DIR = "/tmp/wechat_token_cache"
os.makedirs(TOKEN_CACHE_DIR, exist_ok=True)

# 文章目录
ARTICLES_DIR = os.environ.get("ARTICLES_DIR", "/home/rover/XKA公众号")

# 上传目录
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/tmp/wechat_typeset_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)




# ============================================================
# Markdown → 微信行内样式HTML 转换器
# ============================================================

def md_to_wechat_html(md_content, theme_name="herb-garden", overrides=None):
    """将 Markdown 转换为微信兼容的行内样式HTML
    
    overrides: 微调参数字典，如 {"primary": "#FF0000", "font_size": "16px"}
    """
    themes = get_all_themes()
    theme = themes.get(theme_name, themes.get("herb-garden"))
    S = dict(theme["styles"])  # 浅拷贝

    # 应用微调覆盖
    if overrides:
        if overrides.get("primary"):
            new_color = overrides["primary"]
            # 替换主色相关
            for key in ["strong", "em", "h2", "h3", "tag", "num", "card_h"]:
                if key in S:
                    S[key] = re.sub(r'color:#?[0-9A-Fa-f]{3,8}', f'color:{new_color}', S[key])
        if overrides.get("font_size"):
            fs = overrides["font_size"]
            S["p"] = re.sub(r'font-size:\d+px', f'font-size:{fs}', S.get("p", ""))
        if overrides.get("line_height"):
            lh = overrides["line_height"]
            S["p"] = re.sub(r'line-height:[\d.]+', f'line-height:{lh}', S.get("p", ""))
        if overrides.get("border_radius"):
            br = overrides["border_radius"]
            for key in ["card_green", "card_amber", "tip", "img", "blockquote"]:
                if key in S:
                    S[key] = re.sub(r'border-radius:[\d]+px', f'border-radius:{br}', S[key])

    # 提取标题
    title_match = re.search(r'^#\s+(.+)$', md_content, re.MULTILINE)
    title = title_match.group(1) if title_match else ""

    # 去掉标题行
    body = re.sub(r'^#\s+.+$', '', md_content, flags=re.MULTILINE).strip()

    # 处理行内格式
    def process_inline(text):
        text = re.sub(r'\*\*(.+?)\*\*', lambda m: f'<span style="{S["strong"]}">{m.group(1)}</span>', text)
        text = re.sub(r'\*(.+?)\*', lambda m: f'<span style="{S["em"]}">{m.group(1)}</span>', text)
        code_inline = S.get('code_inline', 'background:#f0f0f0;padding:2px 6px;border-radius:3px;font-size:13px;color:#e83e8c;')
        text = re.sub(r'`([^`]+)`', lambda m: f'<code style="{code_inline}">{m.group(1)}</code>', text)
        text = re.sub(r'\{\{tag:(.+?)\}\}', lambda m: f'<div style="{S["tag_wrap"]}"><span style="{S["tag"]}">{m.group(1)}</span></div>', text)
        text = re.sub(r'\{\{num:(\d+)\}\}', lambda m: f'<span style="{S["num"]}">{m.group(1)}</span>', text)
        return text

    # 处理图片
    def replace_image(match):
        alt_text = match.group(1)
        img_path = match.group(2).strip()
        return f'<img src="{img_path}" alt="{alt_text}" style="{S["img"]}" />'

    # 块级状态
    in_card = False
    card_type = "green"

    # 表格样式
    p_font = S['p'].split('font-size:')[1].split(';')[0] if 'font-size:' in S['p'] else '14px'
    tbl = {
        "table": f"width:100%;border-collapse:collapse;margin:16px 0;font-size:{p_font};",
        "th": f"padding:10px 12px;text-align:left;font-weight:700;border-bottom:2px solid {theme['colors'].get('primary','#333')};color:{theme['colors'].get('accent','#333')};background:{theme['colors'].get('primary_light','#f5f5f5')};",
        "td": f"padding:8px 12px;border-bottom:1px solid #e0e0e0;color:{theme['colors'].get('text','#333')};",
        "tr_alt": f"background:{theme['colors'].get('warm_bg','#fafafa')};",
    }

    def process_block(block):
        nonlocal in_card, card_type
        block = block.strip()
        if not block:
            return ''

        # 卡片
        if re.match(r'\{\{card:绿色\}\}', block):
            in_card = True; card_type = "green"
            return f'<div style="{S["card_green"]}">'
        if re.match(r'\{\{card:琥珀\}\}', block):
            in_card = True; card_type = "amber"
            return f'<div style="{S["card_amber"]}">'
        if re.match(r'\{\{/card\}\}', block):
            in_card = False
            return '</div>'

        # 提示框
        tip_match = re.match(r'\{\{tip:(.+?)\}\}', block)
        if tip_match:
            return f'<div style="{S["tip"]}"><p style="{S["tip_text"]}">{process_inline(tip_match.group(1))}</p></div>'

        # 代码块
        code_match = re.match(r'^```(\w*)\n(.+)```$', block, re.DOTALL)
        if code_match:
            lang = code_match.group(1)
            code_content = code_match.group(2)
            # HTML转义
            code_content = code_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lang_tag = f'<span style="font-size:11px;color:#888;float:right;">{lang}</span>' if lang else ''
            code_style = S.get('code', 'background:#f6f8fa;border-radius:6px;padding:16px;margin:16px 0;overflow-x:auto;font-size:13px;line-height:1.6;')
            code_inline = S.get('code_inline', 'background:#f0f0f0;padding:2px 6px;border-radius:3px;font-size:13px;color:#e83e8c;')
            return f'<div style="{code_style}">{lang_tag}<pre style="margin:0;padding:0;background:transparent;white-space:pre-wrap;word-wrap:break-word;font-family:Menlo,Monaco,Consolas,monospace;"><code style="font-family:Menlo,Monaco,Consolas,monospace;color:#333;">{code_content}</code></pre></div>'

        # 分隔线
        if re.match(r'^---+$', block):
            return f'<hr style="{S["hr"]}" />'

        # 引用
        if block.startswith('>'):
            text_content = re.sub(r'^>\s?', '', block, flags=re.MULTILINE)
            return f'<blockquote style="{S["blockquote"]}"><p style="margin:0;line-height:1.8;">{process_inline(text_content)}</p></blockquote>'

        # 标题
        hm = re.match(r'^#{1,3}\s+(.+)$', block)
        if hm:
            level = len(hm.group(1))
            t = process_inline(hm.group(1))
            tag = 'h1' if level == 1 else ('h2' if level == 2 else 'h3')
            style_key = 'title' if level == 1 else ('h2' if level == 2 else 'h3')
            return f'<{tag} style="{S[style_key]}">{t}</{tag}>'

        # 无序列表
        if re.match(r'^[-*]\s+', block):
            items = re.findall(r'^[-*]\s+(.+)$', block, re.MULTILINE)
            return '\n'.join(f'<p style="{S["p"]}">◆ {process_inline(i)}</p>' for i in items)

        # 有序列表
        if re.match(r'^\d+\.\s+', block):
            items = re.findall(r'^\d+\.\s+(.+)$', block, re.MULTILINE)
            return '\n'.join(f'<p style="{S["p"]}"><span style="{S["num"]}">{idx}</span>{process_inline(i)}</p>' for idx, i in enumerate(items, 1))

        # 图片行
        img_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)', block)
        if img_match:
            return replace_image(img_match)

        # 普通段落
        block = process_inline(block)
        block = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_image, block)
        return f'<p style="{S["p"]}">{block}</p>'

    def process_table(table_lines):
        """解析Markdown表格→微信行内样式HTML"""
        rows = []
        for line in table_lines:
            line = line.strip()
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            rows.append(cells)
        if not rows:
            return ''

        html = f'<table style="{tbl["table"]}">'
        html += '<tr>'
        for cell in rows[0]:
            html += f'<th style="{tbl["th"]}">{process_inline(cell)}</th>'
        html += '</tr>'
        for i, row in enumerate(rows[1:], 1):
            tr_style = tbl["tr_alt"] if i % 2 == 0 else ''
            html += f'<tr style="{tr_style}">'
            for cell in row:
                html += f'<td style="{tbl["td"]}">{process_inline(cell)}</td>'
            html += '</tr>'
        html += '</table>'
        return html

    # 先提取代码块，用占位符替换，避免被分块逻辑拆碎
    code_blocks = []
    def save_code_block(m):
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return f'\n\n%%CODEBLOCK_{idx}%%\n\n'
    body = re.sub(r'```[\s\S]*?```', save_code_block, body)

    # 分块：表格行不能被拆开
    raw_blocks = re.split(r'\n\n+', body)
    final_blocks = []
    for b in raw_blocks:
        b = b.strip()
        if not b:
            continue
        # 代码块占位符
        cb_match = re.match(r'^%%CODEBLOCK_(\d+)%%$', b)
        if cb_match:
            final_blocks.append(('code', code_blocks[int(cb_match.group(1))]))
            continue
        lines = b.split('\n')
        table_lines = []
        other_lines = []
        in_table = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') and '|' in stripped[1:]:
                in_table = True
                table_lines.append(stripped)
            else:
                if in_table and table_lines:
                    final_blocks.append(('table', '\n'.join(table_lines)))
                    table_lines = []
                    in_table = False
                other_lines.append(stripped)
        if table_lines:
            final_blocks.append(('table', '\n'.join(table_lines)))
        if other_lines:
            final_blocks.append(('normal', '\n'.join(other_lines)))

    html_blocks = []
    for btype, bcontent in final_blocks:
        if btype == 'table':
            html_blocks.append(process_table(bcontent.split('\n')))
        elif btype == 'code':
            html_blocks.append(process_block(bcontent))
        else:
            html_blocks.append(process_block(bcontent))

    body_html = '\n'.join(html_blocks)
    full_html = f'<div style="{S["body"]}">\n{body_html}\n</div>'

    return title, full_html


# ============================================================
# 微信 API
# ============================================================

def get_access_token(app_id, app_secret):
    """用前端传入的凭证获取access_token，缓存按app_id隔离"""
    if not app_secret or not app_id:
        return None

    # 按app_id隔离的缓存文件
    cache_file = os.path.join(TOKEN_CACHE_DIR, f"token_{app_id}.json")

    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cached = json.load(f)
            if cached.get("access_token") and cached.get("expire_time"):
                if cached["expire_time"] > time.time() + 300:
                    return cached["access_token"]
    except Exception:
        pass

    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
            if "access_token" in data:
                cache_data = {
                    "access_token": data["access_token"],
                    "expire_time": data.get("expires_in", 7200) + time.time(),
                }
                try:
                    with open(cache_file, 'w') as f:
                        json.dump(cache_data, f)
                except Exception:
                    pass
                return data["access_token"]
            return None
    except Exception:
        return None


def upload_image_to_wechat(token, image_path):
    if not os.path.exists(image_path):
        return None, None

    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/jpeg"

    # PNG转JPG
    if image_path.lower().endswith('.png'):
        try:
            from PIL import Image
            img = Image.open(image_path)
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            jpg_path = image_path.rsplit('.', 1)[0] + '_wechat.jpg'
            img.save(jpg_path, 'JPEG', quality=95)
            image_path = jpg_path
            mime_type = "image/jpeg"
        except Exception:
            pass

    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    filename = os.path.basename(image_path)

    with open(image_path, 'rb') as f:
        file_data = f.read()

    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f'Content-Type: {mime_type}\r\n\r\n'
    ).encode('utf-8') + file_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')

    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Accept': 'application/json'
    }, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            if "media_id" in data:
                return data["media_id"], data.get("url")
            return None, None
    except Exception:
        return None, None


def create_wechat_draft(token, title, author, digest, content, thumb_media_id):
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
            app.logger.error(f"创建草稿失败，微信返回: {result}")
            return None
    except Exception as e:
        app.logger.error(f"创建草稿请求异常: {e}")
        return None


# ============================================================
# 路由
# ============================================================

@app.route('/')
def index():
    return render_template('index.html', themes=get_all_themes())


@app.route('/api/themes')
def api_themes():
    themes = get_all_themes()
    result = {}
    for k, v in themes.items():
        result[k] = {"name": v["name"], "desc": v["desc"], "category": v.get("category", ""), "colors": v.get("colors", {})}
    return jsonify(result)


@app.route('/api/preview', methods=['POST'])
def api_preview():
    data = request.json or {}
    md_content = data.get("md_content", "")
    theme = data.get("theme", "herb-garden")
    overrides = data.get("overrides", {})

    title, html = md_to_wechat_html(md_content, theme, overrides)
    return jsonify({"title": title, "html": html})


@app.route('/api/publish', methods=['POST'])
def api_publish():
    data = request.json or {}
    md_content = data.get("md_content", "")
    theme = data.get("theme", "herb-garden")
    overrides = data.get("overrides", {})
    cover_url = data.get("cover_url", "")
    app_id = data.get("app_id", "")
    app_secret = data.get("app_secret", "")
    author = data.get("author", "XKA北辰星团队")

    if not app_secret or not app_id:
        return jsonify({"error": "未配置微信凭证，请在设置页面填写 AppID 和 AppSecret"}), 400

    # 构建传递给 md2wechat CLI 的环境变量
    md2wechat_env = {**os.environ, 'WECHAT_APPID': app_id, 'WECHAT_SECRET': app_secret}

    # 生成HTML（用自带的排版引擎）
    title, wechat_html = md_to_wechat_html(md_content, theme, overrides)

    # 生成摘要
    body_text = re.sub(r'^#.*$', '', md_content, flags=re.MULTILINE).strip()
    paragraphs = [p.strip() for p in body_text.split('\n\n') if p.strip() and not re.match(r'^[-*>]|---|\{\{', p)]
    digest = paragraphs[0][:120] if paragraphs else ""

    # 上传正文中的本地图片并替换URL
    image_map = {}
    local_images = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', md_content)
    for img_path in local_images:
        clean = img_path.strip()
        if clean.startswith(('http://', 'https://')):
            continue
        abs_path = os.path.join(UPLOAD_DIR, clean.lstrip('/').replace('uploads/', '', 1)) if not os.path.isabs(clean) else clean
        if os.path.exists(abs_path):
            try:
                result = subprocess.run(
                    ['md2wechat', 'upload_image', abs_path, '--json'],
                    capture_output=True, text=True, timeout=30,
                    env=md2wechat_env
                )
                if result.returncode == 0:
                    upload_data = json.loads(result.stdout)
                    if upload_data.get('success'):
                        wechat_url = upload_data['data'].get('wechat_url', '')
                        if wechat_url:
                            wechat_html = wechat_html.replace(clean, wechat_url)
            except Exception as e:
                app.logger.warning(f"正文图片上传失败: {e}")

    # 用md2wechat上传封面图
    cover_media_id = None
    if cover_url:
        clean_cover = cover_url.lstrip('/')
        if clean_cover.startswith('uploads/'):
            clean_cover = clean_cover[len('uploads/'):]
        abs_cover = os.path.join(UPLOAD_DIR, clean_cover) if not os.path.isabs(clean_cover) else clean_cover
        app.logger.info(f"封面图路径: {abs_cover} exists={os.path.exists(abs_cover)}")
        if os.path.exists(abs_cover):
            try:
                result = subprocess.run(
                    ['md2wechat', 'upload_image', abs_cover, '--json'],
                    capture_output=True, text=True, timeout=30,
                    env=md2wechat_env
                )
                if result.returncode == 0:
                    upload_data = json.loads(result.stdout)
                    if upload_data.get('success'):
                        cover_media_id = upload_data['data'].get('media_id')
                        app.logger.info(f"封面上传成功: media_id={cover_media_id}")
                else:
                    app.logger.error(f"封面上传失败: {result.stderr}")
            except Exception as e:
                app.logger.error(f"封面上传异常: {e}")

    if not cover_media_id:
        return jsonify({"error": "封面图上传失败，微信公众号草稿必须上传封面图（40007）"}), 400

    # 用md2wechat创建草稿
    draft = {
        "articles": [{
            "title": title,
            "author": author,
            "digest": digest[:120] if digest else "",
            "content": wechat_html,
            "thumb_media_id": cover_media_id,
            "show_cover_pic": 0,
            "need_open_comment": 1,
            "only_fans_can_comment": 0
        }]
    }
    draft_path = os.path.join(UPLOAD_DIR, f"draft_{app_id}_{int(time.time())}.json")
    with open(draft_path, 'w', encoding='utf-8') as f:
        json.dump(draft, f, ensure_ascii=False)

    try:
        result = subprocess.run(
            ['md2wechat', 'create_draft', draft_path, '--json'],
            capture_output=True, text=True, timeout=30,
            env=md2wechat_env
        )
        if result.returncode == 0:
            draft_data = json.loads(result.stdout)
            if draft_data.get('success'):
                return jsonify({
                    "success": True,
                    "media_id": draft_data['data'].get('media_id'),
                    "title": title,
                    "message": "草稿创建成功！请到公众号后台查看和发布。"
                })
        app.logger.error(f"创建草稿失败: stdout={result.stdout} stderr={result.stderr}")
        return jsonify({"error": "创建草稿失败，请检查微信API配置"}), 400
    except Exception as e:
        app.logger.error(f"创建草稿异常: {e}")
        return jsonify({"error": f"创建草稿异常: {str(e)}"}), 500


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传图片到临时目录"""
    if 'file' not in request.files:
        return jsonify({"error": "没有文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400

    filename = file.filename
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)
    
    return jsonify({
        "success": True,
        "filename": filename,
        "path": save_path,
        "url": f"/uploads/{filename}"
    })


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/api/token_status', methods=['POST'])
def api_token_status():
    """验证前端传入的凭证是否有效"""
    data = request.json or {}
    app_id = data.get("app_id", "")
    app_secret = data.get("app_secret", "")
    if not app_secret or not app_id:
        return jsonify({"configured": False, "message": "未配置微信API凭证"})
    token = get_access_token(app_id, app_secret)
    if token:
        return jsonify({"configured": True, "valid": True, "message": "access_token 有效"})
    else:
        return jsonify({"configured": True, "valid": False, "message": "access_token 获取失败，请检查 AppID/AppSecret"})


# ============================================================
# md2wechat CLI 对接路由
# ============================================================

def _md2wechat_env(app_id, app_secret):
    """构建md2wechat CLI子进程的环境变量——凭证从前端传入"""
    return {**os.environ, 'WECHAT_APPID': app_id, 'WECHAT_SECRET': app_secret}


@app.route('/api/md2wechat_themes')
def api_md2wechat_themes():
    """获取md2wechat的主题列表"""
    try:
        result = subprocess.run(
            ['md2wechat', 'themes', 'list', '--json'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get('success'):
                return jsonify({"success": True, "themes": data['data'].get('themes', [])})
        return jsonify({"success": False, "error": "获取主题列表失败", "themes": []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "themes": []})


@app.route('/api/md2wechat_providers')
def api_md2wechat_providers():
    """获取md2wechat的图片生成provider列表"""
    try:
        result = subprocess.run(
            ['md2wechat', 'providers', '--json'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get('success'):
                return jsonify({"success": True, "providers": data['data'].get('providers', [])})
        return jsonify({"success": False, "error": "获取provider列表失败", "providers": []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "providers": []})


@app.route('/api/generate_cover', methods=['POST'])
def api_generate_cover():
    """调用md2wechat生成封面图并上传到微信"""
    data = request.json or {}
    app_id = data.get("app_id", "")
    app_secret = data.get("app_secret", "")
    title = data.get("title", "")
    summary = data.get("summary", "")
    preset = data.get("preset", "cover-default")
    aspect_ratio = data.get("aspect_ratio", "16:9")

    if not app_id or not app_secret:
        return jsonify({"error": "未配置微信凭证"}), 400

    env = _md2wechat_env(app_id, app_secret)
    try:
        result = subprocess.run(
            ['md2wechat', 'generate_cover',
             '--preset', preset,
             '--title', title,
             '--summary', summary,
             '--aspect-ratio', aspect_ratio,
             '--json'],
            capture_output=True, text=True, timeout=120,
            env=env
        )
        if result.returncode == 0:
            cover_data = json.loads(result.stdout)
            if cover_data.get('success'):
                return jsonify({
                    "success": True,
                    "image_path": cover_data['data'].get('local_path', ''),
                    "media_id": cover_data['data'].get('media_id', ''),
                    "wechat_url": cover_data['data'].get('wechat_url', '')
                })
            return jsonify({"error": cover_data.get('error', '封面生成失败')}), 400
        return jsonify({"error": f"封面生成失败: {result.stderr[:200]}"}), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "封面生成超时（120秒）"}), 504
    except Exception as e:
        return jsonify({"error": f"封面生成异常: {str(e)}"}), 500


@app.route('/api/humanize', methods=['POST'])
def api_humanize():
    """调用md2wechat AI去痕"""
    data = request.json or {}
    app_id = data.get("app_id", "")
    app_secret = data.get("app_secret", "")
    content = data.get("content", "")
    intensity = data.get("intensity", "medium")  # gentle/medium/aggressive

    if not content.strip():
        return jsonify({"error": "内容为空"}), 400

    env = _md2wechat_env(app_id, app_secret) if app_id and app_secret else os.environ.copy()

    # 写临时文件（按app_id隔离）
    tmp_path = os.path.join(UPLOAD_DIR, f"humanize_{app_id or 'anon'}_{int(time.time())}.md")
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(content)

    try:
        result = subprocess.run(
            ['md2wechat', 'humanize', tmp_path,
             '--intensity', intensity, '--json'],
            capture_output=True, text=True, timeout=60,
            env=env
        )
        if result.returncode == 0:
            h_data = json.loads(result.stdout)
            if h_data.get('success'):
                return jsonify({
                    "success": True,
                    "humanized_content": h_data['data'].get('content', content)
                })
            return jsonify({"error": h_data.get('error', '去痕失败')}), 400
        return jsonify({"error": f"去痕失败: {result.stderr[:200]}"}), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "去痕超时（60秒）"}), 504
    except Exception as e:
        return jsonify({"error": f"去痕异常: {str(e)}"}), 500


@app.route('/api/generate_image', methods=['POST'])
def api_generate_image():
    """调用md2wechat生成图片并上传到微信"""
    data = request.json or {}
    app_id = data.get("app_id", "")
    app_secret = data.get("app_secret", "")
    prompt = data.get("prompt", "")
    provider = data.get("provider", "volcengine")

    if not prompt.strip():
        return jsonify({"error": "提示词为空"}), 400
    if not app_id or not app_secret:
        return jsonify({"error": "未配置微信凭证"}), 400

    env = _md2wechat_env(app_id, app_secret)
    try:
        result = subprocess.run(
            ['md2wechat', 'generate_image', prompt,
             '--provider', provider, '--json'],
            capture_output=True, text=True, timeout=120,
            env=env
        )
        if result.returncode == 0:
            img_data = json.loads(result.stdout)
            if img_data.get('success'):
                return jsonify({
                    "success": True,
                    "image_path": img_data['data'].get('local_path', ''),
                    "media_id": img_data['data'].get('media_id', ''),
                    "wechat_url": img_data['data'].get('wechat_url', '')
                })
            return jsonify({"error": img_data.get('error', '图片生成失败')}), 400
        return jsonify({"error": f"图片生成失败: {result.stderr[:200]}"}), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "图片生成超时（120秒）"}), 504
    except Exception as e:
        return jsonify({"error": f"图片生成异常: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9120))
    app.run(host="0.0.0.0", port=port, debug=True)
