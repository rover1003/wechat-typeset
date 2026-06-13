"""配置管理 - AppSecret持久化、运行时配置"""
import json
import os

CONFIG_FILE = os.environ.get("TYPESET_CONFIG_FILE", "/home/rover/wechat-typeset-app/data/config.json")

DEFAULTS = {
    "wechat_app_id": os.environ.get("WECHAT_APP_ID", "wxe8a5927558605b29"),
    "wechat_app_secret": os.environ.get("WECHAT_APP_SECRET", ""),
    "author": os.environ.get("WECHAT_AUTHOR", "XKA北辰星团队"),
    "articles_dir": os.environ.get("ARTICLES_DIR", "/home/rover/XKA公众号"),
    "port": int(os.environ.get("PORT", 9120)),
}


def load_config():
    """加载配置，文件优先，环境变量兜底"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            cfg = {**DEFAULTS, **saved}
            return cfg
    except Exception:
        pass
    return {**DEFAULTS}


def save_config(updates: dict):
    """保存配置到文件"""
    cfg = load_config()
    cfg.update(updates)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def get(key, default=None):
    return load_config().get(key, default)
