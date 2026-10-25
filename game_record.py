# -*- coding: utf-8 -*-
"""米游社战绩接口、数据整理与真人验证码协议。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx


CN_RECORD_HOST = "https://api-takumi-record.mihoyo.com"
CN_API_HOST = "https://api-takumi.mihoyo.com"
DEVICE_FP_URL = "https://public-data-api.mihoyo.com/device-fp/api/getFp"
CN_RECORD_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"

RECORD_GAMES = {
    "hk4e_cn": "gs",
    "hkrpg_cn": "sr",
    "nap_cn": "zzz",
}

GAME_NAMES = {
    "gs": "原神",
    "sr": "崩坏：星穹铁道",
    "zzz": "绝区零",
}

ENDPOINTS = {
    "gs": {
        "profile": ("GET", f"{CN_RECORD_HOST}/game_record/app/genshin/api/index"),
        "characters": ("POST", f"{CN_RECORD_HOST}/game_record/app/genshin/api/character/list"),
        "character_detail": ("POST", f"{CN_RECORD_HOST}/game_record/app/genshin/api/character/detail"),
        "abyss": ("GET", f"{CN_RECORD_HOST}/game_record/app/genshin/api/spiralAbyss"),
        "daily_note": ("GET", f"{CN_RECORD_HOST}/game_record/app/genshin/api/dailyNote"),
    },
    "sr": {
        "profile": ("GET", f"{CN_RECORD_HOST}/game_record/app/hkrpg/api/index"),
        "characters": ("GET", f"{CN_RECORD_HOST}/game_record/app/hkrpg/api/avatar/basic"),
        "character_detail": ("GET", f"{CN_RECORD_HOST}/game_record/app/hkrpg/api/avatar/info"),
        "abyss": ("GET", f"{CN_RECORD_HOST}/game_record/app/hkrpg/api/challenge"),
        "daily_note": ("GET", f"{CN_RECORD_HOST}/game_record/app/hkrpg/api/note"),
    },
    "zzz": {
        "profile": ("GET", f"{CN_RECORD_HOST}/event/game_record_zzz/api/zzz/index"),
        "characters": ("GET", f"{CN_RECORD_HOST}/event/game_record_zzz/api/zzz/avatar/basic"),
        "daily_note": ("GET", f"{CN_RECORD_HOST}/event/game_record_zzz/api/zzz/note"),
        "buddy": ("GET", f"{CN_RECORD_HOST}/event/game_record_zzz/api/zzz/buddy/info"),
    },
}

CAPTCHA_RETCODES = {1034, 10035, 10041}
RETRYABLE_RETCODES = {-1000}
LOGIN_INVALID_RETCODES = {-1, -100, 1001, 10001, 10103}


class GameRecordError(RuntimeError):
    def __init__(self, message: str, retcode: int | None = None):
        super().__init__(message)
        self.retcode = retcode


@dataclass
class RecordRequest:
    game: str
    action: str
    uid: str
    server: str
    query: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)


@dataclass
class CaptchaChallenge:
    request: RecordRequest
    retcode: int
    gt: str
    challenge: str
    new_captcha: bool
    app_key: str
    verify_headers: dict


def _random_text(length: int) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def _compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_query(values: dict) -> str:
    pairs = [(str(key), str(value)) for key, value in values.items() if value is not None]
    return urlencode(pairs)


def _make_ds(query: str, body: str, salt: str = CN_RECORD_SALT) -> str:
    timestamp = str(int(time.time()))
    nonce = str(random.randint(100001, 200000))
    raw = f"salt={salt}&t={timestamp}&r={nonce}&b={body}&q={query}"
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"{timestamp},{nonce},{digest}"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ts_text(timestamp) -> str:
    value = _safe_int(timestamp)
    if value <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def _as_list(data, *keys) -> list:
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def record_game_from_biz(game_biz: str) -> str | None:
    return RECORD_GAMES.get(game_biz)


def get_character_list(game: str, data: dict) -> list:
    keys = {
        "gs": ("list", "avatars"),
        "sr": ("avatar_list", "list", "avatars"),
        "zzz": ("avatar_list", "list", "avatars"),
    }[game]
    return _as_list(data, *keys)


def get_detail_list(game: str, data: dict) -> list:
    keys = {
        "gs": ("list", "avatars"),
        "sr": ("avatar_list", "list", "avatars"),
        "zzz": ("avatar_list", "list", "avatars"),
    }[game]
    return _as_list(data, *keys)


def find_characters(characters: list, selector: str) -> list:
    selector = (selector or "").strip()
    if not selector:
        return characters
    exact = [
        item for item in characters
        if selector in {
            str(item.get("name") or "").strip(),
            str(item.get("id") or "").strip(),
        }
    ]
    if exact:
        return exact
    lowered = selector.lower()
    return [
        item for item in characters
        if lowered in str(item.get("name") or "").lower()
        or lowered in str(item.get("id") or "").lower()
    ]


def _find_detail_item(detail: list, character_id) -> dict:
    character_id = str(character_id or "")
    for item in detail:
        base = item.get("base") if isinstance(item.get("base"), dict) else item
        if str(base.get("id") or item.get("id") or "") == character_id:
            return item
    return {}


def _value_text(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def _property_value_text(name: str, value) -> str:
    suffix = ""
    if any(
        token in name
        for token in ("率", "伤害", "加成", "恢复", "精通", "充能", "命中", "抵抗", "击破")
    ):
        try:
            suffix = "%" if float(value) <= 1000 else ""
        except (TypeError, ValueError):
            suffix = ""
    return f"{_value_text(value)}{suffix}"


GS_PROPERTY_NAMES = {
    2000: "生命上限",
    2001: "攻击力",
    2002: "防御力",
    20: "暴击率",
    22: "暴击伤害",
    23: "元素充能",
    26: "治疗加成",
    28: "元素精通",
    30: "物理伤害",
    40: "火伤",
    41: "雷伤",
    42: "水伤",
    43: "草伤",
    44: "风伤",
    45: "岩伤",
    46: "冰伤",
    2: "生命值",
    3: "生命值",
    5: "攻击力",
    6: "攻击力",
    8: "防御力",
    9: "防御力",
}

SR_PROPERTY_NAMES = {
    1: "生命值",
    2: "攻击力",
    3: "防御力",
    4: "速度",
    5: "暴击率",
    6: "暴击伤害",
    7: "治疗加成",
    9: "能量恢复",
    10: "效果命中",
    11: "效果抵抗",
    12: "物理伤害",
    14: "火伤",
    16: "冰伤",
    18: "雷伤",
    20: "风伤",
    22: "量子伤害",
    24: "虚数伤害",
    27: "生命值",
    29: "攻击力",
    31: "防御力",
    32: "生命值%",
    33: "攻击力%",
    34: "防御力%",
    51: "速度",
    52: "暴击率",
    53: "暴击伤害",
    54: "能量恢复",
    55: "治疗加成",
    56: "效果命中",
    57: "效果抵抗",
    58: "击破特攻",
    59: "击破特攻",
}

GS_ARTIFACT_POS = {
    1: "生之花",
    2: "死之羽",
    3: "时之沙",
    4: "空之杯",
    5: "理之冠",
}


def _property_lines(properties: list, game: str, limit: int = 10) -> list[str]:
    names = GS_PROPERTY_NAMES if game == "gs" else SR_PROPERTY_NAMES
    lines = []
    for prop in properties or []:
        if not isinstance(prop, dict):
            continue
        name = names.get(_safe_int(prop.get("property_type")))
        if not name:
            continue
        value = prop.get("final", prop.get("value", prop.get("base")))
        if value is None:
            continue
        lines.append(f"{name} {_property_value_text(name, value)}")
        if len(lines) >= limit:
            break
    return lines


def _artifact_main_text(item: dict, game: str) -> str:
    prop = item.get("main_property")
    if not isinstance(prop, dict):
        return ""
    names = GS_PROPERTY_NAMES if game == "gs" else SR_PROPERTY_NAMES
    name = names.get(_safe_int(prop.get("property_type")), "主词条")
    value = prop.get("value", prop.get("final"))
    return f"{name} {_property_value_text(name, value)}" if value is not None else name


def _artifact_sub_text(item: dict, game: str) -> str:
    names = GS_PROPERTY_NAMES if game == "gs" else SR_PROPERTY_NAMES
    parts = []
    for prop in item.get("sub_property_list") or []:
        if not isinstance(prop, dict):
            continue
        name = names.get(_safe_int(prop.get("property_type")))
        value = prop.get("value")
        if name and value is not None:
            parts.append(f"{name} {_property_value_text(name, value)}")
    return " / ".join(parts)


def format_profile(game: str, data: dict) -> str:
    role = data.get("role") if isinstance(data.get("role"), dict) else {}
    nickname = role.get("nickname") or "未知角色"
    level = role.get("level") or "?"
    region = role.get("region_name") or role.get("region") or "未知区服"
    lines = [f"【{GAME_NAMES[game]}资料】{nickname} · Lv.{level}", f"区服：{region}"]

    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    stat_names = {
        "active_day_number": "活跃天数",
        "active_days": "活跃天数",
        "achievement_number": "成就数",
        "achievement_num": "成就数",
        "avatar_number": "角色数",
        "avatar_num": "角色数",
        "way_point_number": "传送点",
        "domain_number": "秘境",
        "luxurious_chest_number": "华丽宝箱",
        "precious_chest_number": "珍贵宝箱",
        "exquisite_chest_number": "精致宝箱",
        "common_chest_number": "普通宝箱",
        "anemoculus_number": "风神瞳",
        "geoculus_number": "岩神瞳",
        "electroculus_number": "雷神瞳",
        "dendroculus_number": "草神瞳",
        "hydroculus_number": "水神瞳",
        "pyroculus_number": "火神瞳",
        "cryoculus_number": "冰神瞳",
        "homes": "洞天",
        "world_level": "世界等级",
        "memory_chaos": "混沌回忆",
        "max_rogue_score": "模拟宇宙",
    }
    stat_lines = []
    for key, value in stats.items():
        label = stat_names.get(key)
        if label and value not in (None, "", [], {}):
            stat_lines.append(f"{label}：{value}")
    if stat_lines:
        lines.append("统计：" + " · ".join(stat_lines[:10]))

    explorations = _as_list(data, "world_explorations")
    if explorations:
        areas = []
        for item in explorations[:12]:
            area = item.get("name")
            percent = item.get("exploration_percentage")
            if area and percent is not None:
                areas.append(f"{area} {percent}%")
        if areas:
            lines.append("探索：" + " · ".join(areas))

    avatars = _as_list(data, "avatars", "avatar_list")
    if avatars:
        lines.append(f"角色数量：{len(avatars)}")
    lines.append("可继续查询：角色 / 武器 / 圣遗物 / 装备 / 深渊 / 体力")
    return "\n".join(lines)


def format_character_list(game: str, characters: list) -> str:
    if not characters:
        return f"没有查到{GAME_NAMES[game]}角色数据"
    sorted_items = sorted(
        characters,
        key=lambda item: (_safe_int(item.get("level")), _safe_int(item.get("rarity") or item.get("star"))),
        reverse=True,
    )
    names = []
    for item in sorted_items:
        name = item.get("name") or item.get("full_name") or item.get("id") or "?"
        level = item.get("level")
        names.append(f"{name}(Lv.{level})" if level else str(name))
    return (
        f"【{GAME_NAMES[game]}角色】共 {len(names)} 个\n"
        + "、".join(names)
        + "\n查询详情：米游社 角色 <角色名>"
    )


def _equipment_sections(game: str, base: dict, detail_item: dict, mode: str) -> list[str]:
    lines = []
    base_data = detail_item.get("base") if isinstance(detail_item.get("base"), dict) else detail_item
    weapon = detail_item.get("weapon") or detail_item.get("equip") or base.get("weapon") or {}
    if mode in ("weapon", "equipment") and isinstance(weapon, dict) and weapon:
        name = weapon.get("name") or base.get("weapon", {}).get("name") or "未知武器"
        rarity = weapon.get("rarity") or weapon.get("star") or "?"
        level = weapon.get("level") or "?"
        affix = weapon.get("affix_level", weapon.get("affix", weapon.get("rank")))
        weapon_line = f"武器：{name} · {rarity}星 · Lv.{level}"
        if affix is not None:
            weapon_line += f" · 精炼/叠影 {affix}"
        lines.append(weapon_line)

    if mode in ("artifacts", "equipment"):
        relics = detail_item.get("relics")
        if not isinstance(relics, list):
            relics = base.get("reliquaries") or []
        relics = relics + (detail_item.get("ornaments") or []) if isinstance(relics, list) else []
        base_relics = base.get("reliquaries") or []
        base_by_id = {
            str(item.get("id")): item
            for item in base_relics
            if isinstance(item, dict) and item.get("id") is not None
        }
        base_by_pos = {
            _safe_int(item.get("pos")): item
            for item in base_relics
            if isinstance(item, dict) and item.get("pos") is not None
        }
        artifact_lines = []
        for item in relics:
            if not isinstance(item, dict):
                continue
            pos = _safe_int(item.get("pos"))
            pos_name = item.get("pos_name") or GS_ARTIFACT_POS.get(pos, "")
            base_item = base_by_id.get(str(item.get("id"))) or base_by_pos.get(pos) or {}
            name = (
                item.get("name")
                or base_item.get("name")
                or (f"圣遗物/遗器 #{item.get('id')}" if item.get("id") else "未知圣遗物/遗器")
            )
            rarity = item.get("rarity") or "?"
            level = item.get("level", "?")
            row = f"{pos_name + ' · ' if pos_name else ''}{name} · {rarity}星 · +{level}"
            main = _artifact_main_text(item, game)
            sub = _artifact_sub_text(item, game)
            if main:
                row += f"\n  主词条：{main}"
            if sub:
                row += f"\n  副词条：{sub}"
            artifact_lines.append(row)
        if artifact_lines:
            lines.append("圣遗物/遗器：")
            lines.extend(artifact_lines)
    if not lines:
        lines.append("该游戏暂未返回装备明细，或当前角色没有对应装备。")
    return lines


def format_character_report(
    game: str,
    base: dict,
    detail_data: dict,
    mode: str = "character",
) -> str:
    detail_item = _find_detail_item(get_detail_list(game, detail_data), base.get("id"))
    detail_base = detail_item.get("base") if isinstance(detail_item.get("base"), dict) else detail_item
    merged = {**base, **detail_base}
    name = merged.get("name") or base.get("name") or merged.get("id") or "未知角色"
    level = merged.get("level") or "?"
    rarity = merged.get("rarity") or base.get("rarity") or merged.get("star") or "?"
    rank = merged.get("rank", merged.get("actived_constellation_num", base.get("actived_constellation_num")))
    element = merged.get("element") or base.get("element") or ""
    lines = [f"【{GAME_NAMES[game]}·{name}】Lv.{level} · {rarity}星"]
    if element:
        lines.append(f"属性：{element}")
    if rank is not None:
        lines.append(f"{'命座/星魂' if game == 'gs' else '星魂'}：{rank}")

    if mode == "character":
        props = detail_item.get("selected_properties") or detail_item.get("properties") or []
        prop_lines = _property_lines(props, game)
        if prop_lines:
            lines.append("面板：" + " · ".join(prop_lines))
        elif detail_base.get("properties"):
            prop_lines = _property_lines(detail_base.get("properties"), game)
            if prop_lines:
                lines.append("面板：" + " · ".join(prop_lines))
        lines.append("查询装备：米游社 装备 " + str(name))
        lines.append("查询武器：米游社 武器 " + str(name))
        lines.append("查询圣遗物：米游社 圣遗物 " + str(name))
    else:
        lines.extend(_equipment_sections(game, base, detail_item, mode))
    return "\n".join(lines)


def format_abyss(game: str, data: dict) -> str:
    if game == "zzz":
        return "绝区零暂未提供稳定的官方战绩深渊/式舆防卫接口。"
    lines = [f"【{GAME_NAMES[game]}挑战记录】"]
    schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    start = data.get("start_time", schedule.get("start_time"))
    end = data.get("end_time", schedule.get("end_time"))
    if start and end:
        lines.append(f"周期：{_ts_text(start)} 至 {_ts_text(end)}")
    for key, label in {
        "max_floor": "最高层数",
        "total_star": "总星数",
        "star_num": "星数",
        "total_battle_times": "战斗次数",
        "total_win_times": "获胜次数",
        "battle_num": "战斗次数",
    }.items():
        if data.get(key) is not None:
            lines.append(f"{label}：{data[key]}")

    floors = _as_list(data, "floors", "all_floor_detail")
    for floor in floors[:12]:
        index = floor.get("index") or floor.get("name") or floor.get("floor")
        star = floor.get("star")
        line = f"第{index}层" if index not in (None, "") else "挑战层"
        if star is not None:
            line += f"：{star}星"
        teams = []
        for level in _as_list(floor, "levels"):
            for battle in _as_list(level, "battles"):
                avatars = [
                    avatar.get("name") or str(avatar.get("id") or avatar.get("avatar_id") or "?")
                    for avatar in _as_list(battle, "avatars")
                ]
                if avatars:
                    teams.append("/".join(avatars))
        if teams:
            line += "\n  " + "；".join(teams[:2])
        lines.append(line)
    if not floors:
        challenge_info = data.get("challenge_info")
        if isinstance(challenge_info, dict):
            for key, label in (("star_num", "星数"), ("max_floor", "最高层"), ("battle_num", "战斗次数")):
                if challenge_info.get(key) is not None:
                    lines.append(f"{label}：{challenge_info[key]}")
    if len(lines) == 1:
        lines.append("本期暂无可展示记录，可能未挑战或官方数据未公开。")
    return "\n".join(lines)


def format_daily_note(game: str, data: dict) -> str:
    lines = [f"【{GAME_NAMES[game]}实时状态】"]
    labels = {
        "current_resin": "原粹树脂",
        "max_resin": "树脂上限",
        "finished_task_num": "每日委托",
        "total_task_num": "每日委托总数",
        "remain_resin_discount_num": "周本减半剩余",
        "current_expedition_num": "派遣中",
        "max_expedition_num": "派遣上限",
        "current_stamina": "开拓力",
        "max_stamina": "开拓力上限",
        "current_train_score": "实训点数",
        "max_train_score": "实训点数上限",
        "current_rogue_score": "模拟宇宙积分",
        "max_rogue_score": "积分上限",
        "accepted_epedition_num": "委托中",
        "total_expedition_num": "委托上限",
        "energy": "电量",
        "max_energy": "电量上限",
        "vitality": "活跃度",
        "max_vitality": "活跃度上限",
    }
    for key, label in labels.items():
        if key in data and data[key] is not None:
            lines.append(f"{label}：{data[key]}")
    recovery = data.get("resin_recovery_time") or data.get("stamina_recover_time")
    if recovery is not None:
        minutes = max(0, _safe_int(recovery) // 60)
        lines.append(f"恢复时间：约 {minutes // 60} 小时 {minutes % 60} 分钟")
    if len(lines) == 1:
        lines.append("官方接口未返回可展示的实时状态。")
    return "\n".join(lines)


def format_buddy(data: dict) -> str:
    buddies = _as_list(data, "avatar_list", "buddy_list", "list")
    if not buddies:
        return "没有查到邦布数据"
    names = [str(item.get("name") or item.get("id") or "?") for item in buddies]
    return f"【绝区零邦布】共 {len(names)} 个\n" + "、".join(names)


def friendly_error(payload: dict) -> str:
    retcode = _safe_int(payload.get("retcode"))
    message = str(payload.get("message") or "未知错误")
    if retcode in LOGIN_INVALID_RETCODES:
        return f"登录凭证已失效（{retcode}），请重新扫码或绑定 Cookie"
    if retcode == 1008:
        return "该账号还未在米游社绑定对应游戏角色"
    if retcode == 10101:
        return "今日米游社战绩查询次数已达上限"
    if retcode == 10102:
        return "该玩家数据未公开，请到米游社调整战绩展示设置"
    if retcode in (1034, 10035, 10041):
        return f"米游社要求真人验证码（{retcode}）"
    if retcode == 10307:
        return "版本更新期间，米游社战绩数据维护中"
    if retcode == -10002:
        return "未查询到对应游戏角色"
    return f"米游社接口错误（{retcode}）：{message}"


class GameRecordClient:
    """国服米游社战绩客户端；验证码由调用方转交真人网页处理。"""

    def __init__(
        self,
        cookie: str,
        device_id: str,
        device_fp: str = "",
        app_version: str = "2.114.0",
        client_type: str = "5",
        retries: int = 3,
    ):
        self.cookie = cookie
        self.device_id = device_id
        self.device_fp = device_fp
        self.app_version = app_version
        self.client_type = client_type
        self.retries = max(1, retries)

    def _headers(self, query: str, body: str, extra: dict = None) -> dict:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; Unspecified Device) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                f"Chrome/103.0.5060.129 Mobile Safari/537.36 miHoYoBBS/{self.app_version}"
            ),
            "x-rpc-app_version": self.app_version,
            "x-rpc-client_type": self.client_type,
            "Origin": "https://webstatic.mihoyo.com",
            "Referer": "https://webstatic.mihoyo.com/",
            "X-Requested-With": "com.mihoyo.hyperion",
            "x-rpc-device_id": self.device_id,
            "DS": _make_ds(query, body),
            "Cookie": self.cookie,
        }
        if self.device_fp:
            headers["x-rpc-device_fp"] = self.device_fp
        if extra:
            headers.update(extra)
        return headers

    async def _json_request(
        self,
        method: str,
        url: str,
        query: str = "",
        body: str = "",
        extra_headers: dict = None,
    ) -> dict:
        target = f"{url}?{query}" if query else url
        headers = self._headers(query, body, extra_headers)
        if body:
            headers["Content-Type"] = "application/json"
        last_error = "网络请求失败"
        for attempt in range(self.retries):
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.request(
                        method,
                        target,
                        headers=headers,
                        content=body.encode("utf-8") if body else None,
                    )
                if response.status_code == 429:
                    last_error = "请求过于频繁（HTTP 429）"
                elif response.status_code >= 500:
                    last_error = f"米游社服务异常（HTTP {response.status_code}）"
                else:
                    payload = response.json()
                    if _safe_int(payload.get("retcode")) not in RETRYABLE_RETCODES:
                        return payload
                    last_error = friendly_error(payload)
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.retries - 1:
                await asyncio.sleep(3 + attempt * 2)
        raise GameRecordError(last_error)

    async def ensure_device_fp(self):
        if self.device_fp:
            return self.device_fp
        seed = _random_text(16)
        body_data = {
            "seed_id": seed,
            "device_id": self.device_id.upper(),
            "platform": "1",
            "seed_time": str(int(time.time() * 1000)),
            "ext_fields": json.dumps({
                "proxyStatus": "0",
                "model": "iPhone12,5",
                "networkType": "WIFI",
                "osVersion": "17.0.2",
                "cpuType": "CPU_TYPE_ARM64",
            }, separators=(",", ":")),
            "app_name": "bbs_cn",
            "device_fp": "38d7ee834d1e9",
        }
        body = _compact_json(body_data)
        payload = await self._json_request("POST", DEVICE_FP_URL, body=body)
        if _safe_int(payload.get("retcode")) == 0:
            fp = (payload.get("data") or {}).get("device_fp")
            if fp:
                self.device_fp = str(fp)
        return self.device_fp

    def _request_spec(self, request: RecordRequest):
        try:
            method, url = ENDPOINTS[request.game][request.action]
        except KeyError as exc:
            raise GameRecordError(f"{GAME_NAMES[request.game]}暂不支持该查询") from exc
        if method == "POST":
            query_data = dict(request.query)
            body_data = {
                "role_id": request.uid,
                "server": request.server,
                **request.body,
            }
        else:
            query_data = {"role_id": request.uid, "server": request.server}
            query_data.update(request.query)
            body_data = dict(request.body)
        return method, url, _compact_query(query_data), _compact_json(body_data) if body_data else ""

    async def request(self, request: RecordRequest) -> dict:
        await self.ensure_device_fp()
        method, url, query, body = self._request_spec(request)
        payload = await self._json_request(
            method,
            url,
            query=query,
            body=body,
            extra_headers=request.extra_headers,
        )
        retcode = _safe_int(payload.get("retcode"))
        if retcode in CAPTCHA_RETCODES:
            raise GameRecordError(friendly_error(payload), retcode)
        if retcode != 0:
            raise GameRecordError(friendly_error(payload), retcode)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def create_captcha(self, request: RecordRequest, retcode: int) -> CaptchaChallenge:
        await self.ensure_device_fp()
        challenge_game = {"gs": "2", "sr": "6", "zzz": "8"}[request.game]
        app_key = {"gs": "", "sr": "hkrpg_game_record", "zzz": "game_record_zzz"}[request.game]
        _, endpoint_url, _, _ = self._request_spec(request)
        extra_headers = {
            "x-rpc-challenge_game": challenge_game,
            "x-rpc-challenge_path": urlsplit(endpoint_url).path,
        }
        if retcode == 10035:
            query = urlencode({"is_high": "true", "app_key": app_key})
            url = f"{CN_API_HOST}/event/toolcomsrv/risk/createGeetest"
        else:
            query = "is_high=true"
            url = f"{CN_RECORD_HOST}/game_record/app/card/wapi/createVerification"
        payload = await self._json_request(
            "GET",
            url,
            query=query,
            extra_headers=extra_headers,
        )
        if _safe_int(payload.get("retcode")) != 0:
            raise GameRecordError(friendly_error(payload), _safe_int(payload.get("retcode")))
        data = payload.get("data") or {}
        if not data.get("gt") or not data.get("challenge"):
            raise GameRecordError("米游社没有返回有效的验证码参数")
        return CaptchaChallenge(
            request=request,
            retcode=retcode,
            gt=str(data["gt"]),
            challenge=str(data["challenge"]),
            new_captcha=bool(data.get("new_captcha", True)),
            app_key=app_key,
            verify_headers=extra_headers,
        )

    async def verify_captcha(
        self,
        challenge: CaptchaChallenge,
        solved: dict,
    ) -> dict:
        validate = str(solved.get("geetest_validate") or solved.get("validate") or "")
        challenge_value = str(solved.get("geetest_challenge") or solved.get("challenge") or "")
        seccode = str(solved.get("geetest_seccode") or solved.get("seccode") or "")
        if not challenge_value.startswith(challenge.challenge) or not validate:
            raise GameRecordError("验证码结果无效或 challenge 不匹配")
        if not seccode:
            seccode = f"{validate}|jordan"
        body_data = {
            "geetest_challenge": challenge_value,
            "geetest_validate": validate,
            "geetest_seccode": seccode,
        }
        if challenge.retcode == 10035:
            url = f"{CN_API_HOST}/event/toolcomsrv/risk/verifyGeetest"
            body_data["app_key"] = challenge.app_key
        else:
            url = f"{CN_RECORD_HOST}/game_record/app/card/wapi/verifyVerification"
        payload = await self._json_request(
            "POST",
            url,
            body=_compact_json(body_data),
            extra_headers=challenge.verify_headers,
        )
        if _safe_int(payload.get("retcode")) != 0:
            raise GameRecordError(friendly_error(payload), _safe_int(payload.get("retcode")))
        verified_challenge = (payload.get("data") or {}).get("challenge")
        if not verified_challenge and challenge.retcode != 10035:
            raise GameRecordError("米游社未确认验证码，请重新生成验证链接")
        extra_headers = dict(challenge.request.extra_headers)
        if verified_challenge:
            extra_headers["x-rpc-challenge"] = str(verified_challenge)
        retry_request = RecordRequest(
            game=challenge.request.game,
            action=challenge.request.action,
            uid=challenge.request.uid,
            server=challenge.request.server,
            query=challenge.request.query,
            body=challenge.request.body,
            extra_headers=extra_headers,
        )
        return await self.request(retry_request)
