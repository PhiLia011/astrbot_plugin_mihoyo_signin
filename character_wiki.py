# -*- coding: utf-8 -*-
"""离线角色图鉴数据与图片卡片。"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


WIDTH = 1080
PAGE_SIZE = 20
PAGE_COLUMNS = 5
PAGE_ROWS = 4
SIGNATURE = "DESIGNED BY FIREFLY"

ELEMENT_COLORS = {
    "anemo": "#72d7bc",
    "geo": "#e0b85b",
    "electro": "#b889ef",
    "dendro": "#9ed25a",
    "hydro": "#58b8e8",
    "pyro": "#f08a68",
    "cryo": "#8fdbe8",
    "physical": "#c5cad9",
    "multi": "#ead6a3",
    "火": "#f08a68",
    "冰": "#8fdbe8",
    "雷": "#b889ef",
    "风": "#72d7bc",
    "量子": "#8a8cff",
    "虚数": "#e9d66e",
    "物理": "#c5cad9",
    "记忆": "#7ed4e2",
    "欢愉": "#ef8fb9",
}

ELEMENT_NAMES = {
    "anemo": "风",
    "geo": "岩",
    "electro": "雷",
    "dendro": "草",
    "hydro": "水",
    "pyro": "火",
    "cryo": "冰",
    "physical": "物理",
    "multi": "旅行者",
}

STAT_NAMES = {
    "hp": "生命",
    "atk": "攻击",
    "def": "防御",
    "speed": "速度",
    "cpct": "暴击率",
    "cdmg": "暴击伤害",
    "recharge": "充能效率",
    "mastery": "元素精通",
}

GROWTH_NAMES = {
    "atkPct": "攻击加成",
    "hpPct": "生命加成",
    "defPct": "防御加成",
    "cpct": "暴击率",
    "cdmg": "暴击伤害",
    "recharge": "元素充能",
    "mastery": "元素精通",
    "heal": "治疗加成",
    "phy": "物理伤害",
}


def _hex_rgb(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        alpha,
    )


def _mix(color: str, target: str, ratio: float) -> str:
    first = _hex_rgb(color)
    second = _hex_rgb(target)
    values = [round(a + (b - a) * ratio) for a, b in zip(first[:3], second[:3])]
    return "#" + "".join(f"{value:02x}" for value in values)


def _number(value, decimals: int = 0) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value or "—")
    if decimals:
        return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"{value:,.0f}"


class CharacterWiki:
    def __init__(self, plugin_dir: str, data_dir: str):
        self.plugin_dir = Path(plugin_dir)
        self.data_path = self.plugin_dir / "wiki_data.json"
        self.asset_dir = self.plugin_dir / "wiki_assets"
        self.cache_dir = Path(data_dir) / "wiki_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.data = json.loads(self.data_path.read_text("utf-8"))
        self.data_version = f"{self.data.get('updated_at')}:{self.data_path.stat().st_mtime_ns}"
        self.render_version = str(Path(__file__).stat().st_mtime_ns)
        self.games = self.data.get("games", {})
        self.games.setdefault("zzz", {"name": "绝区零", "characters": []})
        self.font_path = self._find_font()

    def _find_font(self) -> str:
        candidates = [
            self.asset_dir / "fonts" / "NotoSansSC-VF.ttf",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/microsoft/msyh.ttc"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        raise RuntimeError("未找到中文字体，请把 NotoSansSC-VF.ttf 放入 wiki_assets/fonts/")

    def _font(self, size: int, weight: int = 400):
        font = ImageFont.truetype(self.font_path, size)
        try:
            if "NotoSansSC-VF" in self.font_path:
                font.set_variation_by_axes([weight])
        except (AttributeError, OSError, ValueError):
            pass
        return font

    def game_title(self, game: str) -> str:
        return self.games.get(game, {}).get("name", game)

    def characters(self, game: str) -> list[dict]:
        return self.games.get(game, {}).get("characters", [])

    def parse_command_args(self, message: str) -> tuple[str, bool, int, str]:
        text = (message or "").strip()
        for prefix in ("/米游社", "米游社", "/mys", "mys"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break
        text = text.replace("角色图鉴", "图鉴")
        marker = text.find("图鉴")
        if marker >= 0:
            text = (text[:marker] + " " + text[marker + 2:]).strip()

        page_match = re.search(r"第?\s*(\d+)\s*页", text)
        page = int(page_match.group(1)) if page_match else 1
        if page_match:
            text = (text[:page_match.start()] + text[page_match.end():]).strip()

        explicit_game = bool(re.search(
            r"原神|genshin|\bys\b|星铁|崩铁|星穹铁道|starrail|\bsr\b|"
            r"崩坏学园2|崩坏2|崩2|\bbh2\b|\bbb\b|\bb2\b",
            text,
            re.I,
        ))
        game, text = self.detect_game(text, "gs")
        query = text.strip(" ：:，,·")
        return game, explicit_game, page, query

    def detect_game(self, text: str, default: str = "gs") -> tuple[str, str]:
        text = (text or "").strip()
        aliases = {
            "原神": "gs",
            "genshin": "gs",
            "ys": "gs",
            "星铁": "sr",
            "崩铁": "sr",
            "星穹铁道": "sr",
            "崩坏：星穹铁道": "sr",
            "starrail": "sr",
            "sr": "sr",
            "崩坏学园2": "bh2",
            "崩坏2": "bh2",
            "崩2": "bh2",
            "bh2": "bh2",
            "bb": "bh2",
            "b2": "bh2",
        }
        for token in sorted(aliases, key=len, reverse=True):
            position = text.lower().find(token.lower())
            if position >= 0:
                text = (text[:position] + text[position + len(token):]).strip()
                return aliases[token], text
        return default, text

    @staticmethod
    def _normalize(value: str) -> str:
        value = (value or "").lower()
        for token in (" ", "\t", "·", "•", "・", "_", "-"):
            value = value.replace(token, "")
        return value

    def search(self, query: str, game: str | None = None) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        wanted = self._normalize(query)
        games = [game] if game else ["gs", "sr", "bh2"]
        exact = []
        partial = []
        for game_key in games:
            for char in self.characters(game_key):
                aliases = [char.get("name", ""), *char.get("aliases", [])]
                normalized = [self._normalize(alias) for alias in aliases]
                if wanted in normalized:
                    exact.append((game_key, char))
                elif any(wanted in alias for alias in normalized if alias):
                    partial.append((game_key, char))
        found = exact or partial
        return [dict(char, game=game_key) for game_key, char in found]

    def _clean_cache(self) -> None:
        files = sorted(
            self.cache_dir.glob("wiki_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in files[80:]:
            path.unlink(missing_ok=True)

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
        return self.cache_dir / f"wiki_{digest}.png"

    def _new_canvas(self, height: int, color: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = Image.new("RGBA", (WIDTH, height), _hex_rgb("#10131b"))
        draw = ImageDraw.Draw(image)
        top = _hex_rgb(_mix(color, "#11141c", 0.74))
        middle = _hex_rgb("#11141c")
        for y in range(height):
            ratio = min(1.0, y / 900)
            row = tuple(round(top[i] + (middle[i] - top[i]) * ratio) for i in range(3))
            draw.line((0, y, WIDTH, y), fill=(*row, 255))

        glow = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((610, -220, 1260, 430), fill=_hex_rgb(color, 48))
        glow_draw.ellipse((-260, 280, 410, 900), fill=_hex_rgb(color, 24))
        glow = glow.filter(ImageFilter.GaussianBlur(90))
        image.alpha_composite(glow)
        draw = ImageDraw.Draw(image)
        draw.rectangle((42, 34, 45, height - 34), fill=_hex_rgb(color, 210))
        return image, draw

    @staticmethod
    def _wrap(draw, text: str, font, width: int, max_lines: int) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
                continue
            if current:
                lines.append(current.rstrip())
            current = char.lstrip()
            if len(lines) == max_lines:
                break
        if len(lines) < max_lines and current:
            lines.append(current.rstrip())
        if not lines:
            return []
        if len(lines) == max_lines and draw.textlength(text, font=font) > sum(
            draw.textlength(line, font=font) for line in lines
        ):
            tail = lines[-1]
            while tail and draw.textlength(tail + "…", font=font) > width:
                tail = tail[:-1]
            lines[-1] = tail + "…"
        return lines

    def _draw_text(
        self,
        draw,
        position: tuple[int, int],
        text: str,
        size: int,
        fill: str,
        weight: int = 400,
        width: int | None = None,
        max_lines: int = 1,
        spacing: int = 8,
    ) -> int:
        font = self._font(size, weight)
        lines = self._wrap(draw, text, font, width, max_lines) if width else [text]
        x, y = position
        for line in lines:
            draw.text((x, y), line, font=font, fill=_hex_rgb(fill))
            y += size + spacing
        return y

    def _draw_footer(self, draw, height: int, game: str) -> None:
        draw.line((68, height - 78, WIDTH - 68, height - 78), fill=_hex_rgb("#ffffff", 24))
        draw.text(
            (68, height - 57),
            f"{SIGNATURE}  ·  {self.game_title(game)}角色图鉴",
            font=self._font(15, 500),
            fill=_hex_rgb("#ffffff", 105),
        )
        draw.text(
            (WIDTH - 224, height - 57),
            "MITSU · CHARACTER ARCHIVE",
            font=self._font(13, 500),
            fill=_hex_rgb("#ffffff", 66),
        )

    def _draw_avatar(self, image, char: dict, box: tuple[int, int, int, int], radius: int) -> None:
        icon_path = Path(char.get("_icon_path") or (self.asset_dir / str(char.get("icon") or "")))
        if not icon_path.exists():
            return
        icon = Image.open(icon_path).convert("RGBA")
        icon = icon.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, icon.width, icon.height), radius, fill=255)
        layer = Image.new("RGBA", icon.size, (0, 0, 0, 0))
        layer.paste(icon, (0, 0), mask)
        image.alpha_composite(layer, (box[0], box[1]))

    def render_roster(
        self,
        game: str,
        title: str,
        characters: list[dict],
        icon_paths: list[Path],
    ) -> Path:
        rows = max(1, math.ceil(min(len(characters), 20) / 5))
        height = max(720, 250 + rows * 256 + 120)
        image, draw = self._new_canvas(height, ELEMENT_COLORS.get("虚数", "#d6bd88"))
        draw.text((62, 64), self.game_title(game), font=self._font(18, 600), fill=_hex_rgb("#d8c194"))
        draw.text((60, 100), title, font=self._font(58, 700), fill="#fffaf0")
        draw.text(
            (64, 180),
            f"{len(characters)} 位角色 · ACCOUNT ROSTER",
            font=self._font(13, 500),
            fill=_hex_rgb("#ffffff", 112),
        )
        grid_top = 250
        cell_width = 178
        cell_gap = 16
        cell_height = 238
        for index, char in enumerate(characters[:20]):
            row, column = divmod(index, 5)
            left = 54 + column * (cell_width + cell_gap)
            top = grid_top + row * (cell_height + 18)
            draw.rounded_rectangle(
                (left, top, left + cell_width, top + cell_height),
                14,
                fill=_hex_rgb("#e9e3d6", 242),
                outline=_hex_rgb("#fffaf0", 120),
                width=1,
            )
            icon_char = dict(char)
            if index < len(icon_paths):
                icon_char["_icon_path"] = str(icon_paths[index])
            self._draw_avatar(image, icon_char, (left + 10, top + 10, left + 168, top + 168), 10)
            draw = ImageDraw.Draw(image)
            draw.text(
                (left + 89, top + 186),
                str(char.get("name") or "未知角色"),
                font=self._font(18 if len(str(char.get("name") or "")) <= 6 else 15, 700),
                fill="#3d3428",
                anchor="ma",
            )
            level = char.get("level")
            rank = char.get("rank")
            detail = []
            if level is not None:
                detail.append(f"Lv.{level}")
            if rank is not None:
                detail.append(f"{rank}命")
            draw.text(
                (left + 89, top + 213),
                " · ".join(detail) or "★" * int(char.get("star") or 0),
                font=self._font(12, 500),
                fill=_hex_rgb("#9b7429"),
                anchor="ma",
            )
        self._draw_footer(draw, height, game)
        cache = self._cache_path(
            f"roster:{game}:{self.render_version}:"
            + hashlib.md5(
                "|".join(str(item.get("id") or item.get("name")) for item in characters).encode()
            ).hexdigest()
        )
        image.convert("RGB").save(cache, "PNG", optimize=True)
        self._clean_cache()
        return cache

    def _detail_background(self, char: dict, color: str, height: int) -> Image.Image:
        image = Image.new("RGBA", (WIDTH, height), _hex_rgb(_mix(color, "#171513", 0.84)))
        art_path = self.asset_dir / str(char.get("background") or "")
        if not art_path.exists():
            art_path = self.asset_dir / str(char.get("icon") or "")
        if not art_path.exists():
            return image

        with Image.open(art_path) as source:
            art = source.convert("RGBA")

        blurred = ImageOps.fit(
            art,
            (WIDTH, 1150),
            method=Image.Resampling.LANCZOS,
            centering=(0.62, 0.38),
        )
        blurred = blurred.filter(ImageFilter.GaussianBlur(18))
        shade = Image.new("RGBA", blurred.size, (16, 14, 12, 150))
        blurred.alpha_composite(shade)
        image.alpha_composite(blurred, (0, 0))

        sharp = art.copy()
        sharp.thumbnail((1100, 1180), Image.Resampling.LANCZOS)
        image.alpha_composite(sharp, ((WIDTH - sharp.width) // 2 + 34, 12))

        overlay = Image.new("RGBA", (WIDTH, 1160), (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(overlay)
        for x in range(0, 870):
            alpha = round(205 * (1 - x / 870) ** 1.35)
            shade_draw.line((x, 0, x, 1160), fill=(10, 9, 8, alpha))
        for y in range(700, 1160):
            alpha = round(230 * (y - 700) / 460)
            shade_draw.line((0, y, WIDTH, y), fill=(13, 12, 11, alpha))
        image.alpha_composite(overlay)
        return image

    def _constellation_icon(self, char: dict, number: int, lit: bool, size: int) -> Image.Image:
        path = self.asset_dir / "ui" / "cons0.webp"
        if number:
            icons = char.get("constellation_icons") or []
            if number <= len(icons) and icons[number - 1]:
                path = self.asset_dir / icons[number - 1]
        if not path.exists():
            return Image.new("RGBA", (size, size), (0, 0, 0, 0))
        with Image.open(path) as source:
            icon = source.convert("RGBA")
        icon.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(icon, ((size - icon.width) // 2, (size - icon.height) // 2))
        if not lit:
            gray = canvas.convert("L").convert("RGBA")
            alpha = gray.getchannel("A").point(lambda value: value * 38 // 100)
            gray.putalpha(alpha)
            return gray
        return canvas

    def _stat_items(self, char: dict) -> list[tuple[str, str]]:
        game = char["game"]
        if game == "bh2":
            return []
        base = char.get("base") or {}
        element = str(char.get("element") or "")
        element_label = ELEMENT_NAMES.get(element, element)
        if game == "gs":
            stats = [
                ("生命", _number(base.get("hp"))),
                ("攻击", _number(base.get("atk"))),
                ("防御", _number(base.get("def"))),
            ]
            growth = char.get("growth") or {}
            if growth.get("key"):
                key = str(growth["key"])
                name = (
                    f"{element_label}伤"
                    if key == "dmg"
                    else GROWTH_NAMES.get(key, ELEMENT_NAMES.get(key, key))
                )
                value = growth.get("value")
                suffix = "%" if isinstance(value, (int, float)) and key != "mastery" else ""
                stats.append((name, f"{_number(value, 1)}{suffix}"))
            return stats

        stats = [
            ("生命", _number(base.get("hp"))),
            ("攻击", _number(base.get("atk"))),
            ("防御", _number(base.get("def"))),
            ("速度", _number(base.get("speed"))),
            ("暴击", f"{_number(base.get('cpct'), 1)}%"),
            ("爆伤", f"{_number(base.get('cdmg'), 1)}%"),
        ]
        if char.get("sp"):
            stats.append(("能量上限", _number(char["sp"])))
        return stats

    def _data_uri(self, path: Path) -> str:
        if not path.exists():
            return "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        mime = "image/webp" if path.suffix.lower() == ".webp" else "image/png"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"

    def detail_html_payload(self, char: dict) -> tuple[str, dict]:
        game = char.get("game", "gs")
        element = str(char.get("element") or "")
        element_label = ELEMENT_NAMES.get(element, element)
        if game == "bh2":
            facts = [
                {"label": "所属", "value": "崩坏学园2"},
                {"label": "资料", "value": "萌娘百科 / Fandom"},
            ]
        else:
            facts = [
                {"label": "所属", "value": char.get("allegiance") or "未公开"},
                {"label": "生日", "value": char.get("birthday") or "未公开"},
                {
                    "label": "属性" if game == "sr" else "命之座",
                    "value": char.get("astro") or element_label,
                },
                {"label": "中文 CV", "value": char.get("cncv") or "未公开"},
                {"label": "日文 CV", "value": char.get("jpcv") or "未公开"},
            ]

        icons = char.get("constellation_icons") or []
        constellations = []
        if game in ("gs", "sr"):
            for number in range(7):
                icon_path = self.asset_dir / "ui" / "cons0.webp"
                if number and number <= len(icons) and icons[number - 1]:
                    icon_path = self.asset_dir / icons[number - 1]
                constellations.append(
                    {
                        "label": f"{number}命",
                        "lit": number > 0 and bool(icon_path.exists()),
                        "icon": self._data_uri(icon_path),
                    }
                )

        background_path = self.asset_dir / str(char.get("background") or "")
        template = (self.plugin_dir / "character_wiki_template.html").read_text("utf-8")
        return template, {
            "background": self._data_uri(background_path),
            "background_size": char.get("background_size") or "cover",
            "background_position": char.get("background_position") or "center 22%",
            "char": char,
            "game_name": self.game_title(game),
            "facts": facts,
            "constellations": constellations,
            "stats": [
                {"label": label, "value": value}
                for label, value in self._stat_items(char)
            ],
            "skills": char.get("skills") or [],
            "materials": char.get("materials") or [],
            "source_label": (
                "UI reused from bh2-plugin · MIT"
                if game == "bh2"
                else "UI reused from Miao-Plugin · MIT"
            ),
        }

    def _render_bh2_fallback(self, char: dict, cache: Path) -> Path:
        height = 1400
        image = self._detail_background(char, "#93e5ed", height)
        draw = ImageDraw.Draw(image)
        gold = "#d8c194"
        draw.rounded_rectangle((22, 22, WIDTH - 22, height - 22), 18, outline=_hex_rgb(gold, 74), width=2)
        draw.text((52, 62), "崩坏学园2 · CHARACTER ARCHIVE", font=self._font(15, 600), fill=_hex_rgb(gold))
        draw.text((48, 110), char["name"], font=self._font(60, 700), fill="#fffaf0")
        self._draw_text(
            draw,
            (52, 230),
            char.get("desc") or "该角色的详细资料暂未收录。",
            23,
            "#e1dbcf",
            400,
            520,
            8,
            12,
        )
        draw.text((52, 1260), "资料：萌娘百科 / Fandom", font=self._font(16, 500), fill=_hex_rgb(gold))
        self._draw_footer(draw, height, "bh2")
        image.convert("RGB").save(cache, "PNG", optimize=True)
        return cache

    def render_detail(self, char: dict) -> Path:
        game = char["game"]
        element_color = ELEMENT_COLORS.get(str(char.get("element") or ""), "#d6bd88")
        gold = "#d8c194"
        text = "#f4f0e7"
        muted = "#b5ad9f"
        cache = self._cache_path(
            f"detail:{game}:{char['id']}:{self.data_version}:{self.render_version}"
        )
        if cache.exists():
            return cache
        if game == "bh2":
            return self._render_bh2_fallback(char, cache)

        height = 1660
        image = self._detail_background(char, element_color, height)
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle(
            (22, 22, WIDTH - 22, height - 22),
            18,
            outline=_hex_rgb(gold, 74),
            width=2,
        )
        draw.line((48, 46, 310, 46), fill=_hex_rgb(gold, 160), width=1)
        draw.text(
            (50, 56),
            f"{self.game_title(game)}  ·  CHARACTER ARCHIVE",
            font=self._font(15, 600),
            fill=_hex_rgb(gold),
        )

        name_size = 68 if len(char["name"]) <= 6 else 56
        draw.text((48, 98), char["name"], font=self._font(name_size, 700), fill=_hex_rgb(text))
        y = 190
        if char.get("title"):
            draw.text((52, y), char["title"], font=self._font(24, 500), fill=_hex_rgb(gold))
            y += 44
        draw.text(
            (52, y),
            "★" * int(char.get("star") or 0),
            font=self._font(22, 600),
            fill=_hex_rgb("#f4cd73"),
        )
        y += 43

        element = str(char.get("element") or "")
        element_label = ELEMENT_NAMES.get(element, element)
        draw.text(
            (52, y),
            f"{element_label}  /  {char.get('weapon') or '—'}",
            font=self._font(17, 500),
            fill=_hex_rgb(text),
        )

        self._draw_text(
            draw,
            (52, 326),
            char.get("desc") or "暂无角色介绍。",
            20,
            "#ddd5c7",
            400,
            520,
            3,
            9,
        )

        facts = [
            ("所属", char.get("allegiance") or "未公开"),
            ("生日", char.get("birthday") or "未公开"),
            (("属性" if game == "sr" else "命之座"), char.get("astro") or element_label),
            ("中文 CV", char.get("cncv") or "未公开"),
            ("日文 CV", char.get("jpcv") or "未公开"),
        ]
        fact_left, fact_top, fact_width = 618, 276, 396
        draw.rounded_rectangle(
            (fact_left, fact_top, fact_left + fact_width, fact_top + 255),
            12,
            fill=_hex_rgb("#11100f", 132),
            outline=_hex_rgb("#ffffff", 28),
            width=1,
        )
        for index, (label, value) in enumerate(facts):
            row_top = fact_top + index * 48 + 12
            if index:
                draw.line(
                    (fact_left + 18, row_top - 6, fact_left + fact_width - 18, row_top - 6),
                    fill=_hex_rgb("#ffffff", 20),
                )
            draw.text((fact_left + 22, row_top + 4), label, font=self._font(14, 500), fill=_hex_rgb(gold))
            draw.text(
                (fact_left + 120, row_top),
                str(value),
                font=self._font(16 if len(str(value)) > 9 else 18, 500),
                fill=_hex_rgb(text),
            )

        cons_top = 676
        draw.rounded_rectangle(
            (48, cons_top, WIDTH - 48, cons_top + 226),
            16,
            fill=_hex_rgb("#12100e", 150),
            outline=_hex_rgb(gold, 54),
            width=1,
        )
        draw.text((72, cons_top + 22), "命之座" if game == "gs" else "星魂", font=self._font(22, 700), fill=_hex_rgb(gold))
        draw.text(
            (194, cons_top + 30),
            "CONSTELLATION",
            font=self._font(11, 500),
            fill=_hex_rgb("#ffffff", 88),
        )

        cons_icons = char.get("constellation_icons") or []
        start_x = 112
        step = 142
        icon_size = 82
        for number in range(7):
            center_x = start_x + number * step
            icon_top = cons_top + 62
            lit = number > 0 and number <= len(cons_icons) and bool(cons_icons[number - 1])
            if number == 0:
                lit = False
            draw.ellipse(
                (
                    center_x - icon_size // 2 - 5,
                    icon_top - 5,
                    center_x + icon_size // 2 + 5,
                    icon_top + icon_size + 5,
                ),
                fill=_hex_rgb("#0c0b0a", 155),
                outline=_hex_rgb(gold if lit else "#77716a", 145 if lit else 84),
                width=2,
            )
            icon = self._constellation_icon(char, number, lit, icon_size)
            if icon.getbbox():
                image.alpha_composite(
                    icon,
                    (center_x - icon_size // 2, icon_top),
                )
            elif number:
                draw.text(
                    (center_x - 9, icon_top + 21),
                    str(number),
                    font=self._font(25, 600),
                    fill=_hex_rgb(gold if lit else "#77716a"),
                )
            draw.text(
                (center_x - 15, icon_top + icon_size + 13),
                f"{number}命",
                font=self._font(14, 500),
                fill=_hex_rgb(gold if lit else "#77716a"),
            )
        draw = ImageDraw.Draw(image)

        lower_top = 952
        draw.rectangle((22, lower_top, WIDTH - 22, height - 22), fill=_hex_rgb("#1a1715", 236))
        texture_path = self.asset_dir / "ui" / "card-bg.png"
        if texture_path.exists():
            with Image.open(texture_path) as texture:
                texture = texture.convert("RGBA").resize((WIDTH - 44, 400))
                texture.putalpha(texture.getchannel("A").point(lambda value: value * 55 // 100))
                image.alpha_composite(texture, (22, lower_top))
            draw = ImageDraw.Draw(image)

        draw.text((52, 982), "满级基础属性", font=self._font(22, 700), fill=_hex_rgb(gold))
        draw.text((222, 990), "MAX LEVEL", font=self._font(11, 500), fill=_hex_rgb("#ffffff", 72))
        stats = self._stat_items(char)
        stat_width = 884 / len(stats)
        for index, (label, value) in enumerate(stats):
            left = 52 + index * stat_width
            if index:
                draw.line((left, 1028, left, 1074), fill=_hex_rgb("#ffffff", 26))
            draw.text((left + 15, 1027), label, font=self._font(13, 500), fill=_hex_rgb(muted))
            draw.text((left + 15, 1047), value, font=self._font(21, 700), fill=_hex_rgb(text))

        skills_y = 1102
        draw.line((52, skills_y, WIDTH - 52, skills_y), fill=_hex_rgb(gold, 45))
        draw.text((52, skills_y + 22), "技能" if game == "gs" else "行迹", font=self._font(22, 700), fill=_hex_rgb(gold))
        draw.text((116, skills_y + 29), "TALENTS", font=self._font(11, 500), fill=_hex_rgb("#ffffff", 70))
        skills = char.get("skills") or []
        card_width = 456
        card_height = 118
        start_y = skills_y + 72
        for index, skill in enumerate(skills[:4]):
            row, column = divmod(index, 2)
            left = 64 + column * 488
            top = start_y + row * 132
            if index:
                draw.line((left, top - 7, left + card_width, top - 7), fill=_hex_rgb("#ffffff", 18))
            draw.text((left, top + 5), skill["type"], font=self._font(13, 600), fill=_hex_rgb(gold))
            draw.text((left + 72, top), skill["name"], font=self._font(18, 600), fill=_hex_rgb(text))
            self._draw_text(
                draw,
                (left, top + 37),
                skill.get("desc") or "暂无技能说明。",
                14,
                muted,
                400,
                card_width,
                3,
                5,
            )
        if not skills:
            draw.text((52, start_y + 8), "该角色暂无技能资料。", font=self._font(17), fill=_hex_rgb(muted))

        materials = char.get("materials") or []
        if materials:
            material_y = 1470
            draw.text((52, material_y), "养成素材", font=self._font(18, 700), fill=_hex_rgb(gold))
            x = 172
            for material in materials[:6]:
                text = str(material)
                box_width = int(draw.textlength(text, font=self._font(14, 500))) + 34
                if x + box_width > WIDTH - 70:
                    break
                draw.rounded_rectangle(
                    (x, material_y - 5, x + box_width, material_y + 33),
                    8,
                    fill=_hex_rgb("#2b2722", 190),
                    outline=_hex_rgb(gold, 48),
                    width=1,
                )
                draw.text((x + 15, material_y + 2), text, font=self._font(14, 500), fill="#d9dde7")
                x += box_width + 9

        self._draw_footer(draw, height, game)
        image.convert("RGB").save(cache, "PNG", optimize=True)
        self._clean_cache()
        return cache

    def render_index(self, game: str, page: int = 1) -> Path:
        gold = "#d8c194"
        characters = list(reversed(self.characters(game)))
        pages = max(1, math.ceil(len(characters) / PAGE_SIZE))
        page = min(max(1, page), pages)
        page_characters = characters[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        cache = self._cache_path(
            f"index:{game}:{page}:{self.data_version}:{self.render_version}:{len(characters)}"
        )
        if cache.exists():
            return cache

        height = 1550
        image = Image.new("RGBA", (WIDTH, height), _hex_rgb("#171411"))
        featured = page_characters[0] if page_characters else characters[0]
        art_path = self.asset_dir / str(featured.get("background") or "")
        if art_path.exists():
            with Image.open(art_path) as source:
                banner = ImageOps.fit(
                    source.convert("RGBA"),
                    (WIDTH, 390),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.55, 0.28),
                )
            banner = banner.filter(ImageFilter.GaussianBlur(3))
            banner.alpha_composite(Image.new("RGBA", banner.size, (24, 19, 15, 125)))
            image.alpha_composite(banner, (0, 0))
        draw = ImageDraw.Draw(image)
        for y in range(0, 390):
            alpha = round(185 * min(1, y / 320))
            draw.line((0, y, WIDTH, y), fill=(20, 16, 13, alpha))

        draw.rounded_rectangle((18, 18, WIDTH - 18, height - 18), 18, outline=_hex_rgb(gold, 70), width=2)
        draw.text((58, 64), self.game_title(game), font=self._font(18, 600), fill=_hex_rgb(gold))
        draw.text((56, 105), "角色图鉴", font=self._font(68, 700), fill="#fffaf0")
        draw.text(
            (60, 196),
            f"CHARACTER ARCHIVE  ·  {len(characters)} CHARACTERS",
            font=self._font(13, 500),
            fill=_hex_rgb("#ffffff", 122),
        )
        draw.text(
            (846, 144),
            f"{page:02d} / {pages:02d}",
            font=self._font(22, 600),
            fill=_hex_rgb(gold),
        )

        grid_top = 426
        cell_width = 178
        cell_gap_x = 16
        row_gap = 18
        cell_height = 238
        for index, char in enumerate(page_characters):
            row, column = divmod(index, PAGE_COLUMNS)
            left = 54 + column * (cell_width + cell_gap_x)
            top = grid_top + row * (cell_height + row_gap)
            draw.rounded_rectangle(
                (left, top, left + cell_width, top + cell_height),
                14,
                fill=_hex_rgb("#e9e3d6", 242),
                outline=_hex_rgb("#fffaf0", 120),
                width=1,
            )
            self._draw_avatar(image, char, (left + 10, top + 10, left + 168, top + 168), 10)
            draw = ImageDraw.Draw(image)
            element_color = ELEMENT_COLORS.get(str(char.get("element") or ""), gold)
            draw.ellipse((left + 12, top + 12, left + 54, top + 54), fill=_hex_rgb(element_color, 218))
            element = str(char.get("element") or "")
            draw.text(
                (left + 25, top + 22),
                ELEMENT_NAMES.get(element, element[:1] or "?"),
                font=self._font(14, 700),
                fill="#ffffff",
            )
            draw.text(
                (left + 89, top + 186),
                char["name"],
                font=self._font(18 if len(char["name"]) <= 6 else 15, 700),
                fill="#3d3428",
                anchor="ma",
            )
            draw.text(
                (left + 89, top + 213),
                "★" * int(char.get("star") or 0),
                font=self._font(12, 500),
                fill=_hex_rgb("#b68b35"),
                anchor="ma",
            )

        self._draw_footer(draw, height, game)
        image.convert("RGB").save(cache, "PNG", optimize=True)
        self._clean_cache()
        return cache

    def render_home(self) -> Path:
        gold = "#d8c194"
        cache = self._cache_path(f"home:{self.data_version}:{self.render_version}")
        if cache.exists():
            return cache

        height = 1080
        image = Image.new("RGBA", (WIDTH, height), _hex_rgb("#171411"))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 18, WIDTH - 18, height - 18), 18, outline=_hex_rgb(gold, 70), width=2)
        draw.text((60, 62), "MIYOUSHE · CHARACTER ARCHIVE", font=self._font(15, 600), fill=_hex_rgb(gold))
        draw.text((58, 102), "角色图鉴", font=self._font(72, 700), fill="#fffaf0")
        self._draw_text(
            draw,
            (62, 206),
            "查看角色资料、满级面板、技能与命座。无需绑定米游社账号。",
            21,
            "#c8c0b2",
            400,
            890,
            2,
            10,
        )
        cards = [
            ("gs", "原神", len(self.characters("gs")), "anemo"),
            ("sr", "崩坏：星穹铁道", len(self.characters("sr")), "虚数"),
        ]
        for index, (game, name, count, element) in enumerate(cards):
            left = 58 + index * 492
            top = 300
            card_width, card_height = 472, 610
            sample = (self.search("胡桃" if game == "gs" else "流萤", game) or self.characters(game)[-1:])[0]
            art_path = self.asset_dir / str(sample.get("background") or "")
            card = Image.new("RGBA", (card_width, card_height), _hex_rgb("#201b16"))
            if art_path.exists():
                with Image.open(art_path) as source:
                    art = ImageOps.fit(
                        source.convert("RGBA"),
                        (card_width, card_height),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.55, 0.34),
                    )
                card.alpha_composite(art)
            card_draw = ImageDraw.Draw(card)
            for y in range(270, card_height):
                alpha = round(222 * (y - 270) / (card_height - 270))
                card_draw.line((0, y, card_width, y), fill=(18, 15, 13, alpha))
            mask = Image.new("L", (card_width, card_height), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, card_width - 1, card_height - 1),
                14,
                fill=255,
            )
            image.paste(card, (left, top), mask)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (left, top, left + card_width, top + card_height),
                14,
                outline=_hex_rgb(gold, 104),
                width=2,
            )
            draw.text((left + 28, top + 392), name, font=self._font(30, 700), fill="#fffaf0")
            draw.text(
                (left + 30, top + 442),
                f"{count} 位角色",
                font=self._font(17, 500),
                fill=_hex_rgb(gold),
            )
            command = "米游社 图鉴 原神" if game == "gs" else "米游社 图鉴 星铁"
            draw.text((left + 30, top + 492), "浏览全部", font=self._font(14, 500), fill=_hex_rgb("#ffffff", 115))
            draw.text((left + 30, top + 522), command, font=self._font(21, 600), fill="#fffaf0")

        draw.rounded_rectangle(
            (58, 944, 1022, 1018),
            12,
            fill=_hex_rgb("#2a241d", 190),
            outline=_hex_rgb(gold, 46),
            width=1,
        )
        draw.text((82, 965), "查询角色", font=self._font(16, 600), fill=_hex_rgb(gold))
        draw.text(
            (182, 960),
            "米游社 图鉴 胡桃    ·    米游社 图鉴 星铁 流萤",
            font=self._font(21, 600),
            fill="#fffaf0",
        )
        self._draw_footer(draw, height, "gs")
        image.convert("RGB").save(cache, "PNG", optimize=True)
        self._clean_cache()
        return cache
