#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸣潮抽卡记录获取工具
====================
从游戏API抓取最新的抽卡记录，保存为JSON文件。

使用方式:
  1. 直接运行脚本，按提示粘贴JSON凭证
  2. 命令行参数: python wuwa_gacha.py '{"recordId":"xxx","playerId":"xxx",...}'
  3. 命令行指定文件: python wuwa_gacha.py -f input.json
"""

import json
import sys
import os
import time
import datetime
import requests
from urllib.parse import urlencode

# ============================================================
# 常量定义
# ============================================================

API_BASE_CN = "https://gmserver-api.aki-game2.com/gacha"
API_BASE_GLOBAL = "https://gmserver-api.aki-game2.net/gacha"
WEB_BASE = "https://aki-gm-resources.aki-game.com/aki/gacha/index.html#/record"

POOL_TYPE_NAMES = {
    1: "角色活动唤取", 2: "武器活动唤取",
    3: "角色常驻唤取", 4: "武器常驻唤取",
    5: "新手唤取", 6: "新手自选唤取",
    7: "感恩定向唤取", 8: "角色新旅唤取",
    9: "武器新旅唤取", 10: "角色联动唤取",
    11: "武器联动唤取",
}

ALL_POOL_TYPES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
REQUEST_INTERVAL = 0.5
REQUEST_TIMEOUT = 15

# ============================================================
# 核心功能函数
# ============================================================


def parse_input(text: str) -> dict:
    """解析用户输入的JSON凭证文本"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    raise ValueError("无法解析输入的JSON文本，请检查格式")


def build_web_url(creds: dict) -> str:
    """将凭证JSON转为鸣潮唤取记录网页URL"""
    params = {
        "svr_id": creds["serverId"],
        "player_id": creds["playerId"],
        "lang": creds.get("languageCode", "zh-Hans"),
        "gacha_id": creds.get("cardPoolId", ""),
        "gacha_type": creds.get("cardPoolType", 1),
        "svr_area": "cn",
        "record_id": creds["recordId"],
        "resources_id": creds["cardPoolId"],
    }
    return f"{WEB_BASE}?{urlencode(params)}"


def fetch_gacha_records(creds: dict, card_pool_type: int) -> list:
    """调用API获取指定卡池类型的抽卡记录"""
    payload = {
        "recordId": creds["recordId"],
        "playerId": creds["playerId"],
        "serverId": creds["serverId"],
        "cardPoolId": creds["cardPoolId"],
        "cardPoolType": card_pool_type,
        "languageCode": creds.get("languageCode", "zh-Hans"),
    }

    svr_area = creds.get("svr_area", "cn")
    api_base = API_BASE_CN if svr_area == "cn" else API_BASE_GLOBAL
    url = f"{api_base}/record/query"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()

        if isinstance(result, dict):
            code = result.get("code", -1)
            if code == 0 or code == 200 or result.get("message") == "成功":
                data = result.get("data", [])
                return data if isinstance(data, list) else []
            else:
                msg = result.get("message", "未知错误")
                print(f"  [!] 卡池{card_pool_type} API返回错误: code={code}, message={msg}")
                return []
        return []
    except requests.exceptions.Timeout:
        print(f"  [!] 卡池{card_pool_type} 请求超时")
        return []
    except requests.exceptions.RequestException as e:
        print(f"  [!] 卡池{card_pool_type} 请求失败: {e}")
        return []
    except json.JSONDecodeError:
        print(f"  [!] 卡池{card_pool_type} 响应解析失败")
        return []


def transform_record(raw: dict, card_pool_type: int) -> dict:
    """将API返回的单条记录转换为目标JSON格式，保留原始字段值"""
    raw_cpt = raw.get("cardPoolType", str(card_pool_type))
    return {
        "cardPoolType": raw_cpt,
        "resourceId": raw.get("resourceId", 0),
        "qualityLevel": raw.get("qualityLevel", 0),
        "resourceType": raw.get("resourceType", ""),
        "name": raw.get("name", ""),
        "count": raw.get("count", 1),
        "time": raw.get("time", ""),
    }


def fetch_all_pools(creds: dict) -> dict:
    """遍历所有卡池类型，获取全部抽卡记录"""
    player_id = str(creds["playerId"])
    result = {"uid": player_id}
    total = 0

    max_pool = ALL_POOL_TYPES[-1]
    for pool_type in ALL_POOL_TYPES:
        pool_name = POOL_TYPE_NAMES.get(pool_type, str(pool_type))
        print(f"  [{pool_type}/{max_pool}] 正在获取 {pool_name} ...")

        records = fetch_gacha_records(creds, pool_type)
        transformed = [transform_record(r, pool_type) for r in records]
        result[str(pool_type)] = transformed
        total += len(transformed)

        print(f"       获取到 {len(transformed)} 条记录")

        if pool_type < ALL_POOL_TYPES[-1]:
            time.sleep(REQUEST_INTERVAL)

    print(f"\n  共获取 {total} 条抽卡记录")
    return result


def save_result(data: dict, player_id: str, output_dir: str = None) -> str:
    """保存结果为JSON文件，文件名带时间戳"""
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))

    now = datetime.datetime.now()
    filename = f"uid_{player_id}_{now.strftime('%Y-%m-%d_%H%M%S')}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return filepath


# ============================================================
# 主交互逻辑
# ============================================================


def interactive_input() -> dict:
    """交互式获取用户输入"""
    print("=" * 56)
    print("  鸣潮抽卡记录获取工具")
    print("=" * 56)
    print()
    print("请粘贴游戏内唤取记录的JSON凭证，格式如:")
    print('{ "recordId" : "xxx", "playerId" : "xxx",')
    print('  "serverId" : "xxx", "cardPoolId" : "xxx",')
    print('  "cardPoolType" : 1, "languageCode" : "zh-Hans" }')
    print()
    print("输入完成后按回车确认 (可多行粘贴):")
    print("-" * 56)

    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
            if line.strip() == "":
                empty_count += 1
                if empty_count >= 2 and lines:
                    break
            else:
                empty_count = 0
                lines.append(line)
        except EOFError:
            break

    text = "\n".join(lines)
    return parse_input(text)


def main():
    creds = None

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "-f" and len(sys.argv) > 2:
            with open(sys.argv[2], "r", encoding="utf-8") as f:
                creds = parse_input(f.read())
        elif arg in ("-h", "--help"):
            print(__doc__)
            return
        else:
            creds = parse_input(arg)
    else:
        creds = interactive_input()

    if not creds:
        print("未获取到有效凭证")
        return

    required = ["recordId", "playerId", "serverId", "cardPoolId"]
    missing = [f for f in required if f not in creds]
    if missing:
        print(f"缺少必要字段: {', '.join(missing)}")
        return

    print(f"\n凭证解析成功!")
    print(f"  玩家ID: {creds['playerId']}")
    print(f"  服务器ID: {creds['serverId']}")
    print(f"  记录ID: {creds['recordId']}")

    web_url = build_web_url(creds)
    print(f"\n生成的唤取记录URL:")
    print(f"  {web_url}")

    print(f"\n开始获取所有卡池的抽卡记录...")
    gacha_data = fetch_all_pools(creds)

    filepath = save_result(gacha_data, str(creds["playerId"]))
    print(f"\n结果已保存至: {filepath}")

    print(f"\n{'='*56}")
    print("  抽卡统计摘要")
    print(f"{'='*56}")
    for pool_type in ALL_POOL_TYPES:
        records = gacha_data.get(str(pool_type), [])
        if not records:
            continue
        pool_name = POOL_TYPE_NAMES.get(pool_type, str(pool_type))
        star5 = sum(1 for r in records if r.get("qualityLevel") == 5)
        star4 = sum(1 for r in records if r.get("qualityLevel") == 4)
        star3 = sum(1 for r in records if r.get("qualityLevel") == 3)
        print(f"  {pool_name}: 共{len(records)}抽", end="")
        if star5:
            print(f" | 5星:{star5}", end="")
        if star4:
            print(f" | 4星:{star4}", end="")
        if star3:
            print(f" | 3星:{star3}", end="")
        print()
    print(f"{'='*56}")


if __name__ == "__main__":
    main()
