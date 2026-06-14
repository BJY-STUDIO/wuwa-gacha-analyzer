#!/usr/bin/env python3
"""生成鸣潮抽卡分析报告HTML（Fluent 2 设计规范，深浅主题+本地图标缓存）"""
import json, os, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = r"C:\Users\Administrator\Documents\抽卡分析"
ICONS_CHAR_DIR = os.path.join(DATA_DIR, "icons", "characters")
ICONS_WEAPON_DIR = os.path.join(DATA_DIR, "icons", "weapons")
CDN_CHAR_BASE = "https://files.wuthery.com/p/GameData/IDFiedResources/Common/Image/IconRoleHead256"
CDN_WEAPON_BASE = "https://files.wuthery.com/p/GameData/IDFiedResources/Common/Image/IconWeapon80"
JSON_FILE = os.path.join(DATA_DIR, "uid_100018154_2026-06-13.json")

if len(sys.argv) > 1:
    JSON_FILE = sys.argv[1]

# 自动查找该UID最新的JSON文件
json_files = [f for f in os.listdir(DATA_DIR) if f.startswith("uid_") and f.endswith(".json")]
json_files.sort(reverse=True)
if json_files:
    latest = os.path.join(DATA_DIR, json_files[0])
    if not os.path.exists(JSON_FILE) or latest != JSON_FILE:
        JSON_FILE = latest

with open(JSON_FILE, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

uid = raw_data.get("uid", "unknown")

fname_base = json_files[0].replace(".json", "") if json_files else ""
parts = fname_base.split("_")
date_str = parts[2] if len(parts) >= 3 else "unknown"

# ============================================================
# Download & cache icons locally (parallel)
# ============================================================
def ensure_icon_dirs():
    os.makedirs(ICONS_CHAR_DIR, exist_ok=True)
    os.makedirs(ICONS_WEAPON_DIR, exist_ok=True)

def download_icon(resource_id, resource_type):
    if not resource_id:
        return (resource_id, "")
    rid = str(resource_id)
    if resource_type == "\u89d2\u8272":
        local_dir, url = ICONS_CHAR_DIR, f"{CDN_CHAR_BASE}/{rid}.png"
    else:
        local_dir, url = ICONS_WEAPON_DIR, f"{CDN_WEAPON_BASE}/{rid}.png"
    local_path = os.path.join(local_dir, f"{rid}.png")
    if os.path.exists(local_path):
        return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                with open(local_path, "wb") as f:
                    f.write(resp.read())
                return (resource_id, os.path.relpath(local_path, DATA_DIR).replace("\\", "/"))
    except Exception:
        pass
    return (resource_id, "")

ensure_icon_dirs()
items_to_cache = set()
for key, records in raw_data.items():
    if not isinstance(records, list):
        continue
    for r in records:
        if r.get("qualityLevel") in [4, 5] and r.get("resourceId"):
            items_to_cache.add((r["resourceId"], r.get("resourceType", "")))

print(f"需要缓存 {len(items_to_cache)} 个图标，并行下载中...")
icon_map = {}
with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(download_icon, rid, rtype): rid for rid, rtype in items_to_cache}
    for future in as_completed(futures):
        rid, local_path = future.result()
        if local_path:
            icon_map[rid] = local_path
print(f"图标缓存完成，成功 {len(icon_map)}/{len(items_to_cache)} 个")

icon_map_str = json.dumps(icon_map, ensure_ascii=False)
json_str = json.dumps(raw_data, ensure_ascii=False)

html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>鸣潮抽卡分析 | UID:{uid}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fluentui/web-components@2/dist/web-components.min.css" onerror="this.remove()">
<script type="module" src="https://cdn.jsdelivr.net/npm/@fluentui/web-components@2/dist/web-components.min.js"></script>
<style>
/* =============================================
   Fluent 2 Design Token System
   基于 @fluentui/react-theme 官方色值
   ============================================= */

/* --- Light Theme (default if system prefers light) --- */
:root,
[data-theme="light"] {{
  /* Neutral Background */
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
  /* Neutral Foreground */
  --colorNeutralForeground1: #141414;
  --colorNeutralForeground1Hover: #242424;
  --colorNeutralForeground2: #616161;
  --colorNeutralForeground2Hover: #717171;
  --colorNeutralForeground3: #9e9e9e;
  --colorNeutralForeground4: #b3b3b3;
  --colorNeutralForegroundDisabled: #bdbdbd;
  /* Neutral Stroke */
  --colorNeutralStroke1: #d1d1d1;
  --colorNeutralStroke1Hover: #c4c4c4;
  --colorNeutralStroke2: #e0e0e0;
  --colorNeutralStroke3: #ebebeb;
  --colorNeutralStrokeAccessible: #616161;
  --colorNeutralStrokeAccessibleHover: #575757;
  --colorNeutralStrokeAccessiblePressed: #4d4d4d;
  /* Brand */
  --colorCompoundBrandStroke: #0078d4;
  --colorCompoundBrandBackground: #0f6cbd;
  --colorCompoundBrandBackgroundHover: #115ea3;
  --colorCompoundBrandBackgroundPressed: #0f548c;
  --colorNeutralForegroundInverted: #ffffff;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandForeground1: #0078d4;
  --colorBrandForeground2: #106ebe;
  /* Shadow */
  --shadow2: 0 1px 2px rgba(0,0,0,0.10), 0 2px 6px rgba(0,0,0,0.06);
  --shadow4: 0 2px 4px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.08);
  --shadow8: 0 4px 8px rgba(0,0,0,0.10), 0 8px 24px rgba(0,0,0,0.10);
  /* Semantic - game specific */
  --colorGold: #d4a017;
  --colorGoldSubtle: #fef6e0;
  --colorGoldText: #8a6914;
  --colorPurple: #8764b8;
  --colorPurpleSubtle: #f3eaf9;
  --colorPurpleText: #6b3f9e;
  --colorRed: #d13438;
  --colorRedSubtle: #fde7e9;
  --colorRedText: #a4262c;
  --colorGreen: #107c10;
  --colorGreenSubtle: #dff6dd;
  --colorGreenText: #0b6a0b;
  --colorCyan: #038387;
  --colorCyanSubtle: #d0f0f1;
  --colorCyanText: #036c6f;
  --colorOrange: #ca5010;
  --colorOrangeSubtle: #fed9cc;
  --colorOrangeText: #9e4708;
  /* pity bar backgrounds */
  --pityBarGold: linear-gradient(90deg, #c49011, #d4a017, #e5b82a);
  --pityBarPurple: linear-gradient(90deg, #6b3f9e, #8764b8, #a278d0);
  --pityBarRed: linear-gradient(90deg, #a4262c, #d13438, #e85050);
}}

/* --- Dark Theme --- */
[data-theme="dark"] {{
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
  --colorCompoundBrandBackground: #479ef5;
  --colorCompoundBrandBackgroundHover: #62abf5;
  --colorCompoundBrandBackgroundPressed: #2886de;
  --colorNeutralForegroundInverted: #242424;
  --colorBrandBackground: #0078d4;
  --colorBrandBackgroundHover: #106ebe;
  --colorBrandBackgroundPressed: #005a9e;
  --colorBrandForeground1: #62abf5;
  --colorBrandForeground2: #74b5f7;
  --shadow2: 0 1px 2px rgba(0,0,0,0.28), 0 2px 6px rgba(0,0,0,0.20);
  --shadow4: 0 2px 4px rgba(0,0,0,0.22), 0 4px 12px rgba(0,0,0,0.24);
  --shadow8: 0 4px 8px rgba(0,0,0,0.26), 0 8px 24px rgba(0,0,0,0.30);
  --colorGold: #f0b429;
  --colorGoldSubtle: #3d3019;
  --colorGoldText: #f0b429;
  --colorPurple: #b77dff;
  --colorPurpleSubtle: #2d1f4a;
  --colorPurpleText: #b77dff;
  --colorRed: #ff6b6b;
  --colorRedSubtle: #3d1a1a;
  --colorRedText: #ff6b6b;
  --colorGreen: #51cf66;
  --colorGreenSubtle: #1a3d20;
  --colorGreenText: #51cf66;
  --colorCyan: #22b8cf;
  --colorCyanSubtle: #1a2e2e;
  --colorCyanText: #22b8cf;
  --colorOrange: #ff922b;
  --colorOrangeSubtle: #2e2a1a;
  --colorOrangeText: #ff922b;
  --pityBarGold: linear-gradient(90deg, #8b6914, #c49520, var(--colorGold));
  --pityBarPurple: linear-gradient(90deg, #5f3d8f, #8b5dcf, var(--colorPurple));
  --pityBarRed: linear-gradient(90deg, #8b2020, #d43d3d, #ff5555);
}}

/* =============================================
   Base
   ============================================= */
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: var(--colorNeutralBackground3);
  color: var(--colorNeutralForeground1);
  line-height: 1.5;
  min-height: 100vh;
  transition: background 0.3s ease, color 0.3s ease;
}}
a {{ color: var(--colorBrandForeground1); text-decoration: none; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 0 24px; }}

/* =============================================
   Header
   ============================================= */
.header {{
  background: var(--colorNeutralBackground1);
  border-bottom: 1px solid var(--colorNeutralStroke2);
  padding: 20px 0;
  position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(20px);
  transition: background 0.3s ease;
}}
.header-inner {{ display: flex; justify-content: space-between; align-items: center; }}
.header h1 {{
  font-size: 22px; font-weight: 600;
  color: var(--colorBrandForeground1);
}}
.header .meta {{ color: var(--colorNeutralForeground2); font-size: 13px; margin-top: 2px; }}

/* Theme switcher */
.theme-switch {{
  display: flex; align-items: center; gap: 8px;
  color: var(--colorNeutralForeground2); font-size: 14px;
}}
.theme-toggle {{
  position: relative; width: 40px; height: 20px;
  background: transparent;
  border-radius: 10px; cursor: pointer;
  border: 1px solid var(--colorNeutralStrokeAccessible);
  transition: all 0.2s ease;
  flex-shrink: 0;
}}
.theme-toggle::after {{
  content: ''; position: absolute;
  top: 2px; left: 2px;
  width: 14px; height: 14px;
  background: var(--colorNeutralStrokeAccessible);
  border-radius: 50%;
  transition: all 0.2s ease;
}}
.theme-toggle:focus-visible {{ box-shadow: 0 0 0 2px var(--colorCompoundBrandStroke); }}
.theme-toggle:hover {{
  border-color: var(--colorNeutralStrokeAccessibleHover);
}}
.theme-toggle:hover::after {{ background: var(--colorNeutralStrokeAccessibleHover); }}
.theme-toggle:active {{ border-color: var(--colorNeutralStrokeAccessiblePressed); }}
.theme-toggle:active::after {{ background: var(--colorNeutralStrokeAccessiblePressed); }}
.theme-toggle.active {{
  background: var(--colorCompoundBrandBackground);
  border-color: transparent;
}}
.theme-toggle.active::after {{
  left: 22px;
  background: var(--colorNeutralForegroundInverted);
}}
.theme-toggle.active:hover {{
  background: var(--colorCompoundBrandBackgroundHover);
}}
.theme-toggle.active:active {{
  background: var(--colorCompoundBrandBackgroundPressed);
}}
.theme-icon {{ font-size: 16px; line-height: 1; transition: opacity 0.2s ease; display: flex; align-items: center; }}
svg.fluent-icon {{ vertical-align: middle; flex-shrink: 0; }}

/* =============================================
   Overview Cards (Fluent Card)
   ============================================= */
.overview {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin: 20px 0;
}}
.stat-card {{
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px;
  padding: 20px;
  text-align: center;
  transition: all 0.15s ease;
  box-shadow: var(--shadow2);
}}
.stat-card:hover {{
  box-shadow: var(--shadow4);
  border-color: var(--colorNeutralStroke1);
}}
.stat-card .label {{
  font-size: 12px; color: var(--colorNeutralForeground2);
  margin-bottom: 6px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.6px;
}}
.stat-card .value {{ font-size: 32px; font-weight: 700; }}
.stat-card .sub {{ font-size: 12px; color: var(--colorNeutralForeground2); margin-top: 4px; }}
.stat-card.gold .value {{ color: var(--colorGoldText); }}
.stat-card.purple .value {{ color: var(--colorPurpleText); }}
.stat-card.blue .value {{ color: var(--colorBrandForeground1); }}

/* =============================================
   Pool Tabs (Fluent Tabs style)
   ============================================= */
.pool-tabs {{
  display: flex; flex-wrap: wrap; gap: 4px;
  margin: 20px 0 12px;
}}
.pool-tab {{
  padding: 8px 16px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 4px 4px 0 0;
  cursor: pointer; font-size: 13px;
  color: var(--colorNeutralForeground2);
  transition: all 0.15s ease;
  white-space: nowrap;
  font-weight: 500;
}}
.pool-tab:hover {{
  color: var(--colorNeutralForeground1);
  background: var(--colorSubtleBackgroundHover);
}}
.pool-tab.active {{
  color: var(--colorBrandForeground1);
  border-bottom-color: var(--colorBrandBackground);
  font-weight: 600;
  background: transparent;
}}
.pool-tab .count {{
  display: inline-block;
  background: var(--colorNeutralBackground4);
  padding: 1px 7px; border-radius: 10px;
  font-size: 11px; margin-left: 4px;
  font-weight: 400;
  color: var(--colorNeutralForeground3);
}}

/* =============================================
   Fluent Card
   ============================================= */
.fcard {{
  background: var(--colorNeutralBackground1);
  border: 1px solid var(--colorNeutralStroke2);
  border-radius: 8px;
  padding: 20px;
  box-shadow: var(--shadow2);
  transition: background 0.3s ease, border-color 0.3s ease;
}}
.fcard h3 {{
  font-size: 13px; font-weight: 600;
  margin-bottom: 16px; padding-bottom: 8px;
  border-bottom: 1px solid var(--colorNeutralStroke3);
  color: var(--colorNeutralForeground2);
  text-transform: uppercase; letter-spacing: 0.6px;
}}

.pool-grid {{
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 12px; margin-bottom: 20px;
}}
@media (max-width: 768px) {{ .pool-grid {{ grid-template-columns: 1fr; }} }}

/* =============================================
   Pity Progress Bar
   ============================================= */
.pity-item {{ margin-bottom: 16px; position: relative; }}
.pity-label {{
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 4px; font-size: 13px;
  color: var(--colorNeutralForeground2);
}}
.pity-bar-track {{
  height: 8px; background: var(--colorNeutralBackground4);
  border-radius: 4px; position: relative; overflow: visible;
}}
.pity-soft-zone {{
  position: absolute; right: 0; top: -1px; bottom: -1px;
  opacity: 0.15; pointer-events: none; border-radius: 0 3px 3px 0;
}}
.pity-soft-zone.gold {{ background: var(--colorGold); }}
.pity-soft-zone.purple {{ background: var(--colorPurple); }}
.pity-fill {{
  height: 100%; border-radius: 4px;
  transition: width 0.8s cubic-bezier(0.4,0,0.2,1);
  position: relative; z-index: 1;
}}
.pity-fill.gold {{ background: var(--pityBarGold); }}
.pity-fill.purple {{ background: var(--pityBarPurple); }}
.pity-fill.red {{ background: var(--pityBarRed); }}
.pity-fill.gold.hot {{ background: var(--pityBarGold); animation: pulse 1.5s ease-in-out infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.7}} }}

.pity-milestone {{
  position: absolute; top: -3px; height: calc(100% + 6px);
  width: 1px; background: var(--colorNeutralStrokeAccessible);
  z-index: 2; opacity: 0.5;
}}
.pity-milestone-label {{
  position: absolute; top: -18px; font-size: 10px;
  color: var(--colorNeutralForeground3);
  transform: translateX(-50%); white-space: nowrap;
}}

.pity-status {{
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 600;
  margin-top: 6px;
}}
.pity-status.small {{ background: var(--colorGreenSubtle); color: var(--colorGreenText); }}
.pity-status.big {{ background: var(--colorRedSubtle); color: var(--colorRedText); }}
.pity-status.no-up {{ background: var(--colorPurpleSubtle); color: var(--colorBrandForeground1); }}
.pity-status::before {{ content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }}
.pity-prob-row {{ display: flex; gap: 16px; margin-top: 6px; font-size: 12px; color: var(--colorNeutralForeground3); }}
.pity-prob-item strong {{ color: var(--colorNeutralForeground1); margin-left: 2px; }}
.pity-prob-item.hot strong {{ color: var(--colorRedText); }}

/* =============================================
   Stats Grid
   ============================================= */
.stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; }}
.stat-item {{
  display: flex; justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--colorNeutralStroke3);
  font-size: 13px;
}}
.stat-item .label {{ color: var(--colorNeutralForeground2); }}
.stat-item .val {{ font-weight: 600; color: var(--colorNeutralForeground1); }}

/* =============================================
   Icon in Tables
   ============================================= */
.icon-cell {{ width: 36px; height: 36px; padding: 2px; }}
.icon-cell img {{
  width: 32px; height: 32px; border-radius: 4px;
  object-fit: cover; background: var(--colorNeutralBackground4);
}}
.star5-row .icon-cell img {{ box-shadow: 0 0 4px rgba(212,160,23,0.25); }}
.star4-row .icon-cell img {{ box-shadow: 0 0 3px rgba(135,100,184,0.2); }}
td.name-cell {{ white-space: nowrap; font-weight: 600; }}

/* =============================================
   Tables (Fluent DataGrid)
   ============================================= */
.history-section {{ margin-bottom: 20px; }}
.history-section h3 {{
  font-size: 13px; font-weight: 600;
  margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
  color: var(--colorNeutralForeground2);
  text-transform: uppercase; letter-spacing: 0.6px;
}}

table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{
  text-align: left; padding: 10px 12px;
  background: var(--colorNeutralBackground2);
  color: var(--colorNeutralForeground2); font-weight: 700;
  font-size: 12px; border-bottom: 2px solid var(--colorNeutralStroke1);
  letter-spacing: 0.3px;
}}
td {{ padding: 10px 12px; border-bottom: 1px solid var(--colorNeutralStroke3); }}
tr:hover td {{ background: var(--colorNeutralBackground1Hover); }}
tr:nth-child(even) td {{ background: var(--colorSubtleBackground); }}
tr:nth-child(even):hover td {{ background: var(--colorNeutralBackground1Hover); }}
.star5-row td {{ color: var(--colorGoldText); }}
.star4-row td.name-cell {{ color: var(--colorPurpleText); }}

/* Fluent Badge */
.tag {{
  display: inline-flex; align-items: center; padding: 2px 10px;
  border-radius: 4px; font-size: 11px; font-weight: 600;
  letter-spacing: 0.2px; line-height: 18px;
}}
.tag.up {{ background: var(--colorGreenSubtle); color: var(--colorGreenText); }}
.tag.lost {{ background: var(--colorRedSubtle); color: var(--colorRedText); }}
.tag.guaranteed {{ background: var(--colorOrangeSubtle); color: var(--colorOrangeText); }}
.tag.weapon-up {{ background: var(--colorCyanSubtle); color: var(--colorCyanText); }}
.tag.standard {{ background: var(--colorPurpleSubtle); color: var(--colorBrandForeground1); }}

/* =============================================
   Pity Distribution
   ============================================= */
.pity-dist {{ display: flex; align-items: flex-end; gap: 2px; height: 80px; padding: 8px 0; }}
.pity-bar-v {{
  flex: 1; min-width: 8px; background: var(--colorGold);
  border-radius: 3px 3px 0 0; transition: height 0.4s ease; position: relative;
}}
.pity-bar-v:hover {{ opacity: 0.8; }}
.pity-bar-v .tip {{
  display: none; position: absolute; bottom: 100%; left: 50%;
  transform: translateX(-50%); background: var(--colorNeutralBackground6);
  color: var(--colorNeutralForeground1);
  padding: 2px 6px; border-radius: 4px; font-size: 11px;
  white-space: nowrap; z-index: 10;
  border: 1px solid var(--colorNeutralStroke1);
}}
.pity-bar-v:hover .tip {{ display: block; }}
.pity-labels {{ display: flex; gap: 2px; font-size: 10px; color: var(--colorNeutralForeground3); }}
.pity-labels span {{ flex: 1; text-align: center; min-width: 8px; }}

/* =============================================
   Footer
   ============================================= */
.footer {{
  text-align: center; color: var(--colorNeutralForeground2);
  font-size: 12px; padding: 24px 0;
  border-top: 1px solid var(--colorNeutralStroke2); margin-top: 32px;
}}
</style>
</head>
<body>
<div class="header">
  <div class="container header-inner">
    <div>
      <h1>鸣潮抽卡分析</h1>
      <div class="meta">UID: {uid} | 数据截至: {date_str}</div>
    </div>
    <div class="theme-switch">
      <span class="theme-icon" id="theme-icon-light"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm0 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-1a3 3 0 1 1 0-6 3 3 0 0 1 0 6Zm7.5-2.5a.5.5 0 0 0 0-1h-1a.5.5 0 0 0 0 1h1ZM10 16c.28 0 .5.22.5.5v1a.5.5 0 0 1-1 0v-1c0-.28.22-.5.5-.5Zm-6.5-5.5a.5.5 0 0 0 0-1H2.46a.5.5 0 0 0 0 1H3.5Zm.65-6.35c.2-.2.5-.2.7 0l1 1a.5.5 0 1 1-.7.7l-1-1a.5.5 0 0 1 0-.7Zm.7 11.7a.5.5 0 0 1-.7-.7l1-1a.5.5 0 0 1 .7.7l-1 1Zm11-11.7a.5.5 0 0 0-.7 0l-1 1a.5.5 0 0 0 .7.7l1-1a.5.5 0 0 0 0-.7Zm-.7 11.7a.5.5 0 0 0 .7-.7l-1-1a.5.5 0 0 0-.7.7l1 1Z"/></svg></span>
      <div class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" role="switch" tabindex="0" aria-label="切换深浅主题"></div>
      <span class="theme-icon" id="theme-icon-dark"><svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M15.5 13.5A6.98 6.98 0 0 1 4 14.39c2.83-1.09 4.56-2.42 5.6-4.4 1.04-2 1.33-4.16.75-6.9A6.98 6.98 0 0 1 15.5 13.5ZM5.45 16.92A7.98 7.98 0 1 0 9.88 2.04a.6.6 0 0 0-.61.73c.69 2.82.43 4.88-.55 6.76-.94 1.78-2.55 3.03-5.55 4.1a.6.6 0 0 0-.3.9 7.95 7.95 0 0 0 2.59 2.39Z"/></svg></span>
    </div>
  </div>
</div>

<div class="container">
  <div id="overview" class="overview"></div>
  <div id="pool-tabs" class="pool-tabs"></div>
  <div id="pool-content"></div>
  <div class="footer">
    数据来源：游戏内唤取记录 | 保底规则：5星80抽硬保底（新手池50抽），4星10抽硬保底<br>
    角色活动池5星保底跨池共享 | 武器活动池5星保底跨池共享 | 联动池保底仅在相同联动主题内共享<br>
    注意：API仅能获取近6个月数据 | UP/歪判定为基于常驻角色列表估算，仅供参考
  </div>
</div>

<script>
// ============================================================
// Theme Management (follows Windows system preference)
// ============================================================
const storageKey = 'wuwa-theme';
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

function getSystemTheme() {{
  return mediaQuery.matches ? 'dark' : 'light';
}}

function applyTheme(theme) {{
  document.documentElement.setAttribute('data-theme', theme);
  const toggle = document.getElementById('theme-toggle');
  if (toggle) toggle.classList.toggle('active', theme === 'dark');
  // Show active icon
  const lightIcon = document.getElementById('theme-icon-light');
  const darkIcon = document.getElementById('theme-icon-dark');
  if (lightIcon) lightIcon.style.opacity = theme === 'light' ? '1' : '0.4';
  if (darkIcon) darkIcon.style.opacity = theme === 'dark' ? '1' : '0.4';
}}

function toggleTheme() {{
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem(storageKey, next);
  applyTheme(next);
}}

// Init theme: use saved preference, or default to system theme
(function initTheme() {{
  const saved = localStorage.getItem(storageKey);
  applyTheme(saved || getSystemTheme());
}});

// Follow system theme changes when no manual override
mediaQuery.addEventListener('change', e => {{
  if (!localStorage.getItem(storageKey)) applyTheme(e.matches ? 'dark' : 'light');
}});

// Keyboard support for toggle
document.addEventListener('keydown', e => {{
  if (e.target.id === 'theme-toggle' && (e.key === 'Enter' || e.key === ' ')) {{
    e.preventDefault(); toggleTheme();
  }}
}});

// ============================================================
// Data & Analysis
// ============================================================
const RAW_DATA = {json_str};
const ICON_MAP = {icon_map_str};

function getIconUrl(resourceId) {{
  if (!resourceId || !ICON_MAP[resourceId]) return '';
  return ICON_MAP[resourceId];
}}

const POOL_CONFIG = {{
  "1":  {{ name: "角色活动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character", hasUP4: true, up4Type: "character", crossPoolPity: "char-event" }},
  "2":  {{ name: "武器活动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-guaranteed", hasUP4: true, up4Type: "weapon", crossPoolPity: "weapon-event" }},
  "3":  {{ name: "角色常驻唤取", pity5: 80, pity4: 10, hasUP5: false, hasUP4: false }},
  "4":  {{ name: "武器常驻唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-selected", hasUP4: false }},
  "5":  {{ name: "新手唤取",     pity5: 50, pity4: 10, hasUP5: false, hasUP4: false }},
  "6":  {{ name: "新手自选唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-selected", hasUP4: false }},
  "7":  {{ name: "感恩定向唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-selected", hasUP4: false }},
  "8":  {{ name: "角色新旅唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character", hasUP4: true, up4Type: "character" }},
  "9":  {{ name: "武器新旅唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-selected", hasUP4: true, up4Type: "weapon" }},
  "10": {{ name: "角色联动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "character-collab", hasUP4: true, up4Type: "character-collab", crossPoolPity: "char-collab" }},
  "11": {{ name: "武器联动唤取", pity5: 80, pity4: 10, hasUP5: true, up5Type: "weapon-guaranteed-collab", hasUP4: true, up4Type: "weapon-collab", crossPoolPity: "weapon-collab" }},
}};

// 官方规则：
// - 角色活动唤取：5星50%UP，歪了下次必UP；4星50%UP角色，非UP下次必UP角色；跨池共享保底
// - 武器活动唤取：5星必出UP武器；4星50%UP武器，非UP下次必UP武器；跨池共享保底
// - 武器常驻唤取：5星必为自选武器；保底独立
// - 感恩定向唤取：5星必为自选角色（潮声答谢券1抽）；保底独立
// - 新手自选唤取：5星必为自选角色；保底独立
// - 角色新旅唤取：5星50%UP（自选角色），歪了下次必UP；4星50%UP角色，非UP下次必UP角色；保底独立不继承
// - 武器新旅唤取：5星必为自选武器；4星50%UP武器，非UP下次必UP武器；保底独立不继承
// - 角色联动唤取：5星50%UP，歪了下次必UP；4星50%UP角色，非UP下次必UP角色；同主题联动池共享保底
// - 武器联动唤取：5星必出UP武器；4星50%UP武器，非UP下次必UP武器；同主题联动池共享保底

const STANDARD_5STAR_CHARS = new Set(['维里奈','凌阳','卡卡罗','吟霖','鉴心','莫宁','珂莱塔']);

// Probability Model (NGA-verified)
function get5StarPullProb(pity, maxPity) {{
  if (pity >= maxPity) return 1;
  if (pity < 0) pity = 0;
  const BASE = 0.008;
  if (maxPity === 80) {{
    if (pity < 70) return BASE;
    return Math.min(1, BASE + (pity - 69) * 0.0902);
  }}
  if (maxPity === 50) {{
    if (pity < 40) return BASE;
    return Math.min(1, BASE + (pity - 39) * 0.0526);
  }}
  return BASE;
}}

function getCumulativeProb(pity, maxPity, ahead) {{
  let p = 1;
  for (let i = 0; i < ahead; i++) p *= (1 - get5StarPullProb(pity + i, maxPity));
  return 1 - p;
}}

function get4StarPullProb(pity, maxPity) {{
  if (pity >= maxPity) return 1;
  if (pity < 0) pity = 0;
  const BASE = 0.06;
  if (pity < 7) return BASE;
  return Math.min(1, BASE + (pity - 6) * 0.28);
}}

function normalizeData(raw) {{
  const data = {{ uid: raw.uid || '' }};
  for (const [key, val] of Object.entries(raw)) {{
    if (key === 'uid' || !Array.isArray(val)) continue;
    let poolId = key;
    if (!/^\\d+$/.test(key)) {{
      const m = {{ '角色活动唤取':'1','武器活动唤取':'2','角色常驻唤取':'3','武器常驻唤取':'4',
        '新手唤取':'5','新手自选唤取':'6','感恩定向唤取':'7',
        '角色精准调谐':'1','武器精准调谐':'2','角色调谐（常驻池）':'3','武器调谐（常驻池）':'4',
        '新手调谐':'5','自选调谐':'6','常驻调谐':'7' }};
      poolId = m[key] || key;
    }}
    const records = val.map(r => ({{ ...r, qualityLevel: Number(r.qualityLevel)||3, resourceId: Number(r.resourceId)||0, count: Number(r.count)||1 }}));
    if (!data[poolId]) data[poolId] = [];
    data[poolId] = data[poolId].concat(records);
  }}
  return data;
}}

function analyzePool(records, poolId) {{
  if (!records || !records.length) return null;
  const cfg = POOL_CONFIG[poolId] || {{ pity5:80, pity4:10, hasUP5:false, hasUP4:false }};
  // 数据本身已是倒序(最新在前)，直接反转为时间升序(最旧在前)
  // 不能用 sort，因为同时间戳的记录顺序代表抽卡先后，sort 会打乱顺序
  const sorted = [...records].reverse();
  let cur5=0, cur4=0, gs5='small', gs4='small';
  const s5=[], s4=[];

  for (let i=0; i<sorted.length; i++) {{
    const r=sorted[i]; cur5++; cur4++;

    // === 5星处理 ===
    if (r.qualityLevel===5) {{
      let tag='';

      if (cfg.hasUP5) {{
        if (cfg.up5Type === 'weapon-guaranteed' || cfg.up5Type === 'weapon-guaranteed-collab') {{
          // 武器活动/联动唤取：5星必出UP武器
          tag = 'up';
        }} else if (cfg.up5Type === 'weapon-selected') {{
          // 武器常驻唤取：5星必为自选武器
          tag = 'selected';
        }} else if (cfg.up5Type === 'character-selected') {{
          // 新手自选唤取：5星必为自选角色
          tag = 'selected';
        }} else if (cfg.up5Type === 'character' || cfg.up5Type === 'character-collab') {{
          // 角色活动/联动唤取：50%UP，歪了下次必UP
          if (gs5 === 'big') {{
            tag = 'guaranteed';
            gs5 = 'small';
          }} else if (r.resourceType === '武器') {{
            // 5星出的是武器而非角色 = 歪了
            tag = 'lost';
            gs5 = 'big';
          }} else if (STANDARD_5STAR_CHARS.has(r.name)) {{
            // 常驻5星角色 = 歪了
            tag = 'lost';
            gs5 = 'big';
          }} else {{
            // 非常驻5星角色 = UP
            tag = 'up';
            gs5 = 'small';
          }}
        }}
      }} else {{
        // 无UP池：常驻/新手/感恩等
        tag = 'standard';
      }}

      s5.push({{...r, pity:cur5, upTag:tag}}); cur5=0;
    }}

    // === 4星处理 ===
    if (r.qualityLevel===4) {{
      let tag4='';

      if (cfg.hasUP4) {{
        if (cfg.up4Type === 'character' || cfg.up4Type === 'character-collab') {{
          // 角色活动/联动池4星：50%UP角色，非UP下次必UP角色
          if (gs4 === 'big') {{
            tag4 = 'up4-guaranteed';
            gs4 = 'small';
          }} else if (r.resourceType === '角色') {{
            // 4星角色可能是UP也可能是非UP，无法从数据区分
            // 标记为"4星角色"，不作UP/歪判断
            tag4 = 'char4';
          }} else {{
            // 4星武器 = 非UP内容
            tag4 = 'lost4';
            gs4 = 'big';
          }}
        }} else if (cfg.up4Type === 'weapon' || cfg.up4Type === 'weapon-collab') {{
          // 武器活动/联动池4星：50%UP武器，非UP下次必UP武器
          if (gs4 === 'big') {{
            tag4 = 'up4-guaranteed';
            gs4 = 'small';
          }} else if (r.resourceType === '武器') {{
            tag4 = 'weapon4';
          }} else {{
            // 4星角色 = 非UP内容
            tag4 = 'lost4';
            gs4 = 'big';
          }}
        }}
      }} else {{
        tag4 = 'normal4';
      }}

      s4.push({{...r, pity:cur4, upTag4:tag4}}); cur4=0;
    }}
  }}

  const n5=s5.length, n4=s4.length, n3=sorted.filter(r=>r.qualityLevel===3).length;
  const avg5 = n5 ? s5.reduce((s,r)=>s+r.pity,0)/n5 : 0;
  const min5 = n5 ? Math.min(...s5.map(r=>r.pity)) : 0;
  const max5 = n5 ? Math.max(...s5.map(r=>r.pity)) : 0;
  const dist={{}};
  for (const s of s5) {{ const b=Math.ceil(s.pity/10)*10; dist[b]=(dist[b]||0)+1; }}

  // 4星统计
  const avg4 = n4 ? s4.reduce((s,r)=>s+r.pity,0)/n4 : 0;

  return {{ total:sorted.length, stars5:s5, stars4:s4, s5Count:n5, s4Count:n4, s3Count:n3,
    current5Pity:cur5, current4Pity:cur4, guaranteeState5:gs5, guaranteeState4:gs4,
    avgPity5:avg5, avgPity4:avg4, minPity5:min5, maxPity5:max5, pityDist:dist,
    pity5Max:cfg.pity5, pity4Max:cfg.pity4,
    hasUP5:cfg.hasUP5, up5Type:cfg.up5Type||'',
    hasUP4:cfg.hasUP4, up4Type:cfg.up4Type||'',
    crossPoolPity:cfg.crossPoolPity||'',
    poolName:cfg.name }};
}}

function renderOverview(all) {{
  let tp=0, t5=0, t4=0, tap=0, pc=0;
  for (const [,a] of Object.entries(all)) {{
    if (!a) continue; tp+=a.total; t5+=a.s5Count; t4+=a.s4Count;
    if (a.s5Count>0) {{ tap+=a.avgPity5*a.s5Count; pc+=a.s5Count; }}
  }}
  const avg=pc?(tap/pc).toFixed(1):'-';
  const label=pc?(avg<=40?'超级欧皇':avg<=50?'比较幸运':avg<=58?'正常水平':avg<=68?'有点非酋':'非酋本酋'):'暂无数据';
  const color=pc?(avg<=40?'var(--colorGreenText)':avg<=50?'var(--colorCyanText)':avg<=58?'var(--colorGoldText)':avg<=68?'var(--colorOrangeText)':'var(--colorRedText)'):'var(--colorNeutralForeground3)';
  document.getElementById('overview').innerHTML = `
    <div class="stat-card blue"><div class="label">总抽数</div><div class="value">${{tp.toLocaleString()}}</div><div class="sub">全部卡池</div></div>
    <div class="stat-card gold"><div class="label">5星总数</div><div class="value">${{t5}}</div><div class="sub">${{tp?(t5/tp*100).toFixed(2):0}}% 出率</div></div>
    <div class="stat-card purple"><div class="label">4星总数</div><div class="value">${{t4}}</div><div class="sub">${{tp?(t4/tp*100).toFixed(2):0}}% 出率</div></div>
    <div class="stat-card"><div class="label">欧非评价</div><div class="value" style="color:${{color}}">${{label}}</div><div class="sub">5星平均 ${{avg}} 抽出金</div></div>`;
}}

function renderPoolTabs(all) {{
  let html='', first=true;
  for (const pid of Object.keys(POOL_CONFIG)) {{
    const a=all[pid], cnt=a?a.total:0;
    if (!cnt && !['1','2','4','5','6','10','11'].includes(pid)) continue;
    html+=`<div class="pool-tab ${{first?'active':''}}" data-pool="${{pid}}" onclick="switchPool('${{pid}}')">${{POOL_CONFIG[pid].name}}<span class="count">${{cnt}}</span></div>`;
    first=false;
  }}
  document.getElementById('pool-tabs').innerHTML = html;
}}

function renderPoolContent(pid, a) {{
  const el = document.getElementById('pool-content');
  if (!a) {{ el.innerHTML='<div style="text-align:center;color:var(--colorNeutralForeground3);padding:40px">该卡池暂无抽卡记录</div>'; return; }}

  const p5=a.s5Count?(a.s5Count/a.total*100).toFixed(2):'0.00';
  const p4=a.s4Count?(a.s4Count/a.total*100).toFixed(2):'0.00';
  const pity5pct=Math.min(100,a.current5Pity/a.pity5Max*100);
  const pity4pct=Math.min(100,a.current4Pity/a.pity4Max*100);

  let guHtml='';
  if (a.hasUP5) {{
    if (a.up5Type === 'weapon-guaranteed') guHtml='<div class="pity-status no-up">武器活动池 — 5星必出UP武器</div>';
    else if (a.up5Type === 'weapon-guaranteed-collab') guHtml='<div class="pity-status no-up">武器联动池 — 5星必出UP武器</div>';
    else if (a.up5Type === 'weapon-selected') guHtml='<div class="pity-status no-up">5星必为自选武器</div>';
    else if (a.up5Type === 'character-selected') guHtml='<div class="pity-status no-up">5星必为自选角色</div>';
    else if (a.up5Type === 'character') guHtml=a.guaranteeState5==='big'?'<div class="pity-status big">大保底 — 下次5星必出UP角色</div>':'<div class="pity-status small">小保底 — 50%概率出UP角色</div>';
    else if (a.up5Type === 'character-collab') guHtml=a.guaranteeState5==='big'?'<div class="pity-status big">联动大保底 — 下次5星必出UP角色</div>':'<div class="pity-status small">联动小保底 — 50%概率出UP角色</div>';
  }} else {{
    guHtml='<div class="pity-status no-up">常驻池 — 无UP机制</div>';
  }}

  // 4星保底状态
  let gu4Html='';
  if (a.hasUP4) {{
    if (a.up4Type === 'character') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP角色</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP角色</div>';
    else if (a.up4Type === 'weapon') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP武器</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP武器</div>';
    else if (a.up4Type === 'character-collab') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP角色(联动)</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP角色(联动)</div>';
    else if (a.up4Type === 'weapon-collab') gu4Html=a.guaranteeState4==='big'?'<div class="pity-status big" style="margin-top:4px">4星大保底 — 下次4星必出UP武器(联动)</div>':'<div class="pity-status small" style="margin-top:4px">4星小保底 — 50%概率出UP武器(联动)</div>';
  }}

  // 跨池保底继承说明
  let crossPoolNote='';
  if (a.crossPoolPity) {{
    if (a.crossPoolPity === 'char-event') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数在所有「角色活动唤取」池间共享继承</div>';
    else if (a.crossPoolPity === 'weapon-event') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数在所有「武器活动唤取」池间共享继承</div>';
    else if (a.crossPoolPity === 'char-collab') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数仅在相同联动主题的「角色联动唤取」池间共享</div>';
    else if (a.crossPoolPity === 'weapon-collab') crossPoolNote='<div style="font-size:11px;color:var(--colorNeutralForeground3);margin-top:4px">*5星保底计数仅在相同联动主题的「武器联动唤取」池间共享</div>';
  }}

  let distH='', lblH='';
  const mx=Math.max(...Object.values(a.pityDist),1);
  for (let b=10;b<=a.pity5Max;b+=10) {{ const c=a.pityDist[b]||0; distH+=`<div class="pity-bar-v" style="height:${{c/mx*70}}px"><div class="tip">${{b-9}}-${{b}}抽:${{c}}次</div></div>`; }}
  for (let b=10;b<=a.pity5Max;b+=10) lblH+=`<span>${{b%20===0?b:''}}</span>`;

  // Probability calculations
  const nextP5 = get5StarPullProb(a.current5Pity, a.pity5Max);
  const nextP4 = get4StarPullProb(a.current4Pity, a.pity4Max);
  const cum10 = getCumulativeProb(a.current5Pity, a.pity5Max, 10);
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
  for (let i=0;i<r5.length;i++) {{
    const s=r5[i]; let tag='';
    if(s.upTag==='up')tag='<span class="tag up">UP</span>';
    else if(s.upTag==='lost')tag='<span class="tag lost">歪了</span>';
    else if(s.upTag==='guaranteed')tag='<span class="tag guaranteed">大保底出</span>';
    else if(s.upTag==='selected')tag='<span class="tag weapon-up">自选</span>';
    else if(s.upTag==='standard')tag='<span class="tag standard">常驻</span>';
    const ic=getIconUrl(s.resourceId);
    s5rows+=`<tr class="star5-row"><td>${{r5.length-i}}</td>${{ic?`<td class="icon-cell"><img src="${{ic}}" loading="lazy" alt="${{s.name}}" onerror="this.style.display='none'"></td>`:''}}<td class="name-cell">${{s.name}}</td><td>${{s.resourceType}}</td><td><strong>${{s.pity}}</strong> 抽</td><td>${{s.time}}</td><td>${{tag}}</td></tr>`;
  }}
  for (let i=0;i<Math.min(r4.length,50);i++) {{
    const s=r4[i], ic=getIconUrl(s.resourceId);
    let tag4='';
    if(s.upTag4==='char4')tag4='<span class="tag weapon-up">4星角色</span>';
    else if(s.upTag4==='weapon4')tag4='<span class="tag weapon-up">4星武器</span>';
    s4rows+=`<tr class="star4-row"><td>${{r4.length-i}}</td>${{ic?`<td class="icon-cell"><img src="${{ic}}" loading="lazy" alt="${{s.name}}" onerror="this.style.display='none'"></td>`:''}}<td class="name-cell">${{s.name}}</td><td>${{s.resourceType}}</td><td>${{s.pity}} 抽</td><td>${{s.time}}</td><td>${{tag4}}</td></tr>`;
  }}

  el.innerHTML = `
    <div class="pool-grid">
      <div class="fcard">
        <h3>保底进度</h3>
        <div class="pity-item">
          <div class="pity-label">
            <span>5星保底</span>
            <span style="color:var(--colorGoldText);font-weight:700">${{a.current5Pity}} / ${{a.pity5Max}}</span>
          </div>
          <div class="pity-bar-track">
            <div class="pity-soft-zone gold" style="width:${{softZone5pct}}%"></div>
            <div class="pity-milestone" style="left:${{(softStart5/a.pity5Max*100).toFixed(1)}}%"><span class="pity-milestone-label">概率提升</span></div>
            <div class="pity-fill ${{isSoft5?'gold hot':pity5pct>50?'red':'gold'}}" style="width:${{pity5pct}}%"></div>
          </div>
          <div class="pity-prob-row">
            <span class="pity-prob-item ${{isSoft5?'hot':''}}">下抽出金 <strong>${{nextP5pct}}%</strong></span>
            <span class="pity-prob-item">10抽内出金 <strong>${{cum10pct}}%</strong></span>
          </div>
          ${{guHtml}}
          ${{crossPoolNote}}
        </div>
        <div class="pity-item" style="margin-top:20px">
          <div class="pity-label"><span>4星保底</span><span style="color:var(--colorPurpleText);font-weight:700">${{a.current4Pity}} / ${{a.pity4Max}}</span></div>
          <div class="pity-bar-track">
            <div class="pity-soft-zone purple" style="width:${{softZone4pct}}%"></div>
            <div class="pity-fill purple" style="width:${{pity4pct}}%"></div>
          </div>
          <div class="pity-prob-row">
            <span class="pity-prob-item ${{a.current4Pity>=softStart4?'hot':''}}">下抽出4星 <strong>${{nextP4pct}}%</strong></span>
          </div>
          ${{gu4Html}}
        </div>
      </div>
      <div class="fcard">
        <h3>统计数据</h3>
        <div class="stats-grid">
          <div class="stat-item"><span class="label">总抽数</span><span class="val">${{a.total}}</span></div>
          <div class="stat-item"><span class="label">5星数量</span><span class="val" style="color:var(--colorGoldText)">${{a.s5Count}}</span></div>
          <div class="stat-item"><span class="label">4星数量</span><span class="val" style="color:var(--colorPurpleText)">${{a.s4Count}}</span></div>
          <div class="stat-item"><span class="label">3星数量</span><span class="val">${{a.s3Count}}</span></div>
          <div class="stat-item"><span class="label">5星出率</span><span class="val">${{p5}}%</span></div>
          <div class="stat-item"><span class="label">4星出率</span><span class="val">${{p4}}%</span></div>
          <div class="stat-item"><span class="label">5星平均抽数</span><span class="val">${{a.avgPity5?a.avgPity5.toFixed(1):'-'}}</span></div>
          <div class="stat-item"><span class="label">4星平均抽数</span><span class="val">${{a.avgPity4?a.avgPity4.toFixed(1):'-'}}</span></div>
          <div class="stat-item"><span class="label">最欧出金</span><span class="val" style="color:var(--colorGreenText)">${{a.minPity5?a.minPity5+'抽':'-'}}</span></div>
          <div class="stat-item"><span class="label">最非出金</span><span class="val" style="color:var(--colorRedText)">${{a.maxPity5?a.maxPity5+'抽':'-'}}</span></div>
          <div class="stat-item"><span class="label">距5星保底</span><span class="val" style="color:${{a.pity5Max-a.current5Pity<=10?'var(--colorRedText)':'var(--colorNeutralForeground1)'}}">${{a.pity5Max-a.current5Pity}}抽</span></div>
        </div>
      </div>
    </div>
    ${{a.s5Count?`<div class="fcard" style="margin-bottom:12px"><h3>5星保底分布</h3><div class="pity-dist">${{distH}}</div><div class="pity-labels">${{lblH}}</div></div>`:''}}
    ${{a.s5Count?`<div class="history-section"><h3><svg class="fluent-icon" style="color:var(--colorGoldText)" width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M9.1 2.9a1 1 0 0 1 1.8 0l1.93 3.91 4.31.63a1 1 0 0 1 .56 1.7l-3.12 3.05.73 4.3a1 1 0 0 1-1.45 1.05L10 15.51l-3.86 2.03a1 1 0 0 1-1.45-1.05l.74-4.3L2.3 9.14a1 1 0 0 1 .56-1.7l4.31-.63L9.1 2.9Z"/></svg> 5星获取记录（共${{a.s5Count}}个）</h3><table><thead><tr><th>#</th><th></th><th>名称</th><th>类型</th><th>保底抽数</th><th>时间</th><th>标记</th></tr></thead><tbody>${{s5rows}}</tbody></table></div>`:''}}
    ${{a.s4Count?`<div class="history-section"><h3><svg class="fluent-icon" style="color:var(--colorPurpleText)" width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M9.1 2.9a1 1 0 0 1 1.8 0l1.93 3.91 4.31.63a1 1 0 0 1 .56 1.7l-3.12 3.05.73 4.3a1 1 0 0 1-1.45 1.05L10 15.51l-3.86 2.03a1 1 0 0 1-1.45-1.05l.74-4.3L2.3 9.14a1 1 0 0 1 .56-1.7l4.31-.63L9.1 2.9Z"/></svg> 4星获取记录（共${{a.s4Count}}个）</h3><table><thead><tr><th>#</th><th></th><th>名称</th><th>类型</th><th>保底抽数</th><th>时间</th><th>标记</th></tr></thead><tbody>${{s4rows}}</tbody></table></div>`:''}}
  `;
}}

let allAnalysis={{}}, currentPool=null;
function switchPool(pid) {{
  document.querySelectorAll('.pool-tab').forEach(t=>t.classList.toggle('active',t.dataset.pool===pid));
  currentPool=pid; renderPoolContent(pid,allAnalysis[pid]);
}}

(function init() {{
  const data=normalizeData(RAW_DATA);
  for (const pid of Object.keys(POOL_CONFIG)) {{
    const r=data[pid]||[];
    allAnalysis[pid]=r.length?analyzePool(r,pid):null;
  }}
  renderOverview(allAnalysis);
  renderPoolTabs(allAnalysis);
  const first=document.querySelector('.pool-tab');
  if (first) switchPool(first.dataset.pool);
}})();
</script>
</body>
</html>
"""

output_path = os.path.join(DATA_DIR, "gacha_report.html")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"报告已生成: {output_path}")
print(f"数据文件: {os.path.basename(JSON_FILE)}")
print(f"图标缓存: {len(icon_map)} 个")
