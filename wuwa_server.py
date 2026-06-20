#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸣潮抽卡分析 — 本地Web服务
===========================
启动后浏览器访问 http://localhost:8766

功能：
  1. 上传抽卡记录JSON → 自动分析展示
  2. 合并历史记录 → 数据更新后自动刷新页面
  3. 图标本地缓存（并行下载）
  4. Fluent 2 主题（深/浅色切换，默认跟随系统）

用法:
  python wuwa_server.py              # 默认端口8766
  python wuwa_server.py --port 9000  # 指定端口
"""

import json, os, sys, datetime, argparse, time
import requests as req_lib
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory, render_template_string

# 抑制 requests verify=False 的 InsecureRequestWarning
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 配置
# ============================================================
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
ICONS_CHAR_DIR = os.path.join(DATA_DIR, "icons", "characters")
ICONS_WEAPON_DIR = os.path.join(DATA_DIR, "icons", "weapons")
CDN_CHAR_BASE = "https://files.wuthery.com/p/GameData/IDFiedResources/Common/Image/IconRoleHead256"
CDN_WEAPON_BASE = "https://files.wuthery.com/p/GameData/IDFiedResources/Common/Image/IconWeapon160"
ENCORE_CHAR_API = "https://api-v2.encore.moe/api/en/character"
ENCORE_WEAPON_API = "https://api-v2.encore.moe/api/en/weapon"
ENCORE_MAPPING_FILE = os.path.join(DATA_DIR, "icons", "encore_mapping.json")

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 内存中当前活跃数据
current_data = None
current_icon_map = {}

# encore.moe 备用图标映射: {resourceId: "https://..."}
_encore_icon_map = {}

# ============================================================
# encore.moe 备用图标源
# ============================================================
def _fetch_encore_mapping():
    """从 encore.moe API 获取 resourceId→图标URL 映射，缓存到本地JSON"""
    # 检查缓存是否可用（7天内）
    if os.path.exists(ENCORE_MAPPING_FILE):
        try:
            mtime = os.path.getmtime(ENCORE_MAPPING_FILE)
            age_days = (datetime.datetime.now().timestamp() - mtime) / 86400
            if age_days < 7:
                with open(ENCORE_MAPPING_FILE, "r", encoding="utf-8") as f:
                    mapping = json.load(f)
                print(f"  encore.moe映射: 从缓存加载 {len(mapping)} 条 ({age_days:.1f}天前)")
                return mapping
        except Exception:
            pass

    mapping = {}
    try:
        # 角色
        print("  正在获取 encore.moe 角色映射...")
        resp = req_lib.get(ENCORE_CHAR_API, headers={"User-Agent": "Mozilla/5.0"},
                           timeout=30, verify=False)
        char_data = resp.json()
        for char in char_data.get("roleList", []):
            rid = str(char.get("Id", ""))
            icon = char.get("RoleHeadIcon", "")
            if rid and icon:
                mapping[rid] = icon
        print(f"  角色: {len([k for k in mapping])} 个")

        # 武器
        print("  正在获取 encore.moe 武器映射...")
        resp = req_lib.get(ENCORE_WEAPON_API, headers={"User-Agent": "Mozilla/5.0"},
                           timeout=30, verify=False)
        weapon_data = resp.json()
        for wp in weapon_data.get("weapons", []):
            rid = str(wp.get("Id", ""))
            icon = wp.get("Icon", "")
            if rid and icon:
                mapping[rid] = icon
        print(f"  武器: 合计 {len(mapping)} 个映射")

        # 缓存到文件
        os.makedirs(os.path.dirname(ENCORE_MAPPING_FILE), exist_ok=True)
        with open(ENCORE_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
        print(f"  映射已缓存到 {ENCORE_MAPPING_FILE}")
    except Exception as e:
        print(f"  ⚠ encore.moe映射获取失败: {e}")
        # 获取失败时尝试用旧缓存
        if os.path.exists(ENCORE_MAPPING_FILE):
            try:
                with open(ENCORE_MAPPING_FILE, "r", encoding="utf-8") as f:
                    mapping = json.load(f)
                print(f"  使用旧缓存: {len(mapping)} 条")
            except Exception:
                pass

    return mapping

def load_encore_mapping():
    """加载 encore.moe 映射到内存"""
    global _encore_icon_map
    _encore_icon_map = _fetch_encore_mapping()
    print(f"  encore.moe 备用源就绪: {len(_encore_icon_map)} 个映射")

# ============================================================
# 图标缓存（并行下载 + CDN→encore.moe 回退）
# ============================================================
def ensure_icon_dirs():
    os.makedirs(ICONS_CHAR_DIR, exist_ok=True)
    os.makedirs(ICONS_WEAPON_DIR, exist_ok=True)

def _download_url(url, timeout=15):
    """下载URL内容，关闭SSL验证以兼容Windows证书不全的问题"""
    return req_lib.get(url, headers={"User-Agent": "Mozilla/5.0"},
                       timeout=timeout, verify=False)

def download_icon(resource_id, resource_type):
    if not resource_id:
        return (resource_id, "")
    rid = str(resource_id)
    is_char = resource_type == "\u89d2\u8272"
    local_dir = ICONS_CHAR_DIR if is_char else ICONS_WEAPON_DIR

    # 1) 检查本地已有的缓存（png 或 webp）
    for ext in (".png", ".webp"):
        local_path = os.path.join(local_dir, f"{rid}{ext}")
        if os.path.exists(local_path):
            return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))

    # 2) 角色图标：CDN 主源 → encore.moe 备用
    #    武器图标：encore.moe 优先（高分辨率 webp）→ CDN IconWeapon80 兜底（低分辨率 png）
    last_err = None
    if is_char:
        # 角色：CDN 优先
        cdn_url = f"{CDN_CHAR_BASE}/{rid}.png"
        try:
            resp = _download_url(cdn_url)
            if resp.status_code == 200:
                local_path = os.path.join(local_dir, f"{rid}.png")
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
        except Exception as e:
            last_err = f"CDN角色: {e}"
        # CDN 失败 → encore.moe
        encore_url = _encore_icon_map.get(rid)
        if encore_url:
            try:
                resp = _download_url(encore_url)
                if resp.status_code == 200:
                    local_path = os.path.join(local_dir, f"{rid}.webp")
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
            except Exception as e:
                last_err = f"encore角色: {e}"
        else:
            last_err = last_err or "encore角色: 无映射"
    else:
        # 武器：CDN IconWeapon160 优先（高分辨率 png）→ encore.moe 兜底（webp）
        cdn_url = f"{CDN_WEAPON_BASE}/{rid}.png"
        try:
            resp = _download_url(cdn_url)
            data = resp.content
            # CDN 可能返回 HTML 404 页面，需验证是真实 PNG
            if resp.status_code == 200 and data[:4] == b'\x89PNG':
                local_path = os.path.join(local_dir, f"{rid}.png")
                with open(local_path, "wb") as f:
                    f.write(data)
                return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
            else:
                last_err = f"CDN武器: 非PNG响应({resp.status_code})"
        except Exception as e:
            last_err = f"CDN武器: {e}"
        # CDN 失败或返回非图片 → encore.moe 兜底
        encore_url = _encore_icon_map.get(rid)
        if encore_url:
            try:
                resp = _download_url(encore_url)
                if resp.status_code == 200:
                    local_path = os.path.join(local_dir, f"{rid}.webp")
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
            except Exception as e:
                last_err = f"encore武器: {e}"
        else:
            last_err = last_err or "encore武器: 无映射"

    return (resource_id, "", last_err or "未知原因")

def cache_icons(data):
    """从数据中提取全部图标，并行下载缓存"""
    ensure_icon_dirs()
    items = set()
    for key, records in data.items():
        if not isinstance(records, list):
            continue
        for r in records:
            if r.get("resourceId"):
                items.add((r["resourceId"], r.get("resourceType", "")))

    print(f"  图标缓存: {len(items)}个, 并行下载中...")
    icon_map = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download_icon, rid, rtype): rid for rid, rtype in items}
        for future in as_completed(futures):
            result = future.result()
            rid = result[0]
            local_path = result[1]
            if local_path:
                icon_map[rid] = local_path
            else:
                err_reason = result[2] if len(result) > 2 else "未知"
                errors[rid] = err_reason
    success = len(icon_map)
    total = len(items)
    print(f"  图标缓存完成: {success}/{total}个")
    if success == 0 and total > 0:
        print(f"  ⚠ 警告: 所有图标下载失败！可能网络不可达或CDN被屏蔽")
        # 打印最多3个典型错误
        for i, (rid, err) in enumerate(list(errors.items())[:3]):
            print(f"    失败示例 [{rid}]: {err}")
    elif errors:
        print(f"  {len(errors)}个图标下载失败:")
        for rid, err in list(errors.items())[:3]:
            print(f"    [{rid}]: {err}")
        if len(errors) > 3:
            print(f"    ... 共{len(errors)}个失败")
    return icon_map

# ============================================================
# 数据合并（复用 wuwa_gacha.py 的逻辑）
# ============================================================
POOL_TYPE_NAMES = {
    1: "角色活动唤取", 2: "武器活动唤取", 3: "角色常驻唤取",
    4: "武器常驻唤取", 5: "新手唤取", 6: "新手自选唤取",
    7: "感恩定向唤取", 8: "角色新旅唤取", 9: "武器新旅唤取",
    10: "角色联动唤取", 11: "武器联动唤取",
}

POOL_NAME_TO_TYPE = {
    "角色活动唤取": 1, "武器活动唤取": 2,
    "角色常驻唤取": 3, "武器常驻唤取": 4,
    "新手唤取": 5, "新手自选唤取": 6,
    "感恩定向唤取": 7, "角色新旅唤取": 8,
    "武器新旅唤取": 9, "角色联动唤取": 10,
    "武器联动唤取": 11,
    "角色精准调谐": 1, "武器精准调谐": 2,
    "角色调谐（常驻池）": 3, "武器调谐（常驻池）": 4,
    "新手调谐": 5, "自选调谐": 6, "常驻调谐": 7,
}

def normalize_pool_key(pool_key, records=None):
    if pool_key.isdigit():
        return pool_key
    if pool_key in POOL_NAME_TO_TYPE:
        return str(POOL_NAME_TO_TYPE[pool_key])
    if records:
        for r in records[:3]:
            cpt = r.get("cardPoolType", "")
            if isinstance(cpt, str) and cpt in POOL_NAME_TO_TYPE:
                return str(POOL_NAME_TO_TYPE[cpt])
    return pool_key

def _record_key(r):
    return (r.get("time"), r.get("resourceId"), r.get("name"), r.get("qualityLevel"))

def _stable_sort_desc(records):
    """稳定排序：确保时间严格倒序，同时间戳保持原有顺序"""
    indexed = [(i, r) for i, r in enumerate(records)]
    indexed.sort(key=lambda x: (-_time_to_int(x[1].get("time", "")), x[0]))
    return [r for _, r in indexed]

def _time_to_int(t):
    """将时间字符串转为可比较的整数 (YYYYMMDDHHmmss)"""
    try:
        return int(t.replace("-", "").replace(":", "").replace(" ", ""))
    except (ValueError, AttributeError):
        return 0

def _merge_pool(pool_id, old_records, new_records):
    if not new_records:
        # 旧数据原样保留，但确保时间倒序
        return _stable_sort_desc(old_records)
    if not old_records:
        return _stable_sort_desc(new_records)

    new_key_set = set(_record_key(r) for r in new_records)
    oldest_new_time = new_records[-1].get("time", "")

    cut_index = -1
    for i in range(len(old_records) - 1, -1, -1):
        r = old_records[i]
        r_time = r.get("time", "")
        if r_time < oldest_new_time:
            # 时间比new最旧还早，继续往新端找
            # 但其实比new最旧还早的记录肯定不在new里，跳过
            # 我们需要找old中存在于new的最旧一条
            # 所以应该 continue 而不是 break
            continue
        if _record_key(r) in new_key_set:
            cut_index = i
            break

    if cut_index == -1:
        # 无重叠 → 直接拼接后排序
        return _stable_sort_desc(new_records + old_records)

    pure_old = old_records[cut_index + 1:]
    overlap_zone = old_records[:cut_index + 1]
    matched = sum(1 for r in overlap_zone if _record_key(r) in new_key_set)
    overlap_rate = matched / len(overlap_zone) if overlap_zone else 1.0

    if overlap_rate < 0.3:
        # 降级为逐条合并
        merged = list(new_records)
        new_keys = set(_record_key(r) for r in new_records)
        for r in old_records:
            if _record_key(r) not in new_keys:
                merged.append(r)
        return _stable_sort_desc(merged)

    return _stable_sort_desc(new_records + pure_old)

def merge_data(new_data, existing_data):
    """合并新旧抽卡数据（截断+接续）"""
    uid = new_data.get("uid") or existing_data.get("uid", "")
    merged = {"uid": uid}

    all_pool_keys = set()
    for src in [existing_data, new_data]:
        for k, v in src.items():
            if k == "uid" or not isinstance(v, list):
                continue
            norm_key = normalize_pool_key(k, v)
            all_pool_keys.add(norm_key)

    for pool_key in sorted(all_pool_keys, key=lambda x: int(x) if x.isdigit() else 99):
        old_records = []
        for k, v in existing_data.items():
            if k == "uid" or not isinstance(v, list):
                continue
            if normalize_pool_key(k, v) == pool_key:
                old_records.extend(v)

        new_records = []
        for k, v in new_data.items():
            if k == "uid" or not isinstance(v, list):
                continue
            if normalize_pool_key(k, v) == pool_key:
                new_records.extend(v)

        merged_records = _merge_pool(pool_key, old_records, new_records)
        merged[pool_key] = merged_records

    return merged

def count_records(data):
    """统计数据总条数"""
    return sum(len(v) for k, v in data.items() if isinstance(v, list))

# ============================================================
# HTML 模板
# ============================================================

UPLOAD_PAGE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>鸣潮抽卡分析</title>
<style>
/* =============================================
   Fluent 2 Design Token System — Upload Page
   ============================================= */
:root, [data-theme="light"] {
  --colorNeutralBackground1: #ffffff;
  --colorNeutralBackground2: #fafafa;
  --colorNeutralBackground3: #f5f5f5;
  --colorSubtleBackground: #fafafa;
  --colorSubtleBackgroundHover: #f0f0f0;
  --colorNeutralForeground1: #141414;
  --colorNeutralForeground2: #616161;
  --colorNeutralForeground3: #9e9e9e;
  --colorNeutralStroke1: #d1d1d1;
  --colorNeutralStroke2: #e0e0e0;
  --colorNeutralStrokeAccessible: #616161;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandForeground1: #0078d4;
  --colorCompoundBrandBackground: #0f6cbd;
  --colorCompoundBrandBackgroundHover: #115ea3;
  --colorCompoundBrandBackgroundPressed: #0f548c;
  --colorCompoundBrandStroke: #0078d4;
  --colorCompoundBrandForeground: #0078d4;
  --colorNeutralForegroundInverted: #ffffff;
  --shadow4: 0 2px 4px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.08);
}
[data-theme="dark"] {
  --colorNeutralBackground1: #292929;
  --colorNeutralBackground2: #1f1f1f;
  --colorNeutralBackground3: #1a1a1a;
  --colorSubtleBackground: #1f1f1f;
  --colorSubtleBackgroundHover: #292929;
  --colorNeutralForeground1: #f8f8f8;
  --colorNeutralForeground2: #c4c4c4;
  --colorNeutralForeground3: #8a8a8a;
  --colorNeutralStroke1: #4a4a4a;
  --colorNeutralStroke2: #3d3d3d;
  --colorNeutralStrokeAccessible: #adadad;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandForeground1: #62abf5;
  --colorCompoundBrandBackground: #479ef5;
  --colorCompoundBrandBackgroundHover: #62abf5;
  --colorCompoundBrandBackgroundPressed: #2886de;
  --colorCompoundBrandStroke: #62abf5;
  --colorCompoundBrandForeground: #62abf5;
  --colorNeutralForegroundInverted: #242424;
  --shadow4: 0 2px 4px rgba(0,0,0,0.22), 0 4px 12px rgba(0,0,0,0.24);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: var(--colorNeutralBackground3);
  color: var(--colorNeutralForeground1);
  line-height: 1.5; min-height: 100vh;
  transition: background 0.3s ease, color 0.3s ease;
}
.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }

/* --- Header (与分析页统一) --- */
.header {
  background: var(--colorNeutralBackground1);
  border-bottom: 1px solid var(--colorNeutralStroke2);
  padding: 12px 0;
  position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(20px);
  transition: background 0.3s ease;
}
.header-inner { display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 22px; font-weight: 600; color: var(--colorBrandForeground1); }
.header .meta { color: var(--colorNeutralForeground2); font-size: 13px; margin-top: 2px; }
.theme-switch { display: flex; align-items: center; gap: 8px; color: var(--colorNeutralForeground2); font-size: 14px; }
.theme-toggle {
  position: relative; width: 40px; height: 20px;
  background: transparent; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--colorNeutralStrokeAccessible);
  transition: all 0.2s ease; flex-shrink: 0; outline: none;
}
.theme-toggle::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 14px; height: 14px; background: var(--colorNeutralStrokeAccessible);
  border-radius: 50%; transition: all 0.2s ease;
}
.theme-toggle:focus-visible { box-shadow: 0 0 0 2px var(--colorCompoundBrandStroke); }
.theme-toggle:hover { border-color: var(--colorNeutralStrokeAccessible); }
.theme-toggle:hover::after { background: var(--colorNeutralStrokeAccessible); }
.theme-toggle.active { background: var(--colorCompoundBrandBackground); border-color: transparent; }
.theme-toggle.active::after { left: 22px; background: var(--colorNeutralForegroundInverted); }
.theme-toggle.active:hover { background: var(--colorCompoundBrandBackgroundHover); }
.theme-icon { line-height: 1; display: flex; align-items: center; transition: opacity 0.2s; }

/* --- Breadcrumb (Fluent UI 2 实测) --- */
.f2-breadcrumb {
  display: flex; align-items: center; gap: 0;
  font-size: 14px; line-height: 20px; font-weight: 400;
  margin-bottom: 4px;
}
.f2-breadcrumb__item {
  display: flex; align-items: center; justify-content: center;
  padding: 6px; height: 32px;
  color: var(--colorNeutralForeground2);
  text-decoration: none; cursor: pointer;
  border-radius: 4px; border: none; background: transparent;
  transition: background 0.1s, color 0.1s;
}
.f2-breadcrumb__item:hover { color: var(--colorNeutralForeground1); background: var(--colorSubtleBackgroundHover); text-decoration: none; }
.f2-breadcrumb__item--current { color: var(--colorNeutralForeground2); cursor: default; pointer-events: none; }
.f2-breadcrumb__item--current:hover { color: var(--colorNeutralForeground2); background: transparent; }
.f2-breadcrumb__sep { color: var(--colorNeutralForeground1); display: flex; align-items: center; font-size: 16px; padding: 0; margin: 0; }

/* --- 双卡片布局 --- */
.cards-grid {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 16px; margin-top: 24px;
}
@media (max-width: 768px) {
  .cards-grid { grid-template-columns: 1fr; }
}
.input-card {
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px;
  padding: 24px;
  transition: background 0.3s, border-color 0.3s;
}
.input-card__header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 20px;
}
.input-card__icon {
  width: 40px; height: 40px;
  background: var(--colorSubtleBackground);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  color: var(--colorBrandForeground1); flex-shrink: 0;
}
.input-card__title { font-size: 16px; font-weight: 600; color: var(--colorNeutralForeground1); }
.input-card__desc { font-size: 13px; color: var(--colorNeutralForeground2); margin-top: 2px; }

/* --- Upload Zone (文件上传) --- */
.upload-zone {
  border: 2px dashed var(--colorNeutralStroke2);
  border-radius: 8px; padding: 32px 20px;
  cursor: pointer; transition: all 0.2s ease;
  position: relative; background: var(--colorSubtleBackground);
  text-align: center;
}
.upload-zone:hover { border-color: var(--colorCompoundBrandStroke); background: var(--colorSubtleBackgroundHover); }
.upload-zone.dragover { border-color: var(--colorBrandForeground1); background: var(--colorSubtleBackgroundHover); border-style: solid; }
.upload-zone .zone-icon { margin-bottom: 12px; color: var(--colorNeutralForeground3); display: flex; justify-content: center; transition: color 0.2s; }
.upload-zone:hover .zone-icon { color: var(--colorBrandForeground1); }
.upload-zone .zone-text { font-size: 14px; color: var(--colorNeutralForeground2); line-height: 1.5; }
.upload-zone .zone-text strong { color: var(--colorBrandForeground1); font-weight: 600; }
.upload-zone .zone-hint { font-size: 12px; color: var(--colorNeutralForeground3); margin-top: 8px; }
.upload-zone input[type="file"] { position: absolute; top: 0; left: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }

/* --- InfoLabel + Textarea + Primary Button --- */
.f2-infolabel {
  display: flex; align-items: center; gap: 4px;
  margin-bottom: 8px; font-size: 14px;
  font-weight: 600; color: var(--colorNeutralForeground1); line-height: 20px;
}
.f2-infolabel__info {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; cursor: pointer;
  color: var(--colorNeutralForeground3); border-radius: 4px; border: none; background: none;
  padding: 0; transition: color 0.1s, background 0.1s;
}
.f2-infolabel__info:hover { color: var(--colorNeutralForeground2); background: var(--colorSubtleBackgroundHover); }
.f2-infolabel__popover {
  display: none; position: absolute; bottom: calc(100% + 8px);
  left: 50%; transform: translateX(-50%);
  background: var(--colorNeutralBackground1); border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px; box-shadow: var(--shadow4);
  padding: 12px 16px; font-size: 13px; font-weight: 400;
  color: var(--colorNeutralForeground2); line-height: 1.5;
  width: max-content; max-width: 320px; z-index: 100;
  white-space: normal;
}
.f2-infolabel__popover::before {
  content: ''; position: absolute; bottom: -4px;
  left: 50%; transform: translateX(-50%) rotate(45deg);
  width: 8px; height: 8px; background: var(--colorNeutralBackground1);
  border-right: 1px solid var(--colorNeutralStroke2);
  border-bottom: 1px solid var(--colorNeutralStroke2);
}
.f2-infolabel__info.open .f2-infolabel__popover { display: block; }
.f2-textarea {
  width: 100%; resize: vertical; min-height: 88px;
  padding: 6px 12px; font-family: inherit; font-size: 14px; line-height: 20px;
  color: var(--colorNeutralForeground1); background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke1);
  border-bottom: 2px solid var(--colorNeutralStrokeAccessible);
  border-radius: 4px; outline: none; transition: border-color 0.1s;
}
.f2-textarea::placeholder { color: var(--colorNeutralForeground3); }
.f2-textarea:hover { border-bottom-color: var(--colorNeutralStrokeAccessibleHover); }
.f2-textarea:focus { border-bottom-color: var(--colorCompoundBrandStroke); }
.f2-textarea:focus-visible { border-bottom-color: var(--colorCompoundBrandStroke); }
.cred-actions { display: flex; justify-content: flex-start; margin-top: 12px; }
.f2-btn-primary {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: 32px; min-width: 80px; padding: 0 16px;
  border: none; border-radius: 4px;
  font-family: inherit; font-size: 14px; font-weight: 600; line-height: 1;
  cursor: pointer; background: var(--colorBrandBackground); color: #ffffff;
  transition: background 0.1s, box-shadow 0.1s;
}
.f2-btn-primary svg { width: 16px; height: 16px; flex-shrink: 0; }
.f2-btn-primary span { line-height: 1; }
.f2-btn-primary:hover { background: var(--colorBrandBackgroundHover); }
.f2-btn-primary:active { background: var(--colorBrandBackgroundPressed); }
.f2-btn-primary:focus-visible { box-shadow: 0 0 0 2px var(--colorNeutralBackground1), 0 0 0 4px var(--colorCompoundBrandStroke); outline: none; }
.f2-btn-primary:disabled { background: var(--colorNeutralBackground2); color: var(--colorNeutralForeground3); cursor: not-allowed; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.spin-anim { animation: spin 1s linear infinite; }

/* --- Toast (与分析页统一) --- */
.toast-container { position: fixed; bottom: 16px; right: 20px; width: 292px; pointer-events: none; z-index: 9999; }
.toast {
  pointer-events: all; display: grid; grid-template-columns: auto 1fr auto;
  padding: 12px; border-radius: 4px; border: 1px solid transparent;
  box-shadow: 0 4px 8px rgba(0,0,0,0.14), 0 0 2px rgba(0,0,0,0.12);
  background: var(--colorNeutralBackground1); color: var(--colorNeutralForeground1);
  font-size: 14px; line-height: 20px; margin-top: 16px;
  animation: toast-in 0.25s cubic-bezier(0.4,0,0.2,1) forwards;
}
[data-theme="dark"] .toast { background: #292929; color: #e0e0e0; }
.toast__media { display: flex; padding-top: 2px; padding-right: 8px; font-size: 16px; align-items: flex-start; }
.toast__content { grid-column: 2 / 3; min-width: 0; }
.toast__title { font-weight: 600; word-break: break-word; }
.toast__body { padding-top: 4px; font-weight: 400; font-size: 14px; color: var(--colorNeutralForeground2); word-break: break-word; }
[data-theme="dark"] .toast__body { color: #c4c4c4; }
.toast__close { grid-column: 3; display: flex; align-items: flex-start; padding-left: 12px; background: none; border: none; cursor: pointer; color: var(--colorNeutralForeground3); padding: 0; font-size: 16px; line-height: 1; }
.toast__close:hover { color: var(--colorNeutralForeground1); }
.toast--success .toast__media { color: #0f7b0f; }
[data-theme="dark"] .toast--success .toast__media { color: #9edcab; }
.toast--error .toast__media { color: #d13438; }
[data-theme="dark"] .toast--error .toast__media { color: #f5a5ae; }
.toast--warning .toast__media { color: #9d5d00; }
[data-theme="dark"] .toast--warning .toast__media { color: #f7c67f; }
.toast--info .toast__media { color: #616161; }
[data-theme="dark"] .toast--info .toast__media { color: #e0e0e0; }
.toast--exit { animation: toast-out 0.2s cubic-bezier(0.4,0,0.2,1) forwards; }
@keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@keyframes toast-out { from { opacity: 1; transform: translateY(0); } to { opacity: 0; transform: translateY(-4px); } }

/* ===== Breadcrumb bar (below header) ===== */
.breadcrumb-bar { background: var(--colorNeutralBackground2); border-bottom: 1px solid var(--colorNeutralStroke2); padding: 6px 0; }

/* ===== Carousel — Fluent UI 2 Appearance (inverted) ===== */
.carousel { border-radius: 8px; overflow: hidden; position: relative; margin-top: 20px; }

/* Top nav bar — Fluent UI 2 style, above image */
.carousel__topnav { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: var(--colorNeutralBackground2); border-bottom: 1px solid var(--colorNeutralStroke2); color: var(--colorNeutralForeground2); }
.carousel__topnav-title { font-size: 13px; font-weight: 600; color: var(--colorNeutralForeground1); margin-right: 8px; white-space: nowrap; }
.carousel__topnav-pager { font-size: 12px; color: var(--colorNeutralForeground3); min-width: 36px; text-align: center; }
.carousel__topnav-spacer { flex: 1; }
.carousel__topnav-dots { display: flex; gap: 6px; align-items: center; }
.carousel__topnav-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--colorNeutralForeground3); border: none; cursor: pointer; padding: 0; transition: all 0.3s; }
.carousel__topnav-dot.active { background: var(--colorCompoundBrandBackground); width: 20px; border-radius: 3px; }
.carousel__topnav-btn { width: 28px; height: 28px; border-radius: 4px; border: 1px solid var(--colorNeutralStroke2); background: var(--colorSubtleBackground); color: var(--colorNeutralForeground2); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; transition: all 0.15s; padding: 0; }
.carousel__topnav-btn:hover { background: var(--colorSubtleBackgroundHover); border-color: var(--colorNeutralStroke1); }

/* Slider — CSS transition for smooth animation */
.carousel__viewport { position: relative; width: 100%; overflow: hidden; }
.carousel__slide { position: absolute; top: 0; left: 0; width: 100%; opacity: 0; transition: opacity 0.5s ease; pointer-events: none; }
.carousel__slide.active { opacity: 1; pointer-events: auto; z-index: 1; position: relative; }

/* Slide: full-width image + overlay info card */
.carousel__slide-inner { position: relative; width: 100%; background: var(--colorNeutralBackground3); }
.carousel__slide-inner img { width: 100%; height: auto; display: block; }

/* Overlay info at bottom-left — compact, no full-width grey band */
.carousel__overlay { position: absolute; left: 0; right: 0; bottom: 0; padding: 20px 24px 16px; background: linear-gradient(to top, rgba(0,0,0,0.72) 0%, rgba(0,0,0,0.35) 60%, transparent 100%); color: #fff; }
.carousel__overlay-tag { font-size: 11px; font-weight: 600; color: #62abf5; letter-spacing: 0.5px; margin-bottom: 4px; text-transform: uppercase; }
.carousel__overlay-title { font-size: 16px; font-weight: 600; line-height: 1.4; margin-bottom: 6px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.carousel__overlay-footer { display: flex; align-items: center; gap: 14px; }
.carousel__overlay-date { font-size: 12px; color: rgba(255,255,255,0.65); }
.carousel__overlay-link { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 8px; border-radius: 4px; font-size: 12px; font-weight: 600; line-height: 1; color: #fff; background: rgba(255,255,255,0.12); text-decoration: none; border: 1px solid rgba(255,255,255,0.2); backdrop-filter: blur(4px); transition: background 0.15s; }
.carousel__overlay-link:hover { background: rgba(255,255,255,0.22); }
.carousel__overlay-link svg { flex-shrink: 0; }

/* Responsive */
@media (max-width: 768px) {
  .carousel__overlay { padding: 14px 16px 12px; }
  .carousel__overlay-title { font-size: 14px; }
  .carousel__topnav-title { display: none; }
}

@keyframes carousel-loading { from { background-position: -200% 0; } to { background-position: 200% 0; } }
.carousel--loading .carousel__viewport { background: linear-gradient(90deg, var(--colorNeutralBackground3) 25%, var(--colorNeutralBackground2) 50%, var(--colorNeutralBackground3) 75%); background-size: 200% 100%; animation: carousel-loading 1.5s infinite; min-height: 180px; display: flex; align-items: center; justify-content: center; }
.carousel--loading .carousel__loading-text { color: var(--colorNeutralForeground3); font-size: 14px; }

/* Official List — 资讯/公告列表 */
.official-list { margin-top: 20px; background: var(--colorNeutralBackground1); border-radius: 8px; border: 1px solid var(--colorNeutralStroke2); overflow: hidden; }
.official-list__tabs { display: flex; border-bottom: 1px solid var(--colorNeutralStroke2); padding: 0 12px; background: var(--colorNeutralBackground2); }
.official-list__tab { padding: 10px 16px; font-size: 13px; font-weight: 600; color: var(--colorNeutralForeground3); cursor: pointer; border: none; background: none; border-bottom: 2px solid transparent; transition: all 0.15s; line-height: 1; }
.official-list__tab:hover { color: var(--colorNeutralForeground1); }
.official-list__tab.active { color: var(--colorCompoundBrandForeground1); border-bottom-color: var(--colorCompoundBrandBackground); }
.official-list__grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; padding: 12px; min-height: 200px; }
.official-list__card { display: flex; flex-direction: column; border-radius: 6px; border: 1px solid var(--colorNeutralStroke2); background: var(--colorNeutralBackground2); overflow: hidden; cursor: pointer; transition: all 0.15s; text-decoration: none; color: inherit; }
.official-list__card:hover { border-color: var(--colorNeutralStroke1); background: var(--colorSubtleBackgroundHover); box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.official-list__card-img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; background: var(--colorNeutralBackground3); }
.official-list__card-body { padding: 10px 12px; flex: 1; display: flex; flex-direction: column; gap: 6px; }
.official-list__card-title { font-size: 13px; font-weight: 600; color: var(--colorNeutralForeground1); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.official-list__card-date { font-size: 11px; color: var(--colorNeutralForeground3); margin-top: auto; }
.official-list__empty { grid-column: 1 / -1; text-align: center; padding: 40px 0; color: var(--colorNeutralForeground3); font-size: 14px; }
.official-list__loader { grid-column: 1 / -1; text-align: center; padding: 16px 0; color: var(--colorNeutralForeground3); font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.official-list__loader.hidden { display: none; }
.official-list__spin { width: 16px; height: 16px; border: 2px solid var(--colorNeutralStroke2); border-top-color: var(--colorCompoundBrandBackground); border-radius: 50%; animation: official-spin 0.8s linear infinite; }
@keyframes official-spin { to { transform: rotate(360deg); } }
.official-list__card-skeleton { aspect-ratio: 16/9; background: linear-gradient(90deg, var(--colorNeutralBackground3) 25%, var(--colorNeutralBackground2) 50%, var(--colorNeutralBackground3) 75%); background-size: 200% 100%; animation: carousel-loading 1.5s infinite; }
@media (max-width: 768px) { .official-list__grid { grid-template-columns: 1fr; } }

/* Footer */
.site-footer { padding: 16px 0; text-align: center; font-size: 12px; color: var(--colorNeutralForeground3); display: flex; align-items: center; justify-content: center; gap: 12px; flex-wrap: wrap; }
.site-footer a { color: var(--colorNeutralForeground2); text-decoration: none; display: inline-flex; align-items: center; gap: 4px; transition: color 0.15s; }
.site-footer a:hover { color: var(--colorCompoundBrandForeground1); text-decoration: underline; }
.site-footer .sep { color: var(--colorNeutralForeground3); opacity: 0.4; }
</style>
</head>
<body>

<div class="header">
  <div class="container header-inner">
    <div>
      <h1>鸣潮抽卡分析</h1>
      <div class="meta">导入抽卡记录，查看详细分析报告</div>
    </div>
    <div class="theme-switch">
      <span class="theme-icon"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm0 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-1a3 3 0 1 1 0-6 3 3 0 0 1 0 6Zm7.5-2.5a.5.5 0 0 0 0-1h-1a.5.5 0 0 0 0 1h1ZM10 16c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm-6.5-5.5a.5.5 0 0 0 0-1H2.46a.5.5 0 0 0 0 1H3.5Zm.65-6.35c.2-.2.5-.2.7 0l1 1a.5.5 0 1 1-.7.7l-1-1a.5.5 0 0 1 0-.7Zm.7 11.7a.5.5 0 0 1-.7-.7l1-1a.5.5 0 0 1 .7.7l-1 1Zm11-11.7a.5.5 0 0 0-.7 0l-1 1a.5.5 0 0 0 .7.7l1-1a.5.5 0 0 0 0-.7Zm-.7 11.7a.5.5 0 0 0 .7-.7l-1-1a.5.5 0 0 0-.7.7l1 1Z"/></svg></span>
      <div class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" role="switch" tabindex="0" aria-label="切换深浅主题"></div>
      <span class="theme-icon"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M15.5 13.5A6.98 6.98 0 0 1 4 14.39c2.83-1.09 4.56-2.42 5.6-4.4 1.04-2 1.33-4.16.75-6.9A6.98 6.98 0 0 1 15.5 13.5ZM5.45 16.92A7.98 7.98 0 1 0 9.88 2.04a.6.6 0 0 0-.61.73c.69 2.82.43 4.88-.55 6.76-.94 1.78-2.55 3.03-5.55 4.1a.6.6 0 0 0-.3.9 7.95 7.95 0 0 0 2.59 2.39Z"/></svg></span>
    </div>
  </div>
</div>

<div class="breadcrumb-bar">
  <div class="container">
    <nav class="f2-breadcrumb" aria-label="面包屑导航">
      <a class="f2-breadcrumb__item f2-breadcrumb__item--current" aria-current="page">上传数据</a>
    </nav>
  </div>
</div>

<div class="container">
  <div class="cards-grid">

    <!-- 卡片1：上传文件 -->
    <div class="input-card">
      <div class="input-card__header">
        <div class="input-card__icon">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2V6.5C10 7.32843 10.6716 8 11.5 8H16V16.5C16 17.3284 15.3284 18 14.5 18H9.74284C10.5282 17.0491 11 15.8296 11 14.5C11 11.4624 8.53757 9 5.5 9C4.97999 9 4.47683 9.07217 4 9.20703V3.5C4 2.67157 4.67157 2 5.5 2H10ZM11 2.25V6.5C11 6.77614 11.2239 7 11.5 7H15.75L11 2.25ZM5.5 19C7.98528 19 10 16.9853 10 14.5C10 12.0147 7.98528 10 5.5 10C3.01472 10 1 12.0147 1 14.5C1 16.9853 3.01472 19 5.5 19ZM7.85353 14.1465C8.04879 14.3418 8.04879 14.6583 7.85353 14.8536C7.65826 15.0489 7.34168 15.0489 7.14642 14.8536L5.99997 13.7072L5.99997 16.5001C5.99997 16.7762 5.77612 17.0001 5.49997 17.0001C5.22383 17.0001 4.99997 16.7762 4.99997 16.5001L4.99997 13.7072L3.85353 14.8536C3.65826 15.0489 3.34168 15.0489 3.14642 14.8536C2.95116 14.6583 2.95116 14.1465 3.14642 14.1465L5.14642 12.1465C5.19436 12.0986 5.24961 12.0624 5.30858 12.038C5.36666 12.0139 5.43027 12.0005 5.49697 12.0001L5.49997 12.0001L5.50297 12.0001C5.56967 12.0005 5.63328 12.0139 5.69136 12.038C5.74947 12.062 5.80396 12.0975 5.8514 12.1444L5.85392 12.1469L7.85353 14.1465Z"/></svg>
        </div>
        <div>
          <div class="input-card__title">上传 JSON 文件</div>
          <div class="input-card__desc">上传已导出的抽卡记录文件</div>
        </div>
      </div>
      <div class="upload-zone" id="upload-zone">
        <div class="zone-icon"><svg width="32" height="32" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2V6.5C10 7.32843 10.6716 8 11.5 8H16V16.5C16 17.3284 15.3284 18 14.5 18H9.74284C10.5282 17.0491 11 15.8296 11 14.5C11 11.4624 8.53757 9 5.5 9C4.97999 9 4.47683 9.07217 4 9.20703V3.5C4 2.67157 4.67157 2 5.5 2H10ZM11 2.25V6.5C11 6.77614 11.2239 7 11.5 7H15.75L11 2.25ZM5.5 19C7.98528 19 10 16.9853 10 14.5C10 12.0147 7.98528 10 5.5 10C3.01472 10 1 12.0147 1 14.5C1 16.9853 3.01472 19 5.5 19ZM7.85353 14.1465C8.04879 14.3418 8.04879 14.6583 7.85353 14.8536C7.65826 15.0489 7.34168 15.0489 7.14642 14.8536L5.99997 13.7072L5.99997 16.5001C5.99997 16.7762 5.77612 17.0001 5.49997 17.0001C5.22383 17.0001 4.99997 16.7762 4.99997 16.5001L4.99997 13.7072L3.85353 14.8536C3.65826 15.0489 3.34168 15.0489 3.14642 14.8536C2.95116 14.6583 2.95116 14.1465 3.14642 14.1465L5.14642 12.1465C5.19436 12.0986 5.24961 12.0624 5.30858 12.038C5.36666 12.0139 5.43027 12.0005 5.49697 12.0001L5.49997 12.0001L5.50297 12.0001C5.56967 12.0005 5.63328 12.0139 5.69136 12.038C5.74947 12.062 5.80396 12.0975 5.8514 12.1444L5.85392 12.1469L7.85353 14.1465Z"/></svg></div>
        <div class="zone-text">拖拽文件到此处，或 <strong>点击选择文件</strong></div>
        <div class="zone-hint">支持 .json 格式</div>
        <input type="file" id="file-input" accept=".json" onchange="handleFile(this.files[0])">
      </div>
    </div>

    <!-- 卡片2：凭证抓取 -->
    <div class="input-card">
      <div class="input-card__header">
        <div class="input-card__icon">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2C12.8166 2 14.4145 3.92329 14.6469 6.24599L14.7179 6.24599C16.5306 6.24599 18 7.75792 18 9.62299C18 9.71829 17.9962 9.81267 17.9886 9.90598C16.9349 8.5916 15.3157 7.75 13.5 7.75C10.4928 7.75 8.02481 10.0585 7.77144 13H5.28205C3.46942 13 2 11.4881 2 9.62299C2 7.75792 3.46942 6.24599 5.28207 6.24599L5.35314 6.24599C5.58687 3.90802 7.18335 2 10 2ZM13.5 18C11.0147 18 9 15.9853 9 13.5C9 11.0147 11.0147 9 13.5 9C15.9853 9 18 11.0147 18 13.5C18 15.9853 15.9853 18 13.5 18ZM15.1023 13.1023L14 14.2045V11.5C14 11.2239 13.7761 11 13.5 11C13.2239 11 13 11.2239 13 11.5V14.2045L11.8977 13.1023C11.6781 12.8826 11.3219 12.8826 11.1023 13.1023C10.8826 13.3219 10.8826 13.6781 11.1023 13.8977L13.1023 15.8977C13.3219 16.1174 13.6781 16.1174 13.8977 15.8977L15.8977 13.8977C16.1174 13.6781 16.1174 13.3219 15.8977 13.1023C15.6781 12.8826 15.3219 12.8826 15.1023 13.1023Z"/></svg>
        </div>
        <div>
          <div class="input-card__title">提取游戏记录</div>
          <div class="input-card__desc">输入凭证直接从服务器抓取</div>
        </div>
      </div>
      <label class="f2-infolabel">
        JSON 凭证
        <button class="f2-infolabel__info" type="button" onclick="toggleInfoPopover(this)" aria-label="查看凭证格式说明">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm.5 5.5a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM10 9a.5.5 0 0 1 .5.41V14.59a.5.5 0 0 1-1 0V9.41A.5.5 0 0 1 10 9Z"/></svg>
          <div class="f2-infolabel__popover">从游戏日志提取的 JSON 凭证，需含 recordId、playerId、serverId、cardPoolId 四个字段</div>
        </button>
      </label>
      <textarea class="f2-textarea" id="cred-input" rows="4" placeholder='粘贴 JSON 凭证，如 {"recordId":"...","playerId":"...","serverId":"...","cardPoolId":"..."}'></textarea>
      <div class="cred-actions">
        <button class="f2-btn-primary" id="fetch-btn" onclick="handleFetch()">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 3C6.34315 3 5 4.34315 5 6C5 6.27614 4.77614 6.5 4.5 6.5H4.25C3.00736 6.5 2 7.50736 2 8.75C2 9.99264 3.00736 11 4.25 11H5.02242C5.05337 11.3434 5.11588 11.6777 5.20703 12H4.25C2.45507 12 1 10.5449 1 8.75C1 7.029 2.33769 5.62043 4.03004 5.50733C4.27283 3.53062 5.95767 2 8 2C9.92958 2 11.54 3.36628 11.9167 5.1842C11.5678 5.09145 11.2053 5.03214 10.8328 5.0099C10.4237 3.83954 9.30992 3 8 3ZM15 10.5C15 12.9853 12.9853 15 10.5 15C8.01472 15 6 12.9853 6 10.5C6 8.01472 8.01472 6 10.5 6C12.9853 6 15 8.01472 15 10.5ZM10.146 12.8532L10.1486 12.8557C10.196 12.9026 10.2505 12.938 10.3086 12.9621C10.3667 12.9861 10.4303 12.9996 10.497 13L10.5 13L10.503 13C10.5697 12.9996 10.6333 12.9861 10.6914 12.9621C10.7504 12.9377 10.8056 12.9015 10.8536 12.8536L12.8536 10.8536C13.0488 10.6583 13.0488 10.3417 12.8536 10.1464C12.6583 9.95118 12.3417 9.95118 12.1464 10.1464L11 11.2929V8.5C11 8.22386 10.7761 8 10.5 8C10.2239 8 10 8.22386 10 8.5V11.2929L8.85355 10.1464C8.65829 9.95118 8.34171 9.95118 8.14645 10.1464C7.95118 10.3417 7.95118 10.6583 8.14645 10.8536L10.146 12.8532Z"/></svg>
          <span>抓取记录</span>
        </button>
      </div>
    </div>

  </div>

  <!-- 资讯轮播 -->
  <div class="carousel carousel--loading" id="news-carousel">
    <div class="carousel__viewport">
      <span class="carousel__loading-text">资讯加载中...</span>
    </div>
  </div>

  <!-- 资讯/公告列表 -->
  <div class="official-list" id="official-list">
    <div class="official-list__tabs">
      <button class="official-list__tab active" data-type="2" onclick="switchOfficialTab(this)">资讯</button>
      <button class="official-list__tab" data-type="3" onclick="switchOfficialTab(this)">公告</button>
    </div>
    <div class="official-list__grid" id="official-grid"></div>
    <div class="official-list__loader hidden" id="official-loader"><div class="official-list__spin"></div><span>加载中...</span></div>
  </div>

</div>

<div class="toast-container" id="toast-container"></div>

<script>
const storageKey = 'wuwa-theme';
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const toggle = document.getElementById('theme-toggle');
  if (toggle) toggle.classList.toggle('active', theme === 'dark');
}
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem(storageKey, next);
  applyTheme(next);
}
const saved = localStorage.getItem(storageKey);
applyTheme(saved || (mediaQuery.matches ? 'dark' : 'light'));
mediaQuery.addEventListener('change', e => { if (!localStorage.getItem(storageKey)) applyTheme(e.matches ? 'dark' : 'light'); });

// Drag & drop
const zone = document.getElementById('upload-zone');
zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('dragover'); if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]); });

const TOAST_ICONS = {
  success: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm3.36 6.65-3.75 4.5a.5.5 0 0 1-.72.04l-2.25-2a.5.5 0 1 1 .66-.76l1.87 1.66 3.42-4.1a.5.5 0 0 1 .77.66Z"/></svg>',
  error: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.86 3.15a.5.5 0 0 0-.71.7L9.29 10l-6.14 6.15a.5.5 0 0 0 .7.7L10 10.72l6.15 6.14a.5.5 0 0 0 .7-.71L10.72 10l6.14-6.15a.5.5 0 0 0-.7-.7L10 9.29 3.86 3.14Z"/></svg>',
  warning: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm0 11.5a.75.75 0 1 1 0 1.5.75.75 0 0 1 0-1.5ZM10 6a.5.5 0 0 1 .5.41V11.59a.5.5 0 0 1-1 0V6.41A.5.5 0 0 1 10 6Z"/></svg>',
  info: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm.5 5.5a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM10 9a.5.5 0 0 1 .5.41V14.59a.5.5 0 0 1-1 0V9.41A.5.5 0 0 1 10 9Z"/></svg>'
};
function addToast(intent, title, body, duration) {
  const container = document.getElementById('toast-container');
  // Keep at most 3 toasts — dismiss oldest if exceeding
  const existing = container.querySelectorAll('.toast:not(.toast--exit)');
  if (existing.length >= 3) dismissToast(existing[0]);
  const el = document.createElement('div');
  el.className = 'toast toast--' + intent;
  el.innerHTML = '<div class="toast__media">' + (TOAST_ICONS[intent] || TOAST_ICONS.info) + '</div><div class="toast__content"><div class="toast__title">' + title + '</div>' + (body ? '<div class="toast__body">' + body + '</div>' : '') + '</div><button class="toast__close" onclick="dismissToast(this.parentElement)"><svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="m4.09 4.22.06-.07a.5.5 0 0 1 .63-.06l.07.06L10 9.29l5.15-5.14a.5.5 0 0 1 .63-.06l.07.06c.18.17.2.44.06.63l-.06.07L10.71 10l5.14 5.15c.18.17.2.44.06.63l-.06.07a.5.5 0 0 1-.63.06l-.07-.06L10 10.71l-5.15 5.14a.5.5 0 0 1-.63.06l-.07-.06a.5.5 0 0 1-.06-.63l.06-.07L9.29 10 4.15 4.85a.5.5 0 0 1-.06-.63l.06-.07-.06.07Z"/></svg></button>';
  container.appendChild(el);
  const timeout = duration !== undefined ? duration : 3000;
  if (timeout > 0) setTimeout(() => dismissToast(el), timeout);
  return el;
}
function showToast(intent, title, body, duration) { addToast(intent, title, body, duration); }
function dismissToast(el) { if (!el || el.classList.contains('toast--exit')) return; el.classList.add('toast--exit'); setTimeout(() => el.remove(), 200); }

function handleFile(file) {
  if (!file) return;
  showToast('info', '正在上传', file.name);
  const formData = new FormData();
  formData.append('file', file);
  fetch('/api/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      if (data.ok) { showToast('success', '上传成功', '正在跳转分析页...', 3000); setTimeout(() => window.location.href = '/analysis', 800); }
      else { showToast('error', '上传失败', data.error || '未知错误', 5000); }
    })
    .catch(err => { showToast('error', '网络错误', err.message, 5000); });
}

function toggleInfoPopover(btn) {
  btn.classList.toggle('open');
  const closeHandler = (e) => { if (!btn.contains(e.target)) { btn.classList.remove('open'); document.removeEventListener('click', closeHandler); } };
  if (btn.classList.contains('open')) setTimeout(() => document.addEventListener('click', closeHandler), 0);
}

// Cloud Arrow Down 官方图标 SVG (用于按钮恢复状态)
const CLOUD_ARROW_DOWN_SVG = '<svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2C12.8166 2 14.4145 3.92329 14.6469 6.24599L14.7179 6.24599C16.5306 6.24599 18 7.75792 18 9.62299C18 9.71829 17.9962 9.81267 17.9886 9.90598C16.9349 8.5916 15.3157 7.75 13.5 7.75C10.4928 7.75 8.02481 10.0585 7.77144 13H5.28205C3.46942 13 2 11.4881 2 9.62299C2 7.75792 3.46942 6.24599 5.28207 6.24599L5.35314 6.24599C5.58687 3.90802 7.18335 2 10 2ZM13.5 18C11.0147 18 9 15.9853 9 13.5C9 11.0147 11.0147 9 13.5 9C15.9853 9 18 11.0147 18 13.5C18 15.9853 15.9853 18 13.5 18ZM15.1023 13.1023L14 14.2045V11.5C14 11.2239 13.7761 11 13.5 11C13.2239 11 13 11.2239 13 11.5V14.2045L11.8977 13.1023C11.6781 12.8826 11.3219 12.8826 11.1023 13.1023C10.8826 13.3219 10.8826 13.6781 11.1023 13.8977L13.1023 15.8977C13.3219 16.1174 13.6781 16.1174 13.8977 15.8977L15.8977 13.8977C16.1174 13.6781 16.1174 13.3219 15.8977 13.1023C15.6781 12.8826 15.3219 12.8826 15.1023 13.1023Z"/></svg>';

function handleFetch() {
  const input = document.getElementById('cred-input').value.trim();
  const btn = document.getElementById('fetch-btn');
  if (!input) { showToast('warning', '请输入凭证', 'JSON 凭证不能为空', 5000); return; }
  let creds;
  try { creds = JSON.parse(input); } catch (e) { showToast('error', '凭证格式错误', '请输入有效的 JSON 格式', 5000); return; }
  const required = ['recordId', 'playerId', 'serverId', 'cardPoolId'];
  const missing = required.filter(f => !creds[f]);
  if (missing.length) { showToast('error', '凭证字段缺失', '缺少: ' + missing.join(', '), 5000); return; }
  btn.disabled = true;
  btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" class="spin-anim"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm0 1a7 7 0 0 1 7 7H3a7 7 0 0 1 7-7Z"/></svg> 抓取中...';
  showToast('info', '凭证解析成功', '玩家 ' + creds.playerId, 4000);
  const svr_area = creds.serverId.startsWith('2') ? 'global' : 'cn';
  let lastProgressToast = null;
  fetch('/api/fetch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ creds: { ...creds, svr_area } }) })
    .then(r => {
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      function read() {
        return reader.read().then(({ done, value }) => {
          if (done) return;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.type === 'progress') {
                const label = '[' + evt.index + '/' + evt.total_pools + '] ' + evt.pool;
                if (lastProgressToast) dismissToast(lastProgressToast);
                lastProgressToast = addToast('info', '正在获取', label, 10000);
              } else if (evt.type === 'result') {
                if (lastProgressToast) { dismissToast(lastProgressToast); lastProgressToast = null; }
                if (evt.count > 0) showToast('success', evt.pool, '获取记录 ' + evt.count + ' 条', 1500);
              } else if (evt.type === 'done') {
                if (lastProgressToast) { dismissToast(lastProgressToast); lastProgressToast = null; }
                btn.disabled = false;
                btn.innerHTML = CLOUD_ARROW_DOWN_SVG + ' 抓取记录';
                showToast('success', '获取成功', '正在跳转分析界面...', 3000);
                fetch('/api/load?file=' + encodeURIComponent(evt.filename))
                  .then(r => r.json())
                  .then(d => { if (d.ok) window.location.href = '/analysis'; })
                  .catch(() => { window.location.href = '/analysis'; });
              } else if (evt.type === 'error') {
                if (lastProgressToast) { dismissToast(lastProgressToast); lastProgressToast = null; }
                btn.disabled = false;
                btn.innerHTML = CLOUD_ARROW_DOWN_SVG + ' 抓取记录';
                showToast('error', '抓取失败', evt.error || '未知错误', 5000);
              }
            } catch (e) {}
          }
          return read();
        });
      }
      return read();
    })
    .catch(err => {
      btn.disabled = false;
      btn.innerHTML = CLOUD_ARROW_DOWN_SVG + ' 抓取记录';
      showToast('error', '网络错误', err.message, 5000);
    });
}

// ===== Carousel — Fluent UI 2 Top Navigation =====
(function initCarousel() {
  const CAROUSEL_INTERVAL = 4000;
  const ARROW_LEFT = '<svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="M12.65 3.15a.5.5 0 0 1 .7.7L7.41 9l5.94 5.15a.5.5 0 0 1-.7.7L7.05 9.35a.5.5 0 0 1 0-.7l5.6-5.5Z"/></svg>';
  const ARROW_RIGHT = '<svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="M7.35 3.15c.2-.2.5-.2.7 0l5.65 5.65a.5.5 0 0 1 0 .7L8.05 15.15a.5.5 0 0 1-.7-.7L12.59 9 7.35 3.85a.5.5 0 0 1 0-.7Z"/></svg>';
  const NAV_ARROW = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2.5 7.5C2.22386 7.5 2 7.72386 2 8C2 8.27614 2.22386 8.5 2.5 8.5L12.197 8.5L8.16552 12.1284C7.96026 12.3131 7.94362 12.6292 8.12835 12.8345C8.31308 13.0397 8.62923 13.0564 8.83448 12.8717L13.8345 8.37165C13.9398 8.27683 14 8.14175 14 8C14 7.85826 13.9398 7.72318 13.8345 7.62836L8.83448 3.12836C8.62923 2.94363 8.31308 2.96027 8.12835 3.16552C7.94362 3.37078 7.96026 3.68692 8.16552 3.87165L12.197 7.5L2.5 7.5Z"/></svg>';
  let currentSlide = 0;
  let autoTimer = null;

  fetch('/api/news')
    .then(r => r.json())
    .then(data => {
      if (!data.ok || !data.news || data.news.length === 0) return;
      const el = document.getElementById('news-carousel');
      el.classList.remove('carousel--loading');
      el.innerHTML = '';
      const total = data.news.length;

      // — Top Navigation bar (CarouselNavContainer) —
      const topnav = document.createElement('div');
      topnav.className = 'carousel__topnav';
      topnav.innerHTML = `
        <span class="carousel__topnav-title">官方资讯</span>
        <span class="carousel__topnav-pager" id="carousel-pager">1/${total}</span>
        <span class="carousel__topnav-spacer"></span>
        <div class="carousel__topnav-dots" id="carousel-dots"></div>
        <button class="carousel__topnav-btn" id="carousel-prev" aria-label="上一条">${ARROW_LEFT}</button>
        <button class="carousel__topnav-btn" id="carousel-next" aria-label="下一条">${ARROW_RIGHT}</button>`;
      el.appendChild(topnav);

      // — Slider (CarouselSlider) —
      const viewport = document.createElement('div');
      viewport.className = 'carousel__viewport';
      data.news.forEach((item, i) => {
        const slide = document.createElement('div');
        slide.className = 'carousel__slide' + (i === 0 ? ' active' : '');
        slide.innerHTML = `
          <div class="carousel__slide-inner">
            <img src="${item.img}" alt="${item.title}" loading="lazy">
            <div class="carousel__overlay">
              <div class="carousel__overlay-tag">资讯</div>
              <div class="carousel__overlay-title">${item.title}</div>
              <div class="carousel__overlay-footer">
                <span class="carousel__overlay-date">${item.date}</span>
                <a class="carousel__overlay-link" href="${item.url}" target="_blank" rel="noopener noreferrer">阅读详情 ${NAV_ARROW}</a>
              </div>
            </div>
          </div>`;
        viewport.appendChild(slide);
      });
      el.appendChild(viewport);

      // — Dot pagination (CarouselNav) —
      const dotsContainer = document.getElementById('carousel-dots');
      const dotBtns = [];
      data.news.forEach((_, i) => {
        const dot = document.createElement('button');
        dot.className = 'carousel__topnav-dot' + (i === 0 ? ' active' : '');
        dot.setAttribute('aria-label', '第' + (i + 1) + '条');
        dot.onclick = () => { goTo(i); resetTimer(); };
        dotsContainer.appendChild(dot);
        dotBtns.push(dot);
      });

      // — Navigation buttons —
      const slides = viewport.querySelectorAll('.carousel__slide');
      const pager = document.getElementById('carousel-pager');

      function goTo(index) {
        slides[currentSlide].classList.remove('active');
        dotBtns[currentSlide].classList.remove('active');
        currentSlide = ((index % total) + total) % total;
        slides[currentSlide].classList.add('active');
        dotBtns[currentSlide].classList.add('active');
        pager.textContent = (currentSlide + 1) + '/' + total;
      }

      document.getElementById('carousel-prev').onclick = () => { goTo(currentSlide - 1); resetTimer(); };
      document.getElementById('carousel-next').onclick = () => { goTo(currentSlide + 1); resetTimer(); };

      function resetTimer() { clearInterval(autoTimer); autoTimer = setInterval(() => goTo(currentSlide + 1), CAROUSEL_INTERVAL); }
      autoTimer = setInterval(() => goTo(currentSlide + 1), CAROUSEL_INTERVAL);

      // Pause on hover
      el.addEventListener('mouseenter', () => clearInterval(autoTimer));
      el.addEventListener('mouseleave', () => resetTimer());
    })
    .catch(() => {
      const el = document.getElementById('news-carousel');
      if (el) { el.classList.remove('carousel--loading'); el.style.display = 'none'; }
    });
})();

// ========== Official List — 资讯/公告列表 ==========
const officialState = { type: '2', page: 1, loading: false, hasMore: true, items: [] };

function switchOfficialTab(btn) {
  const type = btn.getAttribute('data-type');
  if (type === officialState.type) return;
  document.querySelectorAll('.official-list__tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  officialState.type = type;
  officialState.page = 1;
  officialState.hasMore = true;
  officialState.items = [];
  const grid = document.getElementById('official-grid');
  grid.innerHTML = '';
  loadOfficialList();
}

function renderOfficialCard(item) {
  const card = document.createElement('a');
  card.className = 'official-list__card';
  card.href = item.url;
  card.target = '_blank';
  card.rel = 'noopener noreferrer';
  card.innerHTML =
    '<div class="official-list__card-skeleton" data-img="' + item.img + '"></div>' +
    '<div class="official-list__card-body">' +
      '<div class="official-list__card-title">' + item.title + '</div>' +
      '<div class="official-list__card-date">' + item.date + '</div>' +
    '</div>';
  return card;
}

function loadOfficialImg(card) {
  const skeleton = card.querySelector('.official-list__card-skeleton');
  if (!skeleton) return;
  const src = skeleton.getAttribute('data-img');
  if (!src) { skeleton.classList.remove('official-list__card-skeleton'); return; }
  const img = new Image();
  img.onload = function() {
    skeleton.className = 'official-list__card-img';
    skeleton.removeAttribute('data-img');
    skeleton.style.backgroundImage = 'url(' + src + ')';
    skeleton.style.backgroundSize = 'cover';
    skeleton.style.backgroundPosition = 'center top';
  };
  img.onerror = function() {
    skeleton.classList.remove('official-list__card-skeleton');
    skeleton.removeAttribute('data-img');
  };
  img.src = src;
}

function loadOfficialList() {
  if (officialState.loading || !officialState.hasMore) return;
  officialState.loading = true;
  const loader = document.getElementById('official-loader');
  loader.classList.remove('hidden');

  const params = new URLSearchParams({ type: officialState.type, page: String(officialState.page), size: '8' });
  fetch('/api/official?' + params.toString())
    .then(r => r.json())
    .then(data => {
      officialState.loading = false;
      loader.classList.add('hidden');
      if (!data.ok) return;
      const grid = document.getElementById('official-grid');
      data.list.forEach(item => {
        const card = renderOfficialCard(item);
        grid.appendChild(card);
        loadOfficialImg(card);
        officialState.items.push(item);
      });
      officialState.hasMore = data.hasMore;
      if (officialState.items.length === 0) {
        grid.innerHTML = '<div class="official-list__empty">暂无内容</div>';
        officialState.hasMore = false;
      }
    })
    .catch(() => {
      officialState.loading = false;
      loader.classList.add('hidden');
    });
}

// Scroll-based infinite load
function checkOfficialScroll() {
  if (officialState.loading || !officialState.hasMore) return;
  const container = document.getElementById('official-list');
  if (!container) return;
  const rect = container.getBoundingClientRect();
  if (rect.bottom < window.innerHeight + 200) {
    officialState.page++;
    loadOfficialList();
  }
}
window.addEventListener('scroll', checkOfficialScroll, { passive: true });
window.addEventListener('touchmove', checkOfficialScroll, { passive: true });

// Initial load
setTimeout(() => { loadOfficialList(); }, 100);
</script>
<footer class="site-footer">
  <a href="https://github.com/BJY-STUDIO/wuwa-gacha-analyzer" target="_blank" rel="noopener noreferrer"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg> BJY-STUDIO</a>
  <span class="sep">|</span>
  <a href="https://bjy-studio.github.io/" target="_blank" rel="noopener noreferrer">Blog</a>
</footer>
</body>
</html>"""
# 分析页模板 — CSS/JS 逻辑与原 gacha_report.py 一致，但数据通过 fetch 获取
ANALYSIS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>鸣潮抽卡分析</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fluentui/web-components@2/dist/web-components.min.css" onerror="this.remove()">
<script type="module" src="https://cdn.jsdelivr.net/npm/@fluentui/web-components@2/dist/web-components.min.js"></script>
<style>
/* =============================================
   Fluent 2 Design Token System
   ============================================= */
:root, [data-theme="light"] {
  --colorNeutralBackground1: #ffffff;
  --colorNeutralBackground1Hover: #f5f5f5;
  --colorNeutralBackground1Pressed: #e0e0e0;
  --colorNeutralBackground2: #fafafa;
  --colorNeutralBackground2Hover: #f0f0f0;
  --colorNeutralBackground3: #f5f5f5;
  --colorNeutralBackground3Hover: #ebebeb;
  --colorNeutralBackground4: #f0f0f0;
  --colorNeutralBackground5: #ebebeb;
  --colorNeutralBackground6: #e0e0e0;
  --colorSubtleBackground: #fafafa;
  --colorSubtleBackgroundHover: #f0f0f0;
  --colorNeutralForeground1: #141414;
  --colorNeutralForeground1Hover: #242424;
  --colorNeutralForeground2: #616161;
  --colorNeutralForeground2Hover: #717171;
  --colorNeutralForeground3: #9e9e9e;
  --colorNeutralForeground4: #b3b3b3;
  --colorNeutralForegroundDisabled: #bdbdbd;
  --colorNeutralStroke1: #d1d1d1;
  --colorNeutralStroke1Hover: #c4c4c4;
  --colorNeutralStroke2: #e0e0e0;
  --colorNeutralStroke3: #ebebeb;
  --colorNeutralStrokeAccessible: #616161;
  --colorNeutralStrokeAccessibleHover: #575757;
  --colorNeutralStrokeAccessiblePressed: #4d4d4d;
  --colorCompoundBrandStroke: #0078d4;
  --colorCompoundBrandForeground: #0078d4;
  --colorCompoundBrandBackground: #0f6cbd;
  --colorCompoundBrandBackgroundHover: #115ea3;
  --colorCompoundBrandBackgroundPressed: #0f548c;
  --colorNeutralForegroundInverted: #ffffff;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandBackground2: #deecf9;
  --colorBrandForeground1: #0078d4;
  --colorBrandForeground2: #106ebe;
  --shadow2: 0 1px 2px rgba(0,0,0,0.10), 0 2px 6px rgba(0,0,0,0.06);
  --shadow4: 0 2px 4px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.08);
  --shadow8: 0 4px 8px rgba(0,0,0,0.10), 0 8px 24px rgba(0,0,0,0.10);
  --colorGold: #d4a017; --colorGoldSubtle: #fef6e0; --colorGoldText: #8a6914;
  --colorPurple: #8764b8; --colorPurpleSubtle: #f3eaf9; --colorPurpleText: #6b3f9e;
  --colorRed: #d13438; --colorRedSubtle: #fde7e9; --colorRedText: #a4262c;
  --colorGreen: #107c10; --colorGreenSubtle: #dff6dd; --colorGreenText: #0b6a0b;
  --colorCyan: #038387; --colorCyanSubtle: #d0f0f1; --colorCyanText: #036c6f;
  --colorOrange: #ca5010; --colorOrangeSubtle: #fed9cc; --colorOrangeText: #9e4708;
  --pityBarGold: linear-gradient(90deg, #c49011, #d4a017, #e5b82a);
  --pityBarPurple: linear-gradient(90deg, #6b3f9e, #8764b8, #a278d0);
  --pityBarRed: linear-gradient(90deg, #a4262c, #d13438, #e85050);
}
[data-theme="dark"] {
  --colorNeutralBackground1: #292929;
  --colorNeutralBackground1Hover: #333333;
  --colorNeutralBackground1Pressed: #3d3d3d;
  --colorNeutralBackground2: #1f1f1f;
  --colorNeutralBackground2Hover: #292929;
  --colorNeutralBackground3: #1a1a1a;
  --colorNeutralBackground3Hover: #242424;
  --colorNeutralBackground4: #161616;
  --colorNeutralBackground5: #141414;
  --colorNeutralBackground6: #101010;
  --colorSubtleBackground: #1f1f1f;
  --colorSubtleBackgroundHover: #333333;
  --colorNeutralForeground1: #f8f8f8;
  --colorNeutralForeground1Hover: #ffffff;
  --colorNeutralForeground2: #c4c4c4;
  --colorNeutralForeground2Hover: #d4d4d4;
  --colorNeutralForeground3: #8a8a8a;
  --colorNeutralForeground4: #737373;
  --colorNeutralForegroundDisabled: #6d6d6d;
  --colorNeutralStroke1: #4a4a4a;
  --colorNeutralStroke1Hover: #5a5a5a;
  --colorNeutralStroke2: #3d3d3d;
  --colorNeutralStroke3: #333333;
  --colorNeutralStrokeAccessible: #adadad;
  --colorNeutralStrokeAccessibleHover: #bdbdbd;
  --colorNeutralStrokeAccessiblePressed: #b3b3b3;
  --colorCompoundBrandStroke: #62abf5;
  --colorCompoundBrandForeground: #62abf5;
  --colorCompoundBrandBackground: #479ef5;
  --colorCompoundBrandBackgroundHover: #62abf5;
  --colorCompoundBrandBackgroundPressed: #2886de;
  --colorNeutralForegroundInverted: #242424;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandBackground2: #083147;
  --colorBrandForeground1: #62abf5;
  --colorBrandForeground2: #74b5f7;
  --shadow2: 0 1px 2px rgba(0,0,0,0.28), 0 2px 6px rgba(0,0,0,0.20);
  --shadow4: 0 2px 4px rgba(0,0,0,0.22), 0 4px 12px rgba(0,0,0,0.24);
  --shadow8: 0 4px 8px rgba(0,0,0,0.26), 0 8px 24px rgba(0,0,0,0.30);
  --colorGold: #f0b429; --colorGoldSubtle: #3d3019; --colorGoldText: #f0b429;
  --colorPurple: #b77dff; --colorPurpleSubtle: #2d1f4a; --colorPurpleText: #b77dff;
  --colorRed: #ff6b6b; --colorRedSubtle: #3d1a1a; --colorRedText: #ff6b6b;
  --colorGreen: #51cf66; --colorGreenSubtle: #1a3d20; --colorGreenText: #51cf66;
  --colorCyan: #22b8cf; --colorCyanSubtle: #1a2e2e; --colorCyanText: #22b8cf;
  --colorOrange: #ff922b; --colorOrangeSubtle: #2e2a1a; --colorOrangeText: #ff922b;
  --pityBarGold: linear-gradient(90deg, #8b6914, #c49520, var(--colorGold));
  --pityBarPurple: linear-gradient(90deg, #5f3d8f, #8b5dcf, var(--colorPurple));
  --pityBarRed: linear-gradient(90deg, #8b2020, #d43d3d, #ff5555);
}

* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: var(--colorNeutralBackground3);
  color: var(--colorNeutralForeground1);
  line-height: 1.5; min-height: 100vh;
  transition: background 0.3s ease, color 0.3s ease;
}
a { color: var(--colorBrandForeground1); text-decoration: none; }
.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }

/* Header */
.header {
  background: var(--colorNeutralBackground1);
  border-bottom: 1px solid var(--colorNeutralStroke2);
  padding: 12px 0;
  position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(20px);
  transition: background 0.3s ease;
}
.header-inner { display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 22px; font-weight: 600; color: var(--colorBrandForeground1); }
.header .meta { color: var(--colorNeutralForeground2); font-size: 13px; margin-top: 2px; }
.breadcrumb-bar { background: var(--colorNeutralBackground2); border-bottom: 1px solid var(--colorNeutralStroke2); padding: 6px 0; }

/* Theme switcher */
.theme-switch { display: flex; align-items: center; gap: 8px; color: var(--colorNeutralForeground2); font-size: 14px; }
.theme-toggle {
  position: relative; width: 40px; height: 20px;
  background: transparent;
  border-radius: 10px; cursor: pointer;
  border: 1px solid var(--colorNeutralStrokeAccessible);
  transition: all 0.2s ease; flex-shrink: 0;
}
.theme-toggle::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 14px; height: 14px; background: var(--colorNeutralStrokeAccessible);
  border-radius: 50%; transition: all 0.2s ease;
}
.theme-toggle:focus-visible { box-shadow: 0 0 0 2px var(--colorCompoundBrandStroke); }
.theme-toggle:hover { border-color: var(--colorNeutralStrokeAccessibleHover); }
.theme-toggle:hover::after { background: var(--colorNeutralStrokeAccessibleHover); }
.theme-toggle:active { border-color: var(--colorNeutralStrokeAccessiblePressed); }
.theme-toggle:active::after { background: var(--colorNeutralStrokeAccessiblePressed); }
.theme-toggle.active {
  background: var(--colorCompoundBrandBackground);
  border-color: transparent;
}
.theme-toggle.active::after { left: 22px; background: var(--colorNeutralForegroundInverted); }
.theme-toggle.active:hover {
  background: var(--colorCompoundBrandBackgroundHover);
}
.theme-toggle.active:active {
  background: var(--colorCompoundBrandBackgroundPressed);
}
.theme-icon { font-size: 16px; line-height: 1; transition: opacity 0.2s ease; display: flex; align-items: center; }
svg.fluent-icon { vertical-align: middle; flex-shrink: 0; }

/* Breadcrumb (Fluent UI 2 — 实测 Storybook) */
.f2-breadcrumb {
  display: flex; align-items: center; gap: 0;
  font-size: 14px; line-height: 20px; font-weight: 400;
  margin-bottom: 4px;
}
.f2-breadcrumb__item {
  display: flex; align-items: center; justify-content: center;
  padding: 6px; height: 32px;
  color: var(--colorNeutralForeground2);
  text-decoration: none; cursor: pointer;
  border-radius: 4px; border: none; background: transparent;
  transition: background 0.1s, color 0.1s;
}
.f2-breadcrumb__item:hover {
  color: var(--colorNeutralForeground1);
  background: var(--colorSubtleBackgroundHover);
  text-decoration: none;
}
.f2-breadcrumb__item--current {
  color: var(--colorNeutralForeground2);
  cursor: default; pointer-events: none;
}
.f2-breadcrumb__item--current:hover {
  color: var(--colorNeutralForeground2);
  background: transparent;
}
.f2-breadcrumb__sep {
  color: var(--colorNeutralForeground1);
  display: flex; align-items: center;
  font-size: 16px; padding: 0; margin: 0;
}

/* Header actions */
.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: flex-end; }
.btn-merge {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 16px; border-radius: 4px;
  font-size: 13px; font-weight: 600;
  background: var(--colorBrandBackground);
  color: #ffffff; border: none; cursor: pointer;
  transition: background 0.15s ease;
}
.btn-merge:hover { background: var(--colorBrandBackgroundHover); }
.btn-merge:active { background: var(--colorBrandBackgroundPressed); }
.btn-merge:focus-visible { outline: 2px solid var(--colorCompoundBrandStroke); outline-offset: 1px; }

/* Overview Cards */
.overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 12px 0; }
.stat-card {
  background: var(--colorNeutralBackground1); border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px; padding: 16px; text-align: center;
  transition: all 0.15s ease; box-shadow: var(--shadow2);
}
.stat-card:hover { box-shadow: var(--shadow4); border-color: var(--colorNeutralStroke1); }
.stat-card .label { font-size: 12px; color: var(--colorNeutralForeground2); margin-bottom: 6px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; }
.stat-card .value { font-size: 32px; font-weight: 700; }
.stat-card .sub { font-size: 12px; color: var(--colorNeutralForeground2); margin-top: 4px; }
.stat-card.gold .value { color: var(--colorGoldText); }
.stat-card.purple .value { color: var(--colorPurpleText); }
.stat-card.blue .value { color: var(--colorBrandForeground1); }

/* Pool Tabs */
/* Fluent UI 2 TabList: Horizontal */
/* ── Fluent UI 2 TabList Horizontal ── */
.pool-tabs {
  display: flex; flex-direction: row; gap: 0;
  margin: 12px 0 0; padding: 0; height: 44px;
  position: relative; overflow-x: auto; overflow-y: hidden;
  scrollbar-width: thin;
}
.pool-tabs::-webkit-scrollbar { height: 2px; }
.pool-tabs::-webkit-scrollbar-thumb { background: var(--colorNeutralStroke3); border-radius: 1px; }
.pool-tab {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  padding: 12px 10px; height: 44px; background: transparent; border: none;
  cursor: pointer; font-size: 13px; font-weight: 400;
  color: var(--colorNeutralForeground2);
  border-radius: 4px; white-space: nowrap; flex-shrink: 0; z-index: 1;
  transition: color 0.15s ease, background 0.15s ease;
}
.pool-tab:hover {
  color: var(--colorNeutralForeground1Hover);
  background: var(--colorSubtleBackgroundHover);
}
.pool-tab:focus-visible {
  outline: 2px solid var(--colorCompoundBrandStroke); outline-offset: -2px; border-radius: 4px;
}
.pool-tab.active {
  color: var(--colorNeutralForeground1); font-weight: 400; background: transparent;
}
/* 选中指示条：::after 伪元素，3px 高，pill 圆角，brand 色 */
.pool-tab::after {
  content: ''; display: block; position: absolute; bottom: 0;
  left: 10px; right: 10px; height: 3px; border-radius: 10000px;
  background: transparent;
  transition: background 0.3s cubic-bezier(0.1, 0.9, 0.2, 1);
}
.pool-tab.active::after {
  background: var(--colorCompoundBrandStroke);
}
.pool-tab .count {
  display: inline-block; background: var(--colorNeutralBackground4);
  padding: 1px 7px 2px; border-radius: 10px; font-size: 11px;
  margin-left: 5px; font-weight: 400; color: var(--colorNeutralForeground3);
  vertical-align: middle; line-height: 16px; position: relative; top: 1px;
}
.pool-tab.active .count { background: var(--colorBrandBackground2); color: var(--colorCompoundBrandForeground); }
#pool-content { margin-top: 12px; }

/* Fluent Card */
.fcard {
  background: var(--colorNeutralBackground1); border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px; padding: 16px; box-shadow: var(--shadow2);
  transition: background 0.3s ease, border-color 0.3s ease;
}
.fcard h3 { font-size: 13px; font-weight: 600; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--colorNeutralStroke2); color: var(--colorNeutralForeground2); text-transform: uppercase; letter-spacing: 0.6px; display: flex; align-items: center; gap: 8px; }
.fcard h3 svg { flex-shrink: 0; }
.pool-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
@media (max-width: 768px) { .pool-grid { grid-template-columns: 1fr; } }

/* Pity Bar */
.pity-item { margin-bottom: 12px; position: relative; }
.pity-label { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 13px; color: var(--colorNeutralForeground2); }
.pity-bar-track { height: 8px; background: var(--colorNeutralBackground4); border-radius: 4px; position: relative; overflow: visible; }
.pity-soft-zone { position: absolute; right: 0; top: -1px; bottom: -1px; opacity: 0.15; pointer-events: none; border-radius: 0 3px 3px 0; }
.pity-soft-zone.gold { background: var(--colorGold); width: 31.25%; }
.pity-soft-zone.purple { background: var(--colorPurple); width: 30%; }
.pity-fill { height: 100%; border-radius: 4px; transition: width 0.8s cubic-bezier(0.4,0,0.2,1); position: relative; z-index: 1; }
.pity-fill.gold { background: var(--pityBarGold); }
.pity-fill.purple { background: var(--pityBarPurple); }
.pity-fill.red { background: var(--pityBarRed); }
.pity-fill.gold.hot { background: var(--pityBarGold); animation: pulse 1.5s ease-in-out infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.7} }
.pity-milestone { position: absolute; top: -3px; height: calc(100% + 6px); width: 1px; background: var(--colorNeutralStrokeAccessible); z-index: 2; opacity: 0.5; }
.pity-milestone-label { position: absolute; top: -18px; font-size: 10px; color: var(--colorNeutralForeground3); transform: translateX(-50%); white-space: nowrap; }
.pity-status { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; margin-top: 6px; }
.pity-status.small { background: var(--colorGreenSubtle); color: var(--colorGreenText); }
.pity-status.big { background: var(--colorRedSubtle); color: var(--colorRedText); }
.pity-status.no-up { background: var(--colorPurpleSubtle); color: var(--colorBrandForeground1); }
.pity-status::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.pity-prob-row { display: flex; gap: 16px; margin-top: 6px; font-size: 12px; color: var(--colorNeutralForeground3); }
.pity-prob-item strong { color: var(--colorNeutralForeground1); margin-left: 2px; }
.pity-prob-item.hot strong { color: var(--colorRedText); }

/* Stats Grid */
.stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.stat-item { display: flex; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--colorNeutralStroke3); font-size: 13px; }
.stat-item .label { color: var(--colorNeutralForeground2); }
.stat-item .val { font-weight: 600; color: var(--colorNeutralForeground1); }

/* Fluent UI 2 Table */
.ftable { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 13px; }
.ftable col { width: 14.286%; }
.ftable thead th {
  text-align: center; padding: 6px 8px; font-weight: 600; font-size: 12px;
  color: var(--colorNeutralForeground3); background: transparent;
  border-bottom: 1px solid var(--colorNeutralStroke2); white-space: nowrap;
}
.ftable tbody td {
  padding: 5px 8px; border-bottom: 1px solid var(--colorNeutralStroke3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: center;
}
.ftable tbody tr:hover td { background: var(--colorSubtleBackgroundHover); }
.ftable tbody td.td-icon { padding: 3px 4px; }
.ftable tbody td.td-icon img {
  width: 26px; height: 26px; border-radius: 4px; object-fit: cover;
  background: var(--colorNeutralBackground4); vertical-align: middle;
}
.ftable tbody tr.star5-row .td-icon img { box-shadow: 0 0 3px rgba(212,160,23,0.25); }
.ftable tbody tr.star4-row .td-icon img { box-shadow: 0 0 2px rgba(135,100,184,0.2); }
.ftable tbody td.td-num { color: var(--colorNeutralForeground3); font-size: 12px; }
.ftable tbody td.td-name { font-weight: 600; color: var(--colorNeutralForeground1); }
.ftable tbody td.td-type { color: var(--colorNeutralForeground3); }
.ftable tbody td.td-pity { color: var(--colorNeutralForeground3); }
.ftable tbody td.td-pity strong { font-weight: 700; color: var(--colorNeutralForeground1); }
.ftable tbody td.td-time { color: var(--colorNeutralForeground3); font-size: 12px; font-variant-numeric: tabular-nums; }
.ftable tbody td.td-tag-empty {}
.ftable tbody tr.star5-row td.td-name { color: var(--colorGoldText); }
.ftable tbody tr.star5-row td.td-pity strong { color: var(--colorGoldText); }
.ftable tbody tr.star4-row td.td-name { color: var(--colorPurpleText); }
.ftable tbody tr.star4-row td.td-pity strong { color: var(--colorPurpleText); }

.history-section { margin-bottom: 12px; }
.history-section h3 { font-size: 13px; font-weight: 600; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; color: var(--colorNeutralForeground2); text-transform: uppercase; letter-spacing: 0.6px; }

/* Tags */
.tag { display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; letter-spacing: 0.2px; line-height: 18px; }
.tag.up { background: var(--colorGreenSubtle); color: var(--colorGreenText); }
.tag.lost { background: var(--colorRedSubtle); color: var(--colorRedText); }
.tag.guaranteed { background: var(--colorOrangeSubtle); color: var(--colorOrangeText); }
.tag.weapon-up { background: var(--colorCyanSubtle); color: var(--colorCyanText); }
.tag.standard { background: var(--colorPurpleSubtle); color: var(--colorBrandForeground1); }

/* Pity Distribution */
.pity-dist { display: flex; align-items: flex-end; gap: 2px; height: 80px; padding: 8px 0; }
.pity-bar-v { flex: 1; min-width: 8px; background: var(--colorGold); border-radius: 3px 3px 0 0; transition: height 0.4s ease; position: relative; }
.pity-bar-v:hover { opacity: 0.8; }
.pity-bar-v .tip { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--colorNeutralBackground6); color: var(--colorNeutralForeground1); padding: 2px 6px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; border: 1px solid var(--colorNeutralStroke1); }
.pity-bar-v:hover .tip { display: block; }
.pity-labels { display: flex; gap: 2px; font-size: 10px; color: var(--colorNeutralForeground3); }
.pity-labels span { flex: 1; text-align: center; min-width: 8px; }

/* Fluent UI 2 Divider */
.fui-divider { display: block; margin: 8px 0; border: none; border-top: 1px solid var(--colorNeutralStroke2); }
.fui-divider.inset { margin: 12px 0; }
.fui-divider-brand { display: block; margin: 8px 0; border: none; border-top: 1px solid var(--colorCompoundBrandStroke); }
.fui-divider-strong { display: block; margin: 8px 0; border: none; border-top: 1px solid var(--colorNeutralStroke1); }

/* Footer */
.footer { text-align: center; color: var(--colorNeutralForeground2); font-size: 12px; padding: 24px 0; margin-top: 12px; }

/* Merge modal */
.modal-overlay {
  display: none; position: fixed; top:0; left:0; width:100%; height:100%;
  background: rgba(0,0,0,0.5); z-index: 1000;
  align-items: center; justify-content: center;
}
.modal-overlay.show { display: flex; }
.modal {
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke2);
  border-radius: 12px; padding: 32px;
  max-width: 480px; width: 90%;
  box-shadow: var(--shadow8);
}
.modal h2 { font-size: 18px; font-weight: 600; margin-bottom: 8px; color: var(--colorNeutralForeground1); }
.modal .desc { font-size: 13px; color: var(--colorNeutralForeground2); margin-bottom: 20px; line-height: 1.6; }
.modal .upload-zone {
  border: 2px dashed var(--colorNeutralStroke1);
  border-radius: 8px; padding: 28px 16px;
  cursor: pointer; transition: all 0.2s ease;
  position: relative; text-align: center;
}
.modal .upload-zone:hover, .modal .upload-zone.dragover {
  border-color: var(--colorBrandForeground1);
  background: rgba(0,120,212,0.04);
}
.modal .upload-zone .zone-icon { color: var(--colorNeutralForeground3); margin-bottom: 8px; display: flex; justify-content: center; }
.modal .upload-zone:hover .zone-icon { color: var(--colorBrandForeground1); }
.modal .upload-zone .text { font-size: 13px; color: var(--colorNeutralForeground2); }
.modal .upload-zone .text strong { color: var(--colorBrandForeground1); }
.modal .upload-zone input[type="file"] {
  position: absolute; top:0; left:0; width:100%; height:100%;
  opacity: 0; cursor: pointer;
}
/* Toast */
.toast-container { position: fixed; bottom: 16px; right: 20px; width: 292px; pointer-events: none; z-index: 9999; }
.toast {
  pointer-events: all; display: grid; grid-template-columns: auto 1fr auto;
  padding: 12px; border-radius: 4px; border: 1px solid transparent;
  box-shadow: 0 4px 8px rgba(0,0,0,0.14), 0 0 2px rgba(0,0,0,0.12);
  background: var(--colorNeutralBackground1); color: var(--colorNeutralForeground1);
  font-size: 14px; line-height: 20px; margin-top: 16px;
  animation: toast-in 0.25s cubic-bezier(0.4,0,0.2,1) forwards;
}
[data-theme="dark"] .toast { background: #292929; color: #e0e0e0; }
.toast__media { display: flex; padding-top: 2px; padding-right: 8px; font-size: 16px; align-items: flex-start; }
.toast__content { grid-column: 2 / 3; min-width: 0; }
.toast__title { font-weight: 600; word-break: break-word; }
.toast__body { padding-top: 4px; font-weight: 400; font-size: 14px; color: var(--colorNeutralForeground2); word-break: break-word; }
[data-theme="dark"] .toast__body { color: #c4c4c4; }
.toast__close {
  grid-column: 3; display: flex; align-items: flex-start; padding-left: 12px;
  background: none; border: none; cursor: pointer; color: var(--colorNeutralForeground3);
  padding: 0; font-size: 16px; line-height: 1;
}
.toast__close:hover { color: var(--colorNeutralForeground1); }
.toast--success .toast__media { color: #0f7b0f; }
[data-theme="dark"] .toast--success .toast__media { color: #9edcab; }
.toast--error .toast__media { color: #d13438; }
[data-theme="dark"] .toast--error .toast__media { color: #f5a5ae; }
.toast--warning .toast__media { color: #9d5d00; }
[data-theme="dark"] .toast--warning .toast__media { color: #f7c67f; }
.toast--info .toast__media { color: #616161; }
[data-theme="dark"] .toast--info .toast__media { color: #e0e0e0; }
.toast--exit { animation: toast-out 0.2s cubic-bezier(0.4,0,0.2,1) forwards; }
@keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@keyframes toast-out { from { opacity: 1; transform: translateY(0); } to { opacity: 0; transform: translateY(-4px); } }
.modal-close {
  position: absolute; top: 16px; right: 16px;
  background: none; border: none; cursor: pointer;
  color: var(--colorNeutralForeground2);
  padding: 4px; display: flex; align-items: center; justify-content: center;
  border-radius: 4px; transition: background 0.15s, color 0.15s;
}
.modal-close:hover { background: var(--colorNeutralBackground4); color: var(--colorNeutralForeground1); }

/* ── 抽卡记录宫格/横向 ── */
.record-view-toggle {
  display: flex; gap: 2px; background: var(--colorNeutralBackground3);
  border-radius: 4px; padding: 2px; margin-bottom: 12px; width: fit-content;
}
.record-view-toggle .toggle-btn {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 12px; border-radius: 3px; border: none;
  background: transparent; color: var(--colorNeutralForeground2);
  cursor: pointer; font-size: 12px; font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.record-view-toggle .toggle-btn.active {
  background: var(--colorNeutralBackground1);
  color: var(--colorNeutralForeground1);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.record-view-toggle .toggle-btn svg { width: 14px; height: 14px; }

/* 宫格排列 — 零间隙，padding 模拟间距，整个格子都是悬停区域 */
.grid-records {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
  gap: 0; padding: 0;
}
.grid-card {
  position: relative; display: flex; flex-direction: column;
  align-items: center; padding: 3px; cursor: default;
  background: transparent; border: none; border-radius: 0;
  transition: background 0.1s;
}
.grid-card .card-inner {
  display: flex; flex-direction: column; align-items: center;
  padding: 8px 4px 6px; border-radius: 8px; width: 100%;
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke2);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.grid-card:hover .card-inner { border-color: var(--colorNeutralStroke1); box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
.grid-card.same-time-highlight .card-inner {
  background: var(--colorBrandBackground2);
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.grid-card:hover.same-time-highlight .card-inner {
  box-shadow: 0 3px 10px rgba(0,0,0,0.12);
}
.grid-card.star5 .card-inner { border-color: rgba(255,180,0,0.45); }
[data-theme="dark"] .grid-card.star5 .card-inner { border-color: rgba(255,200,50,0.35); }
.grid-card.star5:hover .card-inner { border-color: rgba(255,180,0,0.7); }
.grid-card.star5.same-time-highlight .card-inner { border-color: rgba(255,180,0,0.6); background: var(--colorBrandBackground2); }
[data-theme="dark"] .grid-card.star5.same-time-highlight .card-inner { border-color: rgba(255,200,50,0.5); }
.grid-card.star4 .card-inner { border-color: rgba(160,90,220,0.35); }
[data-theme="dark"] .grid-card.star4 .card-inner { border-color: rgba(180,120,240,0.3); }
.grid-card.star4:hover .card-inner { border-color: rgba(160,90,220,0.6); }
.grid-card.star4.same-time-highlight .card-inner { border-color: rgba(160,90,220,0.5); background: var(--colorBrandBackground2); }
[data-theme="dark"] .grid-card.star4.same-time-highlight .card-inner { border-color: rgba(180,120,240,0.45); }
.grid-card .card-icon {
  width: 56px; height: 56px; border-radius: 50%;
  object-fit: cover; margin-bottom: 4px;
}
.grid-card .card-name {
  font-size: 12px; line-height: 1.4; color: var(--colorNeutralForeground2);
  text-align: center; width: 100%; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; padding: 0 2px;
}
.grid-card.star5 .card-name { color: var(--colorGoldText); font-weight: 600; }
.grid-card.star4 .card-name { color: var(--colorPurpleText); font-weight: 600; }
.grid-card .card-badge {
  position: absolute; top: 7px; left: 7px; font-size: 10px;
  background: var(--colorNeutralBackground4); color: var(--colorNeutralForeground3);
  border-radius: 3px; padding: 0 4px; line-height: 16px;
}
.grid-card.star5 .card-badge { background: rgba(255,180,0,0.15); color: var(--colorGoldText); }
.grid-card.star4 .card-badge { background: rgba(160,90,220,0.12); color: var(--colorPurpleText); }
.grid-card .card-tag {
  position: absolute; top: 7px; right: 7px; font-size: 9px;
  border-radius: 3px; padding: 0 4px; line-height: 15px; font-weight: 600;
}
.grid-card .card-tag.up { background: rgba(255,180,0,0.15); color: var(--colorGoldText); }
.grid-card .card-tag.lost { background: rgba(220,60,60,0.12); color: var(--colorRedText); }
.grid-card .card-tag.guaranteed { background: rgba(220,60,60,0.12); color: var(--colorRedText); }

/* Fluent UI 2 Tooltip */
.gacha-tooltip {
  position: fixed; z-index: 9999; pointer-events: none;
  padding: 6px 10px; border-radius: 4px;
  font-size: 12px; line-height: 1.5; max-width: 240px;
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke1);
  box-shadow: 0 4px 16px rgba(0,0,0,0.14), 0 1px 4px rgba(0,0,0,0.08);
  color: var(--colorNeutralForeground1);
  opacity: 0; transform: translateY(4px);
  transition: opacity 0.15s cubic-bezier(0.4,0,0.2,1), transform 0.15s cubic-bezier(0.4,0,0.2,1);
}
[data-theme="dark"] .gacha-tooltip {
  box-shadow: 0 4px 16px rgba(0,0,0,0.36), 0 1px 4px rgba(0,0,0,0.2);
}
.gacha-tooltip.visible { opacity: 1; transform: translateY(0); }
.gacha-tooltip .tt-name { font-weight: 600; margin-bottom: 2px; }
.gacha-tooltip .tt-star5 .tt-name { color: var(--colorGoldText); }
.gacha-tooltip .tt-star4 .tt-name { color: var(--colorPurpleText); }
.gacha-tooltip .tt-meta { color: var(--colorNeutralForeground3); font-size: 11px; }

/* 聚焦模式：鼠标在宫格区域内时所有卡片灰化，只高亮同时间卡片 */
.grid-records.in-focus .grid-card {
  filter: saturate(0.2) brightness(0.75);
  transition: filter 0.15s cubic-bezier(0.4,0,0.2,1);
}
[data-theme="dark"] .grid-records.in-focus .grid-card {
  filter: saturate(0.15) brightness(0.65);
}
.grid-records.in-focus .grid-card.same-time-highlight {
  filter: none;
  transition: filter 0.1s cubic-bezier(0.4,0,0.2,1);
}

/* 横向排列 — 保底进度条 */
.tl-pity-timeline { padding: 4px 0; display: flex; flex-direction: column; gap: 10px; }
.tl-row { display: flex; align-items: center; gap: 0; }
.tl-bar-track {
  flex: 1; height: 32px; border-radius: 6px; position: relative; overflow: hidden;
  background: var(--colorNeutralBackground3);
}
.tl-bar-fill {
  height: 100%; border-radius: 6px; transition: width 0.4s cubic-bezier(0.4,0,0.2,1);
}
.tl-bar-fill.up { background: #107c10; }
[data-theme="dark"] .tl-bar-fill.up { background: #0e7a0e; }
.tl-bar-fill.lost { background: #d48c00; }
[data-theme="dark"] .tl-bar-fill.lost { background: #c78c10; }
.tl-bar-fill.guaranteed {
  background: repeating-linear-gradient(
    -45deg, #107c10, #107c10 4px, #18961e 4px, #18961e 8px
  );
}
[data-theme="dark"] .tl-bar-fill.guaranteed {
  background: repeating-linear-gradient(
    -45deg, #0e7a0e, #0e7a0e 4px, #1a9c22 4px, #1a9c22 8px
  );
}
.tl-bar-fill.standard { background: var(--colorNeutralForeground3); }
.tl-bar-fill.current { background: #0078d4; }
[data-theme="dark"] .tl-bar-fill.current { background: #479ef5; }
.tl-bar-text {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  font-size: 12px; color: var(--colorNeutralForegroundOnAccent);
  white-space: nowrap; font-weight: 600;
}
.tl-bar-end {
  display: flex; align-items: center; gap: 8px; margin-left: 10px; flex-shrink: 0;
}
.tl-s5-icon {
  width: 44px; height: 44px; border-radius: 6px; object-fit: cover;
  border: 2px solid rgba(255,180,0,0.6); background: var(--colorNeutralBackground1);
}
[data-theme="dark"] .tl-s5-icon { border-color: rgba(255,200,50,0.5); }
.tl-s5-icon.placeholder { background: var(--colorNeutralBackground3); }
.tl-s5-info { display: flex; flex-direction: column; gap: 1px; min-width: 60px; }
.tl-s5-name { font-size: 13px; color: var(--colorGoldText); font-weight: 600; line-height: 1.3; white-space: nowrap; }
.tl-s5-pity { font-size: 11px; color: var(--colorNeutralForeground3); line-height: 1.4; }
.tl-tag {
  display: inline-block; font-size: 10px; padding: 0 5px; border-radius: 3px;
  line-height: 16px; font-weight: 600; margin-top: 1px;
}
.tl-tag.up { background: rgba(16,124,16,0.12); color: #107c10; }
[data-theme="dark"] .tl-tag.up { background: rgba(16,124,16,0.2); color: #6ccb5f; }
.tl-tag.lost { background: rgba(212,140,0,0.12); color: #d48c00; }
[data-theme="dark"] .tl-tag.lost { background: rgba(212,140,0,0.2); color: #f0b030; }
.tl-tag.guaranteed { background: rgba(16,124,16,0.12); color: #107c10; }
[data-theme="dark"] .tl-tag.guaranteed { background: rgba(16,124,16,0.2); color: #6ccb5f; }

/* Footer */
.site-footer { padding: 16px 0; text-align: center; font-size: 12px; color: var(--colorNeutralForeground3); display: flex; align-items: center; justify-content: center; gap: 12px; flex-wrap: wrap; }
.site-footer a { color: var(--colorNeutralForeground2); text-decoration: none; display: inline-flex; align-items: center; gap: 4px; transition: color 0.15s; }
.site-footer a:hover { color: var(--colorCompoundBrandForeground1); text-decoration: underline; }
.site-footer .sep { color: var(--colorNeutralForeground3); opacity: 0.4; }
</style>
</head>
<body>

<div class="header">
  <div class="container header-inner">
    <div>
      <h1>鸣潮抽卡分析</h1>
      <div class="meta" id="header-meta">UID: - | 数据截至: -</div>
    </div>
    <div class="header-actions">
      <button class="btn-merge" onclick="downloadData()"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" style="flex-shrink:0"><path d="M15.5 17a.5.5 0 0 1 .09 1H4.5a.5.5 0 0 1-.09-1H15.5ZM10 2a.5.5 0 0 1 .5.41V14.3l3.64-3.65a.5.5 0 0 1 .64-.06l.07.06c.17.17.2.44.06.63l-.06.07-4.5 4.5a.5.5 0 0 1-.25.14L10 16a.5.5 0 0 1-.4-.2l-4.46-4.45a.5.5 0 0 1 .64-.76l.07.06 3.65 3.64V2.5c0-.27.22-.5.5-.5Z"/></svg> 下载当前记录</button>
      <button class="btn-merge" onclick="showMergeModal()"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" style="flex-shrink:0"><path d="M10 2.5c.28 0 .5.22.5.5v6.5H17a.5.5 0 0 1 0 1h-6.5V17a.5.5 0 0 1-1 0v-6.5H3a.5.5 0 0 1 0-1h6.5V3c0-.28.22-.5.5-.5Z"/></svg> 合并抽卡记录</button>
      <div class="theme-switch">
        <span class="theme-icon" id="theme-icon-light"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm0 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-1a3 3 0 1 1 0-6 3 3 0 0 1 0 6Zm7.5-2.5a.5.5 0 0 0 0-1h-1a.5.5 0 0 0 0 1h1ZM10 16c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm-6.5-5.5a.5.5 0 0 0 0-1H2.46a.5.5 0 0 0 0 1H3.5Zm.65-6.35c.2-.2.5-.2.7 0l1 1a.5.5 0 1 1-.7.7l-1-1a.5.5 0 0 1 0-.7Zm.7 11.7a.5.5 0 0 1-.7-.7l1-1a.5.5 0 0 1 .7.7l-1 1Zm11-11.7a.5.5 0 0 0-.7 0l-1 1a.5.5 0 0 0 .7.7l1-1a.5.5 0 0 0 0-.7Zm-.7 11.7a.5.5 0 0 0 .7-.7l-1-1a.5.5 0 0 0-.7.7l1 1Z"/></svg></span>
        <div class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" role="switch" tabindex="0" aria-label="切换深浅主题"></div>
        <span class="theme-icon" id="theme-icon-dark"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M15.5 13.5A6.98 6.98 0 0 1 4 14.39c2.83-1.09 4.56-2.42 5.6-4.4 1.04-2 1.33-4.16.75-6.9A6.98 6.98 0 0 1 15.5 13.5ZM5.45 16.92A7.98 7.98 0 1 0 9.88 2.04a.6.6 0 0 0-.61.73c.69 2.82.43 4.88-.55 6.76-.94 1.78-2.55 3.03-5.55 4.1a.6.6 0 0 0-.3.9 7.95 7.95 0 0 0 2.59 2.39Z"/></svg></span>
      </div>
    </div>
  </div>
</div>
<div class="breadcrumb-bar">
  <div class="container">
    <nav class="f2-breadcrumb" aria-label="面包屑导航">
      <a class="f2-breadcrumb__item" href="/">上传数据</a>
      <span class="f2-breadcrumb__sep"><svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="M7.35 3.15c.2-.2.5-.2.7 0l5.65 5.65a.5.5 0 0 1 0 .7L8.05 15.15a.5.5 0 0 1-.7-.7L12.59 9 7.35 3.85a.5.5 0 0 1 0-.7Z"/></svg></span>
      <a class="f2-breadcrumb__item f2-breadcrumb__item--current" aria-current="page">分析报告</a>
    </nav>
  </div>
</div>
<div class="container">
  <div id="overview" class="overview"></div>
  <div id="pool-tabs" class="pool-tabs"></div>
  <div id="pool-content"></div>
  <hr class="fui-divider inset">
  <div class="footer">
    数据来源：游戏内唤取记录 | 保底规则：5星80抽硬保底（新手池50抽），4星10抽硬保底<br>
    角色活动池5星保底跨池共享 | 武器活动池5星保底跨池共享 | 联动池保底仅在相同联动主题内共享<br>
    注意：API仅能获取近6个月数据 | UP/歪判定为基于常驻角色列表估算，仅供参考
  </div>
</div>

<!-- 合并抽卡记录弹窗 -->
<div class="modal-overlay" id="merge-modal">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="hideMergeModal()"><svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="m4.09 4.22.06-.07a.5.5 0 0 1 .63-.06l.07.06L10 9.29l5.15-5.14a.5.5 0 0 1 .63-.06l.07.06c.18.17.2.44.06.63l-.06.07L10.71 10l5.14 5.15c.18.17.2.44.06.63l-.06.07a.5.5 0 0 1-.63.06l-.07-.06L10 10.71l-5.15 5.14a.5.5 0 0 1-.63.06l-.07-.06a.5.5 0 0 1-.06-.63l.06-.07L9.29 10 4.15 4.85a.5.5 0 0 1-.06-.63l.06-.07-.06.07Z"/></svg></button>
    <h2>合并抽卡记录</h2>
    <div class="desc">上传历史抽卡记录JSON文件，将与当前数据合并。合并采用截断+接续策略，保留原始排列顺序。</div>
    <div class="upload-zone" id="merge-zone">
      <div class="zone-icon" style="justify-content:center"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M3.5 6.25c0-.97.78-1.75 1.75-1.75h2.88c.2 0 .39.08.53.22l2.06 2.06c.14.14.33.22.53.22h5.5c.97 0 1.75.78 1.75 1.75 0 .09.01.17.04.25H8.72c-1.34 0-2.58.71-3.25 1.87L3.5 14.28V6.25ZM2 17.79A3.25 3.25 0 0 0 5.25 21h11.04c1.33 0 2.57-.72 3.24-1.88l3.03-5.25A3.25 3.25 0 0 0 19.96 9a.75.75 0 0 0 .04-.25c0-1.8-1.45-3.25-3.25-3.25h-5.19L9.72 3.66c-.42-.42-1-.66-1.6-.66H5.26A3.25 3.25 0 0 0 2 6.25V17.79Zm6.72-7.3h11.03a1.75 1.75 0 0 1 1.51 2.63l-3.03 5.25c-.4.7-1.14 1.13-1.95 1.13H5.25a1.75 1.75 0 0 1-1.51-2.63l3.03-5.25c.4-.7 1.14-1.12 1.95-1.12Z"/></svg></div>
      <div class="text">拖拽文件到此处，或 <strong>点击选择文件</strong></div>
      <input type="file" id="merge-input" accept=".json" onchange="handleMerge(this.files[0])">
    </div>
  </div>
</div>

<script>
// ============================================================
// Toast System
// ============================================================
const TOAST_ICONS = {
  success: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm3.36 6.65-3.75 4.5a.5.5 0 0 1-.72.04l-2.25-2a.5.5 0 1 1 .66-.76l1.87 1.66 3.42-4.1a.5.5 0 0 1 .77.66Z"/></svg>',
  error: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.86 3.15a.5.5 0 0 0-.71.7L9.29 10l-6.14 6.15a.5.5 0 0 0 .7.7L10 10.72l6.15 6.14a.5.5 0 0 0 .7-.71L10.72 10l6.14-6.15a.5.5 0 0 0-.7-.7L10 9.29 3.86 3.14Z"/></svg>',
  warning: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm0 11.5a.75.75 0 1 1 0 1.5.75.75 0 0 1 0-1.5ZM10 6a.5.5 0 0 1 .5.41V11.59a.5.5 0 0 1-1 0V6.41A.5.5 0 0 1 10 6Z"/></svg>',
  info: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm.5 5.5a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM10 9a.5.5 0 0 1 .5.41V14.59a.5.5 0 0 1-1 0V9.41A.5.5 0 0 1 10 9Z"/></svg>'
};

function showToast(intent, title, body, duration) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = 'toast toast--' + intent;
  el.innerHTML = '<div class="toast__media">' + (TOAST_ICONS[intent] || TOAST_ICONS.info) + '</div>' +
    '<div class="toast__content"><div class="toast__title">' + title + '</div>' +
    (body ? '<div class="toast__body">' + body + '</div>' : '') + '</div>' +
    '<button class="toast__close" onclick="dismissToast(this.parentElement)"><svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path d="m4.09 4.22.06-.07a.5.5 0 0 1 .63-.06l.07.06L10 9.29l5.15-5.14a.5.5 0 0 1 .63-.06l.07.06c.18.17.2.44.06.63l-.06.07L10.71 10l5.14 5.15c.18.17.2.44.06.63l-.06.07a.5.5 0 0 1-.63.06l-.07-.06L10 10.71l-5.15 5.14a.5.5 0 0 1-.63.06l-.07-.06a.5.5 0 0 1-.06-.63l.06-.07L9.29 10 4.15 4.85a.5.5 0 0 1-.06-.63l.06-.07-.06.07Z"/></svg></button>';
  container.appendChild(el);
  const timeout = duration !== undefined ? duration : 3000;
  if (timeout > 0) setTimeout(() => dismissToast(el), timeout);
}

function dismissToast(el) {
  if (!el || el.classList.contains('toast--exit')) return;
  el.classList.add('toast--exit');
  setTimeout(() => el.remove(), 200);
}

// ============================================================
// Theme Management
// ============================================================
const storageKey = 'wuwa-theme';
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

function getSystemTheme() { return mediaQuery.matches ? 'dark' : 'light'; }

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const toggle = document.getElementById('theme-toggle');
  if (toggle) toggle.classList.toggle('active', theme === 'dark');
  const lightIcon = document.getElementById('theme-icon-light');
  const darkIcon = document.getElementById('theme-icon-dark');
  if (lightIcon) lightIcon.style.opacity = theme === 'light' ? '1' : '0.4';
  if (darkIcon) darkIcon.style.opacity = theme === 'dark' ? '1' : '0.4';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem(storageKey, next);
  applyTheme(next);
}

(function initTheme() {
  const saved = localStorage.getItem(storageKey);
  applyTheme(saved || getSystemTheme());
})();
mediaQuery.addEventListener('change', e => {
  if (!localStorage.getItem(storageKey)) applyTheme(e.matches ? 'dark' : 'light');
});
document.addEventListener('keydown', e => {
  if (e.target.id === 'theme-toggle' && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleTheme(); }
});

// ============================================================
// Merge Modal
// ============================================================
function showMergeModal() { document.getElementById('merge-modal').classList.add('show'); }
function hideMergeModal() { document.getElementById('merge-modal').classList.remove('show'); }

function downloadData() {
  if (!RAW_DATA || !Object.keys(RAW_DATA).some(k => k !== 'uid' && Array.isArray(RAW_DATA[k]) && RAW_DATA[k].length)) {
    showToast('warning', '暂无数据', '当前没有可下载的抽卡记录'); return;
  }
  const uid = RAW_DATA.uid || 'unknown';
  const now = new Date();
  const ts = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0')
    + '_' + String(now.getHours()).padStart(2,'0') + String(now.getMinutes()).padStart(2,'0') + String(now.getSeconds()).padStart(2,'0');
  const blob = new Blob([JSON.stringify(RAW_DATA, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `uid_${uid}_${ts}_merged.json`;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
}

const mergeZone = document.getElementById('merge-zone');
mergeZone.addEventListener('dragover', e => { e.preventDefault(); mergeZone.classList.add('dragover'); });
mergeZone.addEventListener('dragleave', () => mergeZone.classList.remove('dragover'));
mergeZone.addEventListener('drop', e => {
  e.preventDefault(); mergeZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleMerge(e.dataTransfer.files[0]);
});

// Close modal on overlay click
document.getElementById('merge-modal').addEventListener('click', e => {
  if (e.target === e.currentTarget) hideMergeModal();
});

function handleMerge(file) {
  if (!file) return;
  showToast('info', '正在合并', file.name);

  const formData = new FormData();
  formData.append('file', file);

  fetch('/api/merge', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        showToast('success', '合并成功', '共' + data.total + '条记录，正在刷新...', 3000);
        setTimeout(() => { hideMergeModal(); loadAndRender(); }, 1000);
      } else {
        showToast('error', '合并失败', data.error || '未知错误');
      }
    })
    .catch(err => {
      showToast('error', '网络错误', err.message);
    });
}

// ============================================================
// Data & Analysis (from original gacha_report.py)
// ============================================================
let RAW_DATA = {};
let ICON_MAP = {};

function getIconUrl(resourceId) {
  if (!resourceId || !ICON_MAP[resourceId]) return '';
  return ICON_MAP[resourceId];
}

const POOL_CONFIG = {
  "1":  { name: "角色活动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character", hasUP4: true, up4Type: "character", crossPoolPity: "char-event" },
  "2":  { name: "武器活动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-guaranteed", hasUP4: true, up4Type: "weapon", crossPoolPity: "weapon-event" },
  "3":  { name: "角色常驻唤取", pity5: 80, pity4: 10, hasUP5: false, hasUP4: false },
  "4":  { name: "武器常驻唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-selected", hasUP4: false },
  "5":  { name: "新手唤取",     pity5: 50, pity4: 10, hasUP5: false, hasUP4: false },
  "6":  { name: "新手自选唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-selected", hasUP4: false },
  "7":  { name: "感恩定向唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-selected", hasUP4: false },
  "8":  { name: "角色新旅唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character", hasUP4: true, up4Type: "character" },
  "9":  { name: "武器新旅唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-selected", hasUP4: true, up4Type: "weapon" },
  "10": { name: "角色联动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-collab", hasUP4: true, up4Type: "character-collab", crossPoolPity: "char-collab" },
  "11": { name: "武器联动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-guaranteed-collab", hasUP4: true, up4Type: "weapon-collab", crossPoolPity: "weapon-collab" },
};

const STANDARD_5STAR_CHARS = new Set(['维里奈','凌阳','卡卡罗','鉴心','安可']);

// ============================================================
// Probability Model (NGA-verified: 0.8% base, +9.02%/pull from pull 70)
// ============================================================
function get5StarPullProb(pity, maxPity) {
  if (pity >= maxPity) return 1;
  if (pity < 0) pity = 0;
  const BASE = 0.008;
  if (maxPity === 80) {
    if (pity < 70) return BASE;
    const INC = 0.0902;
    return Math.min(1, BASE + (pity - 69) * INC);
  }
  if (maxPity === 50) {
    // 新手池: 无公开数据，按比例估算
    if (pity < 40) return BASE;
    return Math.min(1, BASE + (pity - 39) * 0.0526);
  }
  return BASE;
}

function getCumulativeProb(pity, maxPity, ahead) {
  // 从当前pity开始，未来ahead抽内出5星的概率
  let p = 1;
  for (let i = 0; i < ahead; i++) {
    p *= (1 - get5StarPullProb(pity + i, maxPity));
  }
  return 1 - p;
}

function get4StarPullProb(pity, maxPity) {
  if (pity >= maxPity) return 1;
  if (pity < 0) pity = 0;
  const BASE = 0.06;
  if (pity < 7) return BASE;
  // 10抽保底: 7抽开始概率递增
  return Math.min(1, BASE + (pity - 6) * 0.28);
}

function normalizeData(raw) {
  const data = { uid: raw.uid || '' };
  for (const [key, val] of Object.entries(raw)) {
    if (key === 'uid' || !Array.isArray(val)) continue;
    let poolId = key;
    if (!/^\\d+$/.test(key)) {
      const m = { '角色活动唤取':'1','武器活动唤取':'2','角色常驻唤取':'3','武器常驻唤取':'4',
        '新手唤取':'5','新手自选唤取':'6','感恩定向唤取':'7',
        '角色精准调谐':'1','武器精准调谐':'2','角色调谐（常驻池）':'3','武器调谐（常驻池）':'4',
        '新手调谐':'5','自选调谐':'6','常驻调谐':'7' };
      poolId = m[key] || key;
    }
    const records = val.map(r => ({ ...r, qualityLevel: Number(r.qualityLevel)||3, resourceId: Number(r.resourceId)||0, count: Number(r.count)||1 }));
    if (!data[poolId]) data[poolId] = [];
    data[poolId] = data[poolId].concat(records);
  }
  return data;
}

function analyzePool(records, poolId) {
  if (!records || !records.length) return null;
  const cfg = POOL_CONFIG[poolId] || { pity5:80, pity4:10, hasUP5:false, hasUP4:false };
  // 数据本身已是倒序(最新在前)，直接反转为时间升序(最旧在前)
  // 不能用 sort，因为同时间戳的记录顺序代表抽卡先后，sort 会打乱顺序
  const sorted = [...records].reverse();
  let cur5=0, cur4=0, gs5='small', gs4='small';
  const s5=[], s4=[];

  for (let i=0; i<sorted.length; i++) {
    const r=sorted[i]; cur5++; cur4++;

    if (r.qualityLevel===5) {
      let tag='';
      if (cfg.hasUP5) {
        if (cfg.up5Type === 'weapon-guaranteed' || cfg.up5Type === 'weapon-guaranteed-collab') { tag = 'up'; }
        else if (cfg.up5Type === 'weapon-selected') { tag = 'selected'; }
        else if (cfg.up5Type === 'character-selected') { tag = 'selected'; }
        else if (cfg.up5Type === 'character' || cfg.up5Type === 'character-collab') {
          // 身份优先：先判断是否常驻角色，再判断保底状态
          if (STANDARD_5STAR_CHARS.has(r.name)) { tag = 'lost'; gs5 = 'big'; }
          else if (r.resourceType === '武器') { tag = 'lost'; gs5 = 'big'; }
          else if (gs5 === 'big') { tag = 'guaranteed'; gs5 = 'small'; }
          else { tag = 'up'; gs5 = 'small'; }
        }
      } else { tag = 'standard'; }
      s5.push({...r, pity:cur5, upTag:tag}); cur5=0;
    }

    if (r.qualityLevel===4) {
      let tag4='';
      if (cfg.hasUP4) {
        if (cfg.up4Type === 'character' || cfg.up4Type === 'character-collab') {
          if (gs4 === 'big') { tag4 = 'up4-guaranteed'; gs4 = 'small'; }
          else if (r.resourceType === '角色') { tag4 = 'char4'; }
          else { tag4 = 'lost4'; gs4 = 'big'; }
        } else if (cfg.up4Type === 'weapon' || cfg.up4Type === 'weapon-collab') {
          if (gs4 === 'big') { tag4 = 'up4-guaranteed'; gs4 = 'small'; }
          else if (r.resourceType === '武器') { tag4 = 'weapon4'; }
          else { tag4 = 'lost4'; gs4 = 'big'; }
        }
      } else { tag4 = 'normal4'; }
      s4.push({...r, pity:cur4, upTag4:tag4}); cur4=0;
    }
  }

  const n5=s5.length, n4=s4.length, n3=sorted.filter(r=>r.qualityLevel===3).length;
  const avg5 = n5 ? s5.reduce((s,r)=>s+r.pity,0)/n5 : 0;
  const min5 = n5 ? Math.min(...s5.map(r=>r.pity)) : 0;
  const max5 = n5 ? Math.max(...s5.map(r=>r.pity)) : 0;
  const dist={};
  for (const s of s5) { const b=Math.ceil(s.pity/10)*10; dist[b]=(dist[b]||0)+1; }
  const avg4 = n4 ? s4.reduce((s,r)=>s+r.pity,0)/n4 : 0;

  return { total:sorted.length, stars5:s5, stars4:s4, s5Count:n5, s4Count:n4, s3Count:n3,
    current5Pity:cur5, current4Pity:cur4, guaranteeState5:gs5, guaranteeState4:gs4,
    avgPity5:avg5, avgPity4:avg4, minPity5:min5, maxPity5:max5, pityDist:dist,
    pity5Max:cfg.pity5, pity4Max:cfg.pity4,
    hasUP5:cfg.hasUP5, up5Type:cfg.up5Type||'',
    hasUP4:cfg.hasUP4, up4Type:cfg.up4Type||'',
    crossPoolPity:cfg.crossPoolPity||'',
    poolName:cfg.name };
}

function renderOverview(all) {
  let tp=0, t5=0, t4=0, tap=0, pc=0;
  for (const [,a] of Object.entries(all)) {
    if (!a) continue; tp+=a.total; t5+=a.s5Count; t4+=a.s4Count;
    if (a.s5Count>0) { tap+=a.avgPity5*a.s5Count; pc+=a.s5Count; }
  }
  const avg=pc?(tap/pc).toFixed(1):'-';
  const label=pc?(avg<=40?'超级欧皇':avg<=50?'比较幸运':avg<=58?'正常水平':avg<=68?'有点非酋':'非酋本酋'):'暂无数据';
  const color=pc?(avg<=40?'var(--colorGreenText)':avg<=50?'var(--colorCyanText)':avg<=58?'var(--colorGoldText)':avg<=68?'var(--colorOrangeText)':'var(--colorRedText)'):'var(--colorNeutralForeground3)';
  document.getElementById('overview').innerHTML = `
    <div class="stat-card blue"><div class="label">总抽数</div><div class="value">${tp.toLocaleString()}</div><div class="sub">全部卡池</div></div>
    <div class="stat-card gold"><div class="label">5星总数</div><div class="value">${t5}</div><div class="sub">${tp?(t5/tp*100).toFixed(2):0}% 出率</div></div>
    <div class="stat-card purple"><div class="label">4星总数</div><div class="value">${t4}</div><div class="sub">${tp?(t4/tp*100).toFixed(2):0}% 出率</div></div>
    <div class="stat-card"><div class="label">欧非评价</div><div class="value" style="color:${color}">${label}</div><div class="sub">5星平均 ${avg} 抽出金</div></div>`;
}

function renderPoolTabs(all) {
  let html='', first=true;
  for (const pid of Object.keys(POOL_CONFIG)) {
    const a=all[pid], cnt=a?a.total:0;
    if (!cnt && !['1','2','4','5','6','10','11'].includes(pid)) continue;
    html+=`<div class="pool-tab ${first?'active':''}" data-pool="${pid}" onclick="switchPool('${pid}')">${POOL_CONFIG[pid].name}<span class="count">${cnt}</span></div>`;
    first=false;
  }
  document.getElementById('pool-tabs').innerHTML = html;
}

function renderPoolContent(pid, a) {
  const el = document.getElementById('pool-content');
  if (!a) { el.innerHTML='<div style="text-align:center;color:var(--colorNeutralForeground3);padding:40px">该卡池暂无抽卡记录</div>'; return; }

  const p5=a.s5Count?(a.s5Count/a.total*100).toFixed(2):'0.00';
  const p4=a.s4Count?(a.s4Count/a.total*100).toFixed(2):'0.00';
  const pity5pct=Math.min(100,a.current5Pity/a.pity5Max*100);
  const pity4pct=Math.min(100,a.current4Pity/a.pity4Max*100);

  let guHtml='';
  if (a.hasUP5) {
    if (a.up5Type === 'weapon-guaranteed') guHtml='<div class="pity-status no-up">武器活动池 — 5星必出UP武器</div>';
    else if (a.up5Type === 'weapon-guaranteed-collab') guHtml='<div class="pity-status no-up">武器联动池 — 5星必出UP武器</div>';
    else if (a.up5Type === 'weapon-selected') guHtml='<div class="pity-status no-up">5星必为自选武器</div>';
    else if (a.up5Type === 'character-selected') guHtml='<div class="pity-status no-up">5星必为自选角色</div>';
    else if (a.up5Type === 'character') guHtml=a.guaranteeState5==='big'?'<div class="pity-status big">大保底 — 下次5星必出UP角色</div>':'<div class="pity-status small">小保底 — 50%概率出UP角色</div>';
    else if (a.up5Type === 'character-collab') guHtml=a.guaranteeState5==='big'?'<div class="pity-status big">联动大保底 — 下次5星必出UP角色</div>':'<div class="pity-status small">联动小保底 — 50%概率出UP角色</div>';
  } else { guHtml='<div class="pity-status no-up">常驻池 — 无UP机制</div>'; }

  let gu4Html='';
  if (a.hasUP4) {
    if (a.up4Type === 'character') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP角色</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP角色</div>';
    else if (a.up4Type === 'weapon') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP武器</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP武器</div>';
    else if (a.up4Type === 'character-collab') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP角色(联动)</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP角色(联动)</div>';
    else if (a.up4Type === 'weapon-collab') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP武器(联动)</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP武器(联动)</div>';
  }

  let crossPoolNote='';
  if (a.crossPoolPity) {
    if (a.crossPoolPity === 'char-event') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数在所有「角色活动唤取」池间共享继承</div>';
    else if (a.crossPoolPity === 'weapon-event') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数在所有「武器活动唤取」池间共享继承</div>';
    else if (a.crossPoolPity === 'char-collab') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数仅在相同联动主题的「角色联动唤取」池间共享</div>';
    else if (a.crossPoolPity === 'weapon-collab') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数仅在相同联动主题的「武器联动唤取」池间共享</div>';
  }

  let distH='', lblH='';
  const mx=Math.max(...Object.values(a.pityDist),1);
  for (let b=10;b<=a.pity5Max;b+=10) { const c=a.pityDist[b]||0; distH+=`<div class="pity-bar-v" style="height:${c/mx*70}px"><div class="tip">${b-9}-${b}抽:${c}次</div></div>`; }
  for (let b=10;b<=a.pity5Max;b+=10) lblH+=`<span>${b%20===0?b:''}</span>`;

  // Probability calculations
  const nextP5 = get5StarPullProb(a.current5Pity, a.pity5Max);
  const nextP4 = get4StarPullProb(a.current4Pity, a.pity4Max);
  const cum10 = getCumulativeProb(a.current5Pity, a.pity5Max, 10);
  // Soft pity start for 80-pull pools = 70; others proportional
  const softStart5 = a.pity5Max === 80 ? 70 : Math.round(a.pity5Max * 0.8);
  const softStart4 = 7;
  const softZone5pct = ((a.pity5Max - softStart5) / a.pity5Max * 100).toFixed(1);
  const softZone4pct = ((a.pity4Max - softStart4) / a.pity4Max * 100).toFixed(1);
  const nextP5pct = (nextP5 * 100).toFixed(1);
  const nextP4pct = (nextP4 * 100).toFixed(1);
  const cum10pct = (cum10 * 100).toFixed(1);
  const isSoft5 = a.current5Pity >= softStart5;

  let s5rows='', s4rows='';
  const r5=[...a.stars5].reverse(), r4=[...a.stars4].reverse();
  for (let i=0;i<r5.length;i++) {
    const s=r5[i]; let tag='';
    if(s.upTag==='up')tag='<span class="tag up">UP</span>';
    else if(s.upTag==='lost')tag='<span class="tag lost">歪了</span>';
    else if(s.upTag==='guaranteed')tag='<span class="tag guaranteed">大保底出</span>';
    else if(s.upTag==='selected')tag='<span class="tag weapon-up">自选</span>';
    else if(s.upTag==='standard')tag='<span class="tag standard">常驻</span>';
    const ic=getIconUrl(s.resourceId);
    s5rows+=`<tr class="star5-row"><td class="td-num">${r5.length-i}</td><td class="td-icon">${ic?`<img src="${ic}" loading="lazy" alt="${s.name}" onerror="this.style.display='none'">`:''}</td><td class="td-name">${s.name}</td><td class="td-type">${s.resourceType}</td><td class="td-pity"><strong>${s.pity}</strong> 抽</td><td class="td-time">${s.time}</td><td class="td-tag">${tag}</td></tr>`;
  }
  for (let i=0;i<r4.length;i++) {
    const s=r4[i], ic=getIconUrl(s.resourceId);
    s4rows+=`<tr class="star4-row"><td class="td-num">${r4.length-i}</td><td class="td-icon">${ic?`<img src="${ic}" loading="lazy" alt="${s.name}" onerror="this.style.display='none'">`:''}</td><td class="td-name">${s.name}</td><td class="td-type">${s.resourceType}</td><td class="td-pity"><strong>${s.pity}</strong> 抽</td><td class="td-time">${s.time}</td><td class="td-tag-empty"></td></tr>`;
  }

  el.innerHTML = `
    <div class="pool-grid">
      <div class="fcard">
        <h3><svg class="fluent-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 3.5C2 2.67157 2.67157 2 3.5 2C4.32843 2 5 2.67157 5 3.5V12.5C5 13.3284 4.32843 14 3.5 14C2.67157 14 2 13.3284 2 12.5V3.5ZM3.5 3C3.22386 3 3 3.22386 3 3.5V12.5C3 12.7761 3.22386 13 3.5 13C3.77614 13 4 12.7761 4 12.5V3.5C4 3.22386 3.77614 3 3.5 3ZM6 6.5C6 5.67157 6.67157 5 7.5 5C8.32843 5 9 5.67157 9 6.5V12.5C9 13.3284 8.32843 14 7.5 14C6.67157 14 6 13.3284 6 12.5V6.5ZM7.5 6C7.22386 6 7 6.22386 7 6.5V12.5C7 12.7761 7.22386 13 7.5 13C7.77614 13 8 12.7761 8 12.5V6.5C8 6.22386 7.77614 6 7.5 6ZM11.5 8C10.6716 8 10 8.67157 10 9.5V12.5C10 13.3284 10.6716 14 11.5 14C12.3284 14 13 13.3284 13 12.5V9.5C13 8.67157 12.3284 8 11.5 8ZM11 9.5C11 9.22386 11.2239 9 11.5 9C11.7761 9 12 9.22386 12 9.5V12.5C12 12.7761 11.7761 13 11.5 13C11.2239 13 11 12.7761 11 12.5V9.5Z"/></svg>保底进度</h3>
        <div class="pity-item">
          <div class="pity-label">
            <span>5星保底</span>
            <span style="color:var(--colorGoldText);font-weight:700">${a.current5Pity} / ${a.pity5Max}</span>
          </div>
          <div class="pity-bar-track">
            <div class="pity-soft-zone gold" style="width:${softZone5pct}%"></div>
            <div class="pity-milestone" style="left:${(softStart5/a.pity5Max*100).toFixed(1)}%"><span class="pity-milestone-label">概率提升</span></div>
            <div class="pity-fill ${isSoft5?'gold hot':pity5pct>50?'red':'gold'}" style="width:${pity5pct}%"></div>
          </div>
          <div class="pity-prob-row">
            <span class="pity-prob-item ${isSoft5?'hot':''}">下抽出金 <strong>${nextP5pct}%</strong></span>
            <span class="pity-prob-item">10抽内出金 <strong>${cum10pct}%</strong></span>
          </div>
          ${guHtml}
          ${crossPoolNote}
        </div>
         <hr class="fui-divider">
         <div class="pity-item">
          <div class="pity-label"><span>4星保底</span><span style="color:var(--colorPurpleText);font-weight:700">${a.current4Pity} / ${a.pity4Max}</span></div>
          <div class="pity-bar-track">
            <div class="pity-soft-zone purple" style="width:${softZone4pct}%"></div>
            <div class="pity-fill purple" style="width:${pity4pct}%"></div>
          </div>
          <div class="pity-prob-row">
            <span class="pity-prob-item ${a.current4Pity>=softStart4?'hot':''}">下抽出4星 <strong>${nextP4pct}%</strong></span>
          </div>
          ${gu4Html}
        </div>
      </div>
      <div class="fcard">
        <h3><svg class="fluent-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M12 7C12 6.31641 12.343 5.71292 12.8662 5.35221C12.3102 2.8616 10.0868 1 7.42857 1C7.18656 1 7 1.20321 7 1.44522V6.5C7 6.77614 7.22386 7 7.5 7H12ZM8 2.03537C10.0678 2.29322 11.7068 3.93217 11.9646 6H8V2.03537ZM1 7.50095C1 10.3698 3.19681 12.7256 6 12.9778V11.9728C3.75008 11.7244 2 9.81706 2 7.50095C2 5.5424 3.25223 3.87461 5 3.25726V7.00005C5 8.10462 5.89543 9.00005 7 9.00005H7.9851L8 9L8.0149 9.00005H9C9 8.63579 9.09738 8.29422 9.26753 8.00005H7C6.44772 8.00005 6 7.55234 6 7.00005V2.5711C6 2.27727 5.74698 2.04482 5.45839 2.1001C2.91894 2.58657 1 4.81966 1 7.50095ZM14 6C13.4477 6 13 6.44771 13 7V14C13 14.5523 13.4477 15 14 15C14.5523 15 15 14.5523 15 14V7C15 6.44772 14.5523 6 14 6ZM11 8C10.4477 8 10 8.44772 10 9V14C10 14.5523 10.4477 15 11 15C11.5523 15 12 14.5523 12 14V9C12 8.44772 11.5523 8 11 8ZM7 11C7 10.4477 7.44772 10 8 10C8.55228 10 9 10.4477 9 11V14C9 14.5523 8.55228 15 8 15C7.44772 15 7 14.5523 7 14V11Z"/></svg>统计数据</h3>
        <div class="stats-grid">
          <div class="stat-item"><span class="label">总抽数</span><span class="val">${a.total}</span></div>
          <div class="stat-item"><span class="label">5星数量</span><span class="val" style="color:var(--colorGoldText)">${a.s5Count}</span></div>
          <div class="stat-item"><span class="label">4星数量</span><span class="val" style="color:var(--colorPurpleText)">${a.s4Count}</span></div>
          <div class="stat-item"><span class="label">3星数量</span><span class="val">${a.s3Count}</span></div>
          <div class="stat-item"><span class="label">5星出率</span><span class="val">${p5}%</span></div>
          <div class="stat-item"><span class="label">4星出率</span><span class="val">${p4}%</span></div>
          <div class="stat-item"><span class="label">5星平均抽数</span><span class="val">${a.avgPity5?a.avgPity5.toFixed(1):'-'}</span></div>
          <div class="stat-item"><span class="label">4星平均抽数</span><span class="val">${a.avgPity4?a.avgPity4.toFixed(1):'-'}</span></div>
          <div class="stat-item"><span class="label">最欧出金</span><span class="val" style="color:var(--colorGreenText)">${a.minPity5?a.minPity5+'抽':'-'}</span></div>
          <div class="stat-item"><span class="label">最非出金</span><span class="val" style="color:var(--colorRedText)">${a.maxPity5?a.maxPity5+'抽':'-'}</span></div>
          <div class="stat-item"><span class="label">距5星保底</span><span class="val" style="color:${a.pity5Max-a.current5Pity<=10?'var(--colorRedText)':'var(--colorNeutralForeground1)'}">${a.pity5Max-a.current5Pity}抽</span></div>
        </div>
      </div>
    </div>
    ${a.s5Count?`<hr class="fui-divider inset"><div class="fcard"><h3><svg class="fluent-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6.5 4V13H9.5V4C9.5 3.44772 9.05228 3 8.5 3H7.5C6.94772 3 6.5 3.44772 6.5 4ZM5.5 7V4C5.5 2.89543 6.39543 2 7.5 2H8.5C9.60457 2 10.5 2.89543 10.5 4V5H12C13.1046 5 14 5.89543 14 7V13.5C14 13.7761 13.7761 14 13.5 14H2.5C2.22386 14 2 13.7761 2 13.5V9C2 7.89543 2.89543 7 4 7H5.5ZM5.5 13V8H4C3.44772 8 3 8.44772 3 9V13H5.5ZM10.5 13H13V7C13 6.44772 12.5523 6 12 6H10.5V13Z"/></svg>5星保底分布</h3><div class="pity-dist">${distH}</div><div class="pity-labels">${lblH}</div></div>`:''}
    ${a.s5Count?`<hr class="fui-divider"><div class="history-section"><h3><svg class="fluent-icon" style="color:var(--colorGoldText)" width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M9.1 2.9a1 1 0 0 1 1.8 0l1.93 3.91 4.31.63a1 1 0 0 1 .56 1.7l-3.12 3.05.73 4.3a1 1 0 0 1-1.45 1.05L10 15.51l-3.86 2.03a1 1 0 0 1-1.45-1.05l.74-4.3L2.3 9.14a1 1 0 0 1 .56-1.7l4.31-.63L9.1 2.9Z"/></svg> 5星获取记录（共${a.s5Count}个）</h3><table class="ftable"><colgroup><col><col><col><col><col><col><col></colgroup><thead><tr><th>序号</th><th>角色</th><th>名称</th><th>类型</th><th>保底抽数</th><th>时间</th><th>标签</th></tr></thead><tbody>${s5rows}</tbody></table></div>`:''}
    ${a.s4Count?`<hr class="fui-divider"><div class="history-section"><h3><svg class="fluent-icon" style="color:var(--colorPurpleText)" width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M9.1 2.9a1 1 0 0 1 1.8 0l1.93 3.91 4.31.63a1 1 0 0 1 .56 1.7l-3.12 3.05.73 4.3a1 1 0 0 1-1.45 1.05L10 15.51l-3.86 2.03a1 1 0 0 1-1.45-1.05l.74-4.3L2.3 9.14a1 1 0 0 1 .56-1.7l4.31-.63L9.1 2.9Z"/></svg> 4星获取记录（共${a.s4Count}个）</h3><table class="ftable"><colgroup><col><col><col><col><col><col><col></colgroup><thead><tr><th>序号</th><th>角色</th><th>名称</th><th>类型</th><th>保底抽数</th><th>时间</th><th></th></tr></thead><tbody>${s4rows}</tbody></table></div>`:''}
    <hr class="fui-divider">
    ${renderRecordSection(pid, a)}
  `;
}

let allAnalysis={}, currentPool=null;
let recordViewMode = 'grid'; // 'grid' | 'timeline'

function renderRecordSection(pid, a) {
  if (!a || !a.total) return '';
  const cfg = POOL_CONFIG[pid] || { pity5: 80 };
  const nd = normalizeData(RAW_DATA);
  const records = (nd[pid] || []).slice().reverse(); // 时间升序

  // 预计算每条记录的pity和upTag（复用analyzePool结果）
  const s5Map = new Map();
  a.stars5.forEach(s => s5Map.set(s.time + '_' + s.resourceId, s));
  const s4Map = new Map();
  a.stars4.forEach(s => s4Map.set(s.time + '_' + s.resourceId, s));

  let curPity5 = 0;
  const enriched = records.map((r, i) => {
    curPity5++;
    let upTag = '', pity = 0;
    if (r.qualityLevel === 5) {
      const m = s5Map.get(r.time + '_' + r.resourceId);
      if (m) { upTag = m.upTag; pity = m.pity; }
      else { pity = curPity5; }
      curPity5 = 0;
    } else if (r.qualityLevel === 4) {
      const m = s4Map.get(r.time + '_' + r.resourceId);
      if (m) { pity = m.pity; }
    }
    return { ...r, idx: i + 1, upTag, pity, pityCount: curPity5 };
  });

  // 显示用时间倒序（最新在前），保底计算已基于时间升序完成
  const displayRecords = enriched.slice().reverse();

  // ── 宫格排列 ──
  let gridHtml = '<div class="grid-records">';
  for (const r of displayRecords) {
    const ic = getIconUrl(r.resourceId);
    const star = r.qualityLevel === 5 ? 'star5' : r.qualityLevel === 4 ? 'star4' : '';
    let tagHtml = '';
    if (r.upTag === 'up') tagHtml = '<span class="card-tag up">UP</span>';
    else if (r.upTag === 'lost') tagHtml = '<span class="card-tag lost">歪</span>';
    else if (r.upTag === 'guaranteed') tagHtml = '<span class="card-tag guaranteed">大</span>';
    // 所有卡片都显示保底抽数badge
    const badge = `<span class="card-badge">${r.qualityLevel === 5 ? r.pity : r.pityCount}</span>`;
    const starLabel = r.qualityLevel + '★';
    gridHtml += `<div class="grid-card ${star}" data-time="${r.time}" data-name="${r.name}" data-star="${starLabel}" data-type="${r.resourceType}">
      <div class="card-inner">
        ${badge}${tagHtml}
        ${ic ? `<img class="card-icon" src="${ic}" loading="lazy" alt="${r.name}" onerror="this.style.display='none'">` : '<div class="card-icon" style="background:var(--colorNeutralBackground3);border-radius:50%"></div>'}
        <span class="card-name">${r.name}</span>
      </div>
    </div>`;
  }
  gridHtml += '</div>';

  // ── 横向排列（保底进度条 + 5星图标，参考 mc.appfeng.com）──
  // 只展示5星保底进度可视化，不展示3/4星卡片
  let tlHtml = '';
  tlHtml += '<div class="tl-pity-timeline">';

  // 当前垫抽进度（顶部，无5星图标）
  if (a.current5Pity > 0) {
    const pct = Math.min(100, a.current5Pity / cfg.pity5 * 100);
    tlHtml += `<div class="tl-row">
      <div class="tl-bar-track">
        <div class="tl-bar-fill current" style="width:${pct}%"></div>
        <span class="tl-bar-text">已垫 ${a.current5Pity} 抽</span>
      </div>
      <div class="tl-bar-end"></div>
    </div>`;
  }

  // 按5星分组（倒序，最新5星在最上面）
  const s5list = a.stars5 ? [...a.stars5].reverse() : [];
  for (const s of s5list) {
    const pct = Math.min(100, s.pity / cfg.pity5 * 100);
    const ic = getIconUrl(s.resourceId);
    // 进度条颜色类型：UP=green, lost=orange, guaranteed=green-stripe, standard=neutral
    let barCls = 'up';
    let tagLabel = '';
    if (s.upTag === 'lost') { barCls = 'lost'; tagLabel = '<span class="tl-tag lost">歪</span>'; }
    else if (s.upTag === 'guaranteed') { barCls = 'guaranteed'; tagLabel = '<span class="tl-tag guaranteed">大保底</span>'; }
    else if (s.upTag === 'up') { tagLabel = '<span class="tl-tag up">UP</span>'; }
    else if (s.upTag === 'selected') { barCls = 'up'; tagLabel = '<span class="tl-tag up">自选</span>'; }
    else if (s.upTag === 'standard') { barCls = 'standard'; }

    tlHtml += `<div class="tl-row">
      <div class="tl-bar-track">
        <div class="tl-bar-fill ${barCls}" style="width:${pct}%"></div>
      </div>
      <div class="tl-bar-end">
        ${ic ? `<img class="tl-s5-icon" src="${ic}" loading="lazy" alt="${s.name}" onerror="this.style.display='none'">` : '<div class="tl-s5-icon placeholder"></div>'}
        <div class="tl-s5-info">
          <span class="tl-s5-name">${s.name}</span>
          <span class="tl-s5-pity">${s.pity}抽</span>
          ${tagLabel}
        </div>
      </div>
    </div>`;
  }
  tlHtml += '</div>';

  // 视图切换控件
  const toggleHtml = `
  <div class="fcard">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <h3 style="margin:0"><svg class="fluent-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M13 6C14.1046 6 15 5.10457 15 4C15 2.89543 14.1046 2 13 2C11.8954 2 11 2.89543 11 4C11 4.50867 11.1899 4.97298 11.5027 5.32592L9.96502 7.2478C9.67889 7.08987 9.34994 7 9 7C7.89543 7 7 7.89543 7 9C7 9.28876 7.0612 9.56323 7.17133 9.81114L4.66173 10.8867C4.30275 10.3519 3.69247 10 3 10C1.89543 10 1 10.8954 1 12C1 13.1046 1.89543 14 3 14C4.10457 14 5 13.1046 5 12C5 11.9436 4.99767 11.8878 4.9931 11.8326L7.82529 10.6188C8.15512 10.8586 8.56104 11 9 11C10.1046 11 11 10.1046 11 9C11 8.60935 10.888 8.24487 10.6944 7.9369L12.3347 5.88667C12.5428 5.96007 12.7667 6 13 6ZM13 5C12.4477 5 12 4.55228 12 4C12 3.44772 12.4477 3 13 3C13.5523 3 14 4.55228 14 4C14 4.55228 13.5523 5 13 5ZM4 12C4 12.5523 3.55228 13 3 13C2.44772 13 2 12.5523 2 12C2 11.4477 2.44772 11 3 11C3.55228 11 4 11.4477 4 12ZM10 9C10 9.55228 9.55228 10 9 10C8.44772 10 8 9.55228 8 9C8 8.44772 8.44772 8 9 8C9.55228 8 10 8.44772 10 9Z"/></svg>抽卡记录</h3>
      <div class="record-view-toggle">
        <button class="toggle-btn ${recordViewMode==='grid'?'active':''}" onclick="switchRecordView('grid','${pid}')">
          <svg viewBox="0 0 20 20" fill="currentColor"><path d="M7 2H3a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1V3a1 1 0 00-1-1zM7 12H3a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1v-4a1 1 0 00-1-1zM17 2h-4a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1V3a1 1 0 00-1-1zM17 12h-4a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1v-4a1 1 0 00-1-1z"/></svg>
          宫格
        </button>
        <button class="toggle-btn ${recordViewMode==='timeline'?'active':''}" onclick="switchRecordView('timeline','${pid}')">
          <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2.5 4a.5.5 0 000 1h15a.5.5 0 000-1h-15zM2.5 9a.5.5 0 000 1h15a.5.5 0 000-1h-15zM2.5 14a.5.5 0 000 1h15a.5.5 0 000-1h-15z"/></svg>
          横向
        </button>
      </div>
      <span style="font-size:12px;color:var(--colorNeutralForeground3)">${enriched.length} 条记录</span>
    </div>
    <div id="record-body-${pid}">
      ${recordViewMode === 'grid' ? gridHtml : tlHtml}
    </div>
  </div>`;

  return toggleHtml;
}

function switchRecordView(mode, pid) {
  recordViewMode = mode;
  renderPoolContent(pid, allAnalysis[pid]);
}

// ── Fluent UI 2 Tooltip + 同时间高亮（聚焦模式）──
let gachaTooltip = null;
let currentHoveredCard = null;
let gridFocusTimeout = null;

function initTooltip() {
  if (gachaTooltip) return;
  gachaTooltip = document.createElement('div');
  gachaTooltip.className = 'gacha-tooltip';
  gachaTooltip.style.pointerEvents = 'none';
  document.body.appendChild(gachaTooltip);

  // ── 聚焦模式：用 pointerenter/pointerleave 在 .grid-records 上 ──
  // 使用事件委托，检测鼠标是否进入了宫格区域
  document.addEventListener('pointerover', e => {
    const grid = e.target.closest ? e.target.closest('.grid-records') : null;
    if (grid) {
      clearTimeout(gridFocusTimeout);
      grid.classList.add('in-focus');
    }
  });

  document.addEventListener('pointerout', e => {
    const grid = e.target.closest ? e.target.closest('.grid-records') : null;
    if (!grid) return;
    // 检查 relatedTarget 是否还在 grid 内
    const related = e.relatedTarget;
    if (related) {
      const stillInGrid = related.closest ? related.closest('.grid-records') : null;
      if (stillInGrid === grid) return; // 还在grid内，不取消聚焦
    }
    // 真正离开了grid，延迟100ms取消（避免鼠标快速经过间隙）
    clearTimeout(gridFocusTimeout);
    gridFocusTimeout = setTimeout(() => {
      grid.classList.remove('in-focus');
      clearSameTimeHighlight(grid);
      hideTooltip();
      currentHoveredCard = null;
    }, 100);
  });

  // ── 滚动时隐藏tooltip，避免脱离卡片 ──
  document.addEventListener('scroll', () => {
    if (gachaTooltip && gachaTooltip.classList.contains('visible')) {
      hideTooltip();
      currentHoveredCard = null;
    }
  }, true);

  // ── hover卡片 → 高亮同时间 + tooltip ──
  document.addEventListener('pointerover', e => {
    const card = e.target.closest ? e.target.closest('.grid-card') : null;
    if (!card || !card.dataset.time) {
      if (currentHoveredCard) {
        // 鼠标从卡片移到宫格内非卡片区域，取消高亮但保留聚焦
        clearSameTimeHighlight();
        hideTooltip();
        currentHoveredCard = null;
      }
      return;
    }
    if (card === currentHoveredCard) return;
    currentHoveredCard = card;
    clearSameTimeHighlight();
    highlightSameTime(card.dataset.time);
    hideTooltip();
    showCardTooltip(card, e);
  });
}

function highlightSameTime(time) {
  document.querySelectorAll('.grid-records').forEach(container => {
    container.querySelectorAll('.grid-card').forEach(card => {
      if (card.dataset.time === time) {
        card.classList.add('same-time-highlight');
      }
    });
  });
}

function clearSameTimeHighlight(scope) {
  const roots = scope ? [scope] : document.querySelectorAll('.grid-records');
  roots.forEach(c => c.querySelectorAll('.same-time-highlight').forEach(card => card.classList.remove('same-time-highlight')));
}

function showCardTooltip(card, event) {
  if (!gachaTooltip) return;
  const name = card.dataset.name || '';
  const star = card.dataset.star || '';
  const rtype = card.dataset.type || '';
  const time = card.dataset.time || '';
  const starCls = star === '5★' ? 'tt-star5' : star === '4★' ? 'tt-star4' : '';

  gachaTooltip.innerHTML = `<div class="${starCls}">
    <div class="tt-name">${name}</div>
    <div class="tt-meta">${star} ${rtype}</div>
    <div class="tt-meta">${time}</div>
  </div>`;

  // 定位
  const rect = card.getBoundingClientRect();
  let left = rect.left + rect.width / 2;
  let top = rect.top - 8;
  gachaTooltip.style.left = left + 'px';
  gachaTooltip.style.top = top + 'px';
  gachaTooltip.style.transform = 'translate(-50%, -100%) translateY(4px)';

  // 先显示获取尺寸
  gachaTooltip.classList.add('visible');
  requestAnimationFrame(() => {
    gachaTooltip.style.transform = 'translate(-50%, -100%)';
  });

  // 边界检测
  const ttRect = gachaTooltip.getBoundingClientRect();
  if (ttRect.top < 4) {
    top = rect.bottom + 8;
    gachaTooltip.style.top = top + 'px';
    gachaTooltip.style.transform = 'translate(-50%, 0)';
  }
  if (ttRect.left < 4) {
    gachaTooltip.style.left = (4 + ttRect.width / 2) + 'px';
  }
  const vpWidth = window.innerWidth;
  if (ttRect.right > vpWidth - 4) {
    gachaTooltip.style.left = (vpWidth - 4 - ttRect.width / 2) + 'px';
  }
}

function hideTooltip() {
  if (gachaTooltip) gachaTooltip.classList.remove('visible');
}




function switchPool(pid) {
  clearSameTimeHighlight();
  hideTooltip();
  document.querySelectorAll('.pool-tab').forEach(t=>t.classList.toggle('active',t.dataset.pool===pid));
  currentPool=pid; renderPoolContent(pid,allAnalysis[pid]);
}

function renderAll() {
  const data=normalizeData(RAW_DATA);
  allAnalysis={};
  for (const pid of Object.keys(POOL_CONFIG)) {
    const r=data[pid]||[];
    allAnalysis[pid]=r.length?analyzePool(r,pid):null;
  }
  renderOverview(allAnalysis);
  renderPoolTabs(allAnalysis);
  const first=document.querySelector('.pool-tab');
  if (first) switchPool(first.dataset.pool);

  // Update header meta
  const uid = RAW_DATA.uid || '-';
  const now = new Date().toISOString().slice(0,10);
  document.getElementById('header-meta').textContent = `UID: ${uid} | 数据截至: ${now}`;
}

function loadAndRender() {
  fetch('/api/data')
    .then(r => r.json())
    .then(resp => {
      if (resp.ok) {
        RAW_DATA = resp.data;
        ICON_MAP = resp.icons || {};
        renderAll();
      }
    });
}

// Init: load data from server
loadAndRender();
initTooltip();
</script>
<div class="toast-container" id="toast-container"></div>
<footer class="site-footer">
  <a href="https://github.com/BJY-STUDIO/wuwa-gacha-analyzer" target="_blank" rel="noopener noreferrer"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg> BJY-STUDIO</a>
  <span class="sep">|</span>
  <a href="https://bjy-studio.github.io/" target="_blank" rel="noopener noreferrer">Blog</a>
</footer>
</body>
</html>"""

# ============================================================
# Flask App
# ============================================================
app = Flask(__name__)

@app.route('/')
def index():
    """上传页"""
    return render_template_string(UPLOAD_PAGE)

@app.route('/analysis')
def analysis():
    """分析页"""
    return render_template_string(ANALYSIS_PAGE)

@app.route('/icons/<path:filename>')
def serve_icon(filename):
    """图标文件服务（支持 png/webp）"""
    response = send_from_directory(os.path.join(DATA_DIR, 'icons'), filename)
    if filename.endswith('.webp'):
        response.headers['Content-Type'] = 'image/webp'
    return response

@app.route('/api/data')
def api_data():
    """返回当前数据和图标映射"""
    global current_data, current_icon_map
    if current_data is None:
        return jsonify({"ok": False, "error": "暂无数据，请先上传抽卡记录"})
    return jsonify({
        "ok": True,
        "data": current_data,
        "icons": current_icon_map,
        "total": count_records(current_data)
    })

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传抽卡记录JSON"""
    global current_data, current_icon_map

    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "未找到文件"})

    f = request.files['file']
    if not f.filename.endswith('.json'):
        return jsonify({"ok": False, "error": "仅支持 .json 文件"})

    try:
        raw = json.load(f.stream)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "JSON 格式错误"})

    # 验证数据结构：至少有一个池键
    has_pool = any(isinstance(v, list) and len(v) > 0 for k, v in raw.items() if k != 'uid')
    if not has_pool:
        return jsonify({"ok": False, "error": "未找到有效的抽卡记录数据"})

    # 保存文件
    uid = raw.get("uid", "unknown")
    now = datetime.datetime.now()
    filename = f"uid_{uid}_{now.strftime('%Y-%m-%d_%H%M%S')}.json"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as out:
        json.dump(raw, out, ensure_ascii=False)

    # 更新数据（确保每个池子按时间严格倒序）
    for k, v in raw.items():
        if k == "uid" or not isinstance(v, list):
            continue
        # 修复已知的时间戳异常（如 "2024s-" → "2024-"）
        for r in v:
            t = r.get("time", "")
            if "2024s-" in t:
                r["time"] = t.replace("2024s-", "2024-")
        raw[k] = _stable_sort_desc(v)
    current_data = raw
    current_icon_map = cache_icons(raw)

    total = count_records(raw)
    print(f"  上传成功: {filename} ({total}条记录)")
    return jsonify({"ok": True, "total": total, "uid": uid})

# ============================================================
# 从游戏API抓取抽卡记录（集成 wuwa_gacha.py 功能）
# ============================================================
FETCH_API_CN = "https://gmserver-api.aki-game2.com/gacha/record/query"
FETCH_API_GLOBAL = "https://gmserver-api.aki-game2.net/gacha/record/query"
FETCH_POOL_NAMES = {
    "1": "角色活动唤取", "2": "武器活动唤取",
    "3": "角色常驻唤取", "4": "武器常驻唤取",
    "5": "新手唤取", "6": "新手自选唤取",
    "7": "感恩定向唤取", "8": "角色新旅唤取",
    "9": "武器新旅唤取", "10": "角色联动唤取",
    "11": "武器联动唤取",
}

@app.route('/api/news')
def api_news():
    """从库洛社区 API 抓取鸣潮官方资讯前6条"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
            'Origin': 'https://www.kurobbs.com',
            'Referer': 'https://www.kurobbs.com/',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'devcode': 'FxT0lIpYOMEz28v6RFIwG3mBsttzX1WK',
            'source': 'h5',
            'version': '3.0.4',
            'token': '',
        }
        body = {'eventType': '2', 'gameId': '3', 'pageNo': '1', 'pageSize': '6'}
        resp = req_lib.post('https://api.kurobbs.com/forum/companyEvent/findEventList',
                            data=body, headers=headers, timeout=10)
        data = resp.json()
        if data.get('code') != 200:
            return jsonify({'ok': False, 'news': []})
        items = data.get('data', {}).get('list', [])
        news = []
        for item in items[:6]:
            post_id = item.get('postId', '')
            ts = item.get('publishTime', 0)
            news.append({
                'title': item.get('postTitle', ''),
                'img': item.get('coverUrl', ''),
                'date': datetime.datetime.fromtimestamp(ts / 1000).strftime('%m-%d') if ts else '',
                'url': f'https://www.kurobbs.com/mc/post/{post_id}' if post_id else ''
            })
        return jsonify({'ok': True, 'news': news})
    except Exception as e:
        return jsonify({'ok': False, 'news': [], 'error': str(e)})

@app.route('/api/official')
def api_official():
    """从库洛社区 API 抓取鸣潮官方资讯/公告列表（分页）"""
    event_type = request.args.get('type', '2')  # 2=资讯, 3=公告
    page_no = request.args.get('page', '1')
    page_size = request.args.get('size', '8')
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
            'Origin': 'https://www.kurobbs.com',
            'Referer': 'https://www.kurobbs.com/',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'devcode': 'FxT0lIpYOMEz28v6RFIwG3mBsttzX1WK',
            'source': 'h5',
            'version': '3.0.4',
            'token': '',
        }
        body = {'eventType': event_type, 'gameId': '3',
                'pageNo': page_no, 'pageSize': page_size}
        resp = req_lib.post('https://api.kurobbs.com/forum/companyEvent/findEventList',
                            data=body, headers=headers, timeout=10, verify=False)
        data = resp.json()
        if data.get('code') != 200:
            return jsonify({'ok': False, 'list': [], 'hasMore': False})
        page_data = data.get('data', {})
        items = page_data.get('list', [])
        result = []
        for item in items:
            post_id = item.get('postId', '')
            ts = item.get('publishTime', 0)
            result.append({
                'id': item.get('id', ''),
                'postId': post_id,
                'title': item.get('postTitle', ''),
                'img': item.get('coverUrl', ''),
                'date': datetime.datetime.fromtimestamp(ts / 1000).strftime('%m-%d') if ts else '',
                'url': f'https://www.kurobbs.com/mc/post/{post_id}' if post_id else '',
                'eventType': item.get('eventType', 0),
            })
        has_more = page_data.get('hasNextPage', False)
        return jsonify({'ok': True, 'list': result, 'hasMore': has_more})
    except Exception as e:
        return jsonify({'ok': False, 'list': [], 'hasMore': False, 'error': str(e)})
def api_fetch():
    """从游戏API抓取抽卡记录 — SSE 流式返回逐池进度"""
    global current_data, current_icon_map

    body = request.get_json(silent=True) or {}
    creds = body.get('creds')
    if not creds:
        return jsonify({"ok": False, "error": "未提供凭证"})

    # 验证必要字段
    required = ["recordId", "playerId", "serverId", "cardPoolId"]
    missing = [f for f in required if f not in creds]
    if missing:
        return jsonify({"ok": False, "error": f"缺少必要字段: {', '.join(missing)}"})

    player_id = str(creds["playerId"])
    svr_area = creds.get("svr_area", "cn")
    api_base = FETCH_API_CN if svr_area == "cn" else FETCH_API_GLOBAL

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    def generate():
        result = {"uid": player_id}
        total = 0
        pool_results = []

        for pool_type in range(1, 12):
            pool_name = FETCH_POOL_NAMES.get(str(pool_type), str(pool_type))
            # 推送"正在获取"进度
            yield f"data: {json.dumps({'type': 'progress', 'pool': pool_name, 'index': pool_type, 'total_pools': 11}, ensure_ascii=False)}\n\n"

            count = 0
            try:
                resp = req_lib.post(api_base, json={
                    "recordId": creds["recordId"],
                    "playerId": creds["playerId"],
                    "serverId": creds["serverId"],
                    "cardPoolId": creds["cardPoolId"],
                    "cardPoolType": pool_type,
                    "languageCode": creds.get("languageCode", "zh-Hans"),
                }, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and (data.get("code") in (0, 200) or data.get("message") == "成功"):
                    records = data.get("data", [])
                    if isinstance(records, list):
                        transformed = []
                        for r in records:
                            raw_cpt = r.get("cardPoolType", str(pool_type))
                            transformed.append({
                                "cardPoolType": raw_cpt,
                                "resourceId": r.get("resourceId", 0),
                                "qualityLevel": r.get("qualityLevel", 0),
                                "resourceType": r.get("resourceType", ""),
                                "name": r.get("name", ""),
                                "count": r.get("count", 1),
                                "time": r.get("time", ""),
                            })
                        result[str(pool_type)] = transformed
                        count = len(transformed)
                        total += count
            except Exception:
                pass

            pool_results.append({"pool": pool_name, "count": count})
            # 推送"获取完成"结果
            yield f"data: {json.dumps({'type': 'result', 'pool': pool_name, 'count': count, 'index': pool_type, 'total_pools': 11}, ensure_ascii=False)}\n\n"

            if pool_type < 11:
                time.sleep(0.5)

        if total == 0:
            yield f"data: {json.dumps({'type': 'error', 'error': '未获取到任何记录，请检查凭证是否正确或已过期'}, ensure_ascii=False)}\n\n"
            return

        # 保存
        now = datetime.datetime.now()
        filename = f"uid_{player_id}_{now.strftime('%Y-%m-%d_%H%M%S')}.json"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)

        # 排序+修复
        for k, v in result.items():
            if k == "uid" or not isinstance(v, list):
                continue
            for r in v:
                t = r.get("time", "")
                if "2024s-" in t:
                    r["time"] = t.replace("2024s-", "2024-")
            result[k] = _stable_sort_desc(v)

        current_data = result
        current_icon_map = cache_icons(result)

        print(f"  抓取成功: {filename} ({total}条记录)")
        # 推送最终完成
        yield f"data: {json.dumps({'type': 'done', 'ok': True, 'total': total, 'uid': player_id, 'pools': pool_results, 'filename': filename}, ensure_ascii=False)}\n\n"

    return app.response_class(generate(), mimetype='text/event-stream',
                              headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/load')
def api_load():
    """从已保存的文件加载数据到内存（SSE完成后由前端调用）"""
    global current_data, current_icon_map

    filename = request.args.get('file', '')
    if not filename:
        return jsonify({"ok": False, "error": "未指定文件名"})

    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"ok": False, "error": "文件不存在"})

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "error": f"文件读取失败: {e}"})

    # 排序+修复
    for k, v in raw.items():
        if k == "uid" or not isinstance(v, list):
            continue
        for r in v:
            t = r.get("time", "")
            if "2024s-" in t:
                r["time"] = t.replace("2024s-", "2024-")
        raw[k] = _stable_sort_desc(v)

    current_data = raw
    current_icon_map = cache_icons(raw)
    total = count_records(raw)
    print(f"  加载成功: {filename} ({total}条记录)")
    return jsonify({"ok": True, "total": total, "uid": raw.get("uid", "unknown")})

@app.route('/api/merge', methods=['POST'])
def api_merge():
    """合并历史抽卡记录"""
    global current_data, current_icon_map

    if current_data is None:
        return jsonify({"ok": False, "error": "请先上传主抽卡记录"})

    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "未找到文件"})

    f = request.files['file']
    if not f.filename.endswith('.json'):
        return jsonify({"ok": False, "error": "仅支持 .json 文件"})

    try:
        history = json.load(f.stream)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "JSON 格式错误"})

    has_pool = any(isinstance(v, list) and len(v) > 0 for k, v in history.items() if k != 'uid')
    if not has_pool:
        return jsonify({"ok": False, "error": "未找到有效的抽卡记录数据"})

    old_total = count_records(current_data)

    # 保留上传文件
    uid = history.get("uid", "unknown")
    now = datetime.datetime.now()
    filename = f"uid_{uid}_{now.strftime('%Y-%m-%d_%H%M%S')}_history.json"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as out:
        json.dump(history, out, ensure_ascii=False)

    # 执行合并：当前数据为主，历史数据为辅助
    merged = merge_data(current_data, history)

    # 保存合并结果
    merged_filename = f"uid_{uid}_{now.strftime('%Y-%m-%d_%H%M%S')}_merged.json"
    merged_filepath = os.path.join(UPLOAD_DIR, merged_filename)
    with open(merged_filepath, "w", encoding="utf-8") as out:
        json.dump(merged, out, ensure_ascii=False)

    # 更新内存数据
    current_data = merged
    current_icon_map = cache_icons(merged)

    new_total = count_records(merged)
    history_total = count_records(history)
    print(f"  合并完成: 原{old_total}条 + 历史{history_total}条 = 合并后{new_total}条")

    return jsonify({
        "ok": True,
        "total": new_total,
        "old_total": old_total,
        "history_total": history_total,
        "uid": uid
    })

@app.route('/api/download')
def api_download():
    """下载当前合并后的抽卡记录JSON"""
    if current_data is None:
        return jsonify({"ok": False, "error": "暂无数据"}), 404
    uid = current_data.get("uid", "unknown")
    now = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"uid_{uid}_{now}_merged.json"
    resp = jsonify(current_data)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp

# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='鸣潮抽卡分析 - 本地Web服务')
    parser.add_argument('--port', type=int, default=8766, help='服务端口 (默认8766)')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    args = parser.parse_args()

    print("=" * 50)
    print("  鸣潮抽卡分析 - 本地Web服务")
    print("=" * 50)
    print(f"  访问地址: http://localhost:{args.port}")
    print(f"  数据目录: {DATA_DIR}")
    print(f"  上传目录: {UPLOAD_DIR}")
    print("=" * 50)

    # 启动时预加载 encore.moe 备用图标映射
    load_encore_mapping()

    app.run(host='127.0.0.1', port=args.port, debug=args.debug)
