#!/usr/bin/env python3
"""把 config/sources.json 里的多个上游源聚合成 dist/ 下的订阅文件。

设计要点
--------
1. **零依赖**：只用标准库，GitHub Actions 上 `python3 scripts/aggregate.py` 直接跑，
   不用装 pip 包，也就不会哪天因为依赖版本漂移而坏掉。
2. **上游坏了不连坐**：单个源超时/404/格式怪，只记进 manifest 的报错字段，
   其余源照常聚合。聚合文件的可用性不该由最差的那个上游决定。
3. **过滤要透明**：被丢掉的条目（存活标记行、示例 mp4）逐条记进 manifest，
   而不是静默丢弃 —— 用户看得到「为什么我的台少了」。
4. **内容不变不落盘**：输出按字节比对，一致就不写。这样 Actions 定时跑
   不会产生一堆空提交。
5. **节目单只给地址**：EPG 一天更新多次、解开几 MB，搬进仓库只会撑爆体积。
   脚本只负责把地址写进 m3u 的 `x-tvg-url`，并每次抓一次报「多少台多少档」，
   上游挂了能在 manifest 里看见。

LeimaoTV 兼容性约束
-------------------
`TxtParser` 以**第一个逗号**切「名称,地址」，所以输出前必须把频道名里的
中英文逗号换成空格，否则名字被截断、地址被拼错。TXT 落 UTF-8 BOM
（`TxtParser.stripBom()` 会去掉，而 Windows 播放器靠 BOM 才不乱码）。

用法
----
    python3 scripts/aggregate.py            # 生成 dist/
    python3 scripts/aggregate.py --check    # 只抓取与体检，不写文件
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.json"
DIST = ROOT / "dist"

GENRE_MARK = "#genre#"
# 判断「这一行是不是能拆出名称 + 地址」时，用协议头的位置而不是逗号：
# 名称里带逗号（`CCTV5+,高清`）、地址里带逗号都不至于拆错。
# 除 http(s) 还要认 rtsp / rtmp —— 电信/联通的 IPTV 单播源基本只有 rtsp，
# 只认 http 会把整条源判成「一条都解析不出来」。不认更多协议是刻意的：
# 认宽了会把 `C:\record\a.ts`、`/mnt/vod/b.ts` 这类脏行当成频道地址。
STREAM_AT = re.compile(r"(?:https?|rtsp|rtmp)://", re.IGNORECASE)
# 上游的「更新时间」占位行两种写法：2026-10-25 06:04 与 20261025 06:04。
# 只认 8 位数字后面紧跟空白/行尾/T 的形式，免得把「2022冬奥纪实」这类真频道名误杀。
DATE_MARKER = re.compile(r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{8}(?=\s|$|T))")
BARE_DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9-]+\.(?:com|cn|net|org|top|cc|tv|me|io)$", re.I)
# 频道名里不许出现的字符：逗号会破坏 TXT 的「名称,地址」结构，换行同理
NAME_CLEAN = re.compile(r"[,，\r\n\t]+")
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff]")


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
def fetch(url: str, ua: str, timeout: int, retries: int) -> tuple[bytes, int, str | None]:
    """返回 (内容, HTTP 状态, 错误说明)。重试只针对网络层错误，4xx 立刻放弃。"""
    last_err: str | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua, "Accept": "*/*", "Accept-Language": "*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), resp.status, None
        except urllib.error.HTTPError as exc:
            # 4xx 是确定性的（源没了 / 路径错了），重试没有意义
            if 400 <= exc.code < 500:
                return b"", exc.code, f"HTTP {exc.code}"
            last_err = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return b"", 0, last_err or "unknown error"


def decode(raw: bytes) -> str:
    # utf-8-sig 放最前：带 BOM 的上游去掉 BOM 后不会把首行分组名污染成 "\ufeff央视频道"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
# iptv-org 把清晰度和状态塞进台名：`Bingtuan Satellite TV (540p) [Not 24/7]`。
# 一个台可能挂好几个括号，所以要反复剥到不动为止。
NAME_SUFFIX = re.compile(r"\s*(?:\(\d+[pi]\))(?:\[[^\]]*\])?\s*$|\s*\[[^\]]*\]\s*$")
GEO_BLOCKED = re.compile(r"\[Geo-blocked\]", re.I)


def clean_name(name: str) -> str:
    name = ZERO_WIDTH.sub("", name)
    name = NAME_CLEAN.sub(" ", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def clean_channel_name(name: str) -> str:
    """组名之外，频道名单独洗一遍：去掉 `(1080p)` `[Not 24/7]` 这类括号后缀。"""
    name = clean_name(name)
    prev = None
    while prev != name:
        prev = name
        name = NAME_SUFFIX.sub("", name).strip()
    return re.sub(r"\s{2,}", " ", name).strip() or "未命名"


def split_entry(line: str) -> tuple[str, str] | None:
    m = STREAM_AT.search(line)
    if not m or m.start() == 0:
        return None
    name = clean_name(line[: m.start()].rstrip(" ,，\t"))
    url = line[m.start():].strip().strip('"').strip()
    if not name or not url:
        return None
    return name, url


def parse_txt(text: str) -> tuple[list[dict], list[str]]:
    channels: list[dict] = []
    junk: list[str] = []
    group = "其他"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(GENRE_MARK):
            head = line[: -len(GENRE_MARK)].rstrip(" ,，").strip()
            group = clean_name(head) or "其他"
            continue
        if line.startswith("#"):
            continue  # 注释行
        pair = split_entry(line)
        if pair is None:
            junk.append(line)
            continue
        channels.append({"group": group, "name": pair[0], "url": pair[1]})
    return channels, junk


ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')


def parse_m3u(text: str) -> tuple[list[dict], list[str]]:
    channels: list[dict] = []
    junk: list[str] = []
    pending: tuple[str, str, str | None] | None = None  # (group, name, tvg_id)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR.findall(line))
            group = clean_name(attrs.get("group-title", "其他")) or "其他"
            named = line.rsplit(",", 1)[-1] if "," in line else ""
            name = clean_name(attrs.get("tvg-name") or named) or "未命名"
            # `tvg-id` 是节目单的挂载点：留着它，播放器才能按 id 精确对上 EPG，
            # 而不是靠台名猜。上游没填就是 None，输出时自然不写。
            tvg_id = clean_name(attrs.get("tvg-id", "")) or None
            pending = (group, name, tvg_id)
            continue
        if line.startswith("#"):
            continue
        url = line.strip()
        if pending is not None and STREAM_AT.match(url):
            channels.append(
                {"group": pending[0], "name": pending[1], "url": url, "tvg_id": pending[2]}
            )
        else:
            junk.append(line)
        pending = None
    return channels, junk


# 判断「这个台对中文电视列表有没有意义」：`Xizang TV Chinese` 留，`ABN China` 也留
# （有明确的中文标识），而 `8TV` `Ando TV` 这类纯英文名在中文列表里没法用。
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ZH_IDENTITY = re.compile(r"\b(?:Chinese|Mandarin|Cantonese|Hokkien|Hakka)\b|中文|华语|国语|粤语", re.I)


def has_chinese_identity(name: str) -> bool:
    return bool(CJK.search(name) or ZH_IDENTITY.search(name))


def merged_filter(cfg: dict, src: dict) -> dict:
    """源自己的 filter 覆盖全局 filter。

    过滤强度是**按源**的：iptv-org 那种全英文台名的源需要额外丢弃纯英文条目，
    但同一条规则套在其他源上会误杀 `HBO` `Discovery` 这类正常英文名频道。
    """
    f = dict(cfg.get("filter", {}))
    f.update(src.get("filter") or {})
    return f


def is_marker(name: str, f: dict) -> str | None:
    """返回过滤原因；None 表示留着。原因会原样写进 manifest，方便事后追溯。"""
    if f.get("drop_date_marker") and DATE_MARKER.match(name):
        return "日期标记行（上游的更新时间戳，不是频道）"
    if f.get("drop_bare_domain") and BARE_DOMAIN.match(name):
        return "域名标记行（上游的站点署名，不是频道）"
    if f.get("drop_geo_blocked") and GEO_BLOCKED.search(name):
        return "地区封锁标记（[Geo-blocked]），目标用户基本播不了"
    if f.get("drop_non_chinese_name") and not has_chinese_identity(name):
        return "台名既无中文也无 Chinese/Mandarin 等中文标识，中文列表里没法用"
    return None


def is_point_file(url: str, f: dict) -> bool:
    """单文件点播地址（历史春晚回放、短视频）不该混进直播列表。

    只认 `.mp4/.m4v` 这类点播容器。**故意不包含 `.flv`** —— HTTP-FLV 是
    地方台直播的常用承载（如 `channellive/gztv.flv`），按扩展名一刀切会把
    真直播误杀。
    """
    if not f.get("drop_mp4"):
        return False
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith((".mp4", ".m4v"))


# 内网/回环地址：`192.168.100.1:4022/udp/239.94.0.122:5140` 这种是**上传者自己家路由器**
# 上的 udpxy/msd_lite 端口。别人导进来必然连不上，只会给线路降级链添一条必然失败的线路。
LAN_HOST = re.compile(
    r"^(?:localhost|127\.|0\.0\.0\.0$|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", re.I
)


def is_lan_url(url: str, f: dict) -> bool:
    """地址指向内网时丢弃（按源开关，默认关）。"""
    if not f.get("drop_lan_host"):
        return False
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return bool(LAN_HOST.match(host))


# --------------------------------------------------------------------------- #
# 组名归一
# --------------------------------------------------------------------------- #
def canonical_group(name: str, cfg: dict) -> str:
    """把各上游自己的组名折成本仓统一的组名。

    三级判断，顺序不能换：
    1. `group_alias` 显式别名（`电影` → `电影频道`）
    2. 已经是 `group_order` 里的标准名（`央视频道` 原样留下）
    3. `group_rules.local_suffix` 兜底：以「频道」结尾却不是以上任何一种，
       判为省级/城市地方台（`浙江频道` `上海频道` → `地方频道`）。
       少了这一步，上游会把 28 个地名拆成 28 个分组。
    """
    name = clean_name(name) or "其他"
    # iptv-org 的 group-title 支持多值：`Animation;Kids`、`Education;Lifestyle;Science`。
    # 取第一个，否则十来种组合会各自变成独立分组。
    if ";" in name:
        name = name.split(";")[0].strip() or "其他"
    alias = cfg.get("group_alias", {})
    if name in alias:
        return alias[name]
    if name in cfg.get("group_order", []):
        return name
    for suffix in cfg.get("group_rules", {}).get("local_suffix", []):
        if len(name) > len(suffix) and name.endswith(suffix):
            return "地方频道"
    return name


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
# 上游有时把测速/分辨率作为附加列拼在地址后面：`...&authid=0,0.4,720x576`。
# 那不是 URL 的一部分，留着等于把一个带垃圾尾巴的地址交给播放器。
URL_TAIL_META = re.compile(r"(?:,\s*\d+(?:\.\d+)?)+(?:,\s*\d+\s*[xX]\s*\d+)?$")


def normalize_url(url: str) -> str:
    # 只做无损归一：去空白、去上游附加列。不补 scheme、不改大小写、不动查询串 ——
    # 这些参数（authid / key）恰恰是许多源能播的关键。
    url = url.strip().strip('"').strip()
    url = URL_TAIL_META.sub("", url)
    return url.rstrip(",， \t")


def aggregate(cfg: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    """按 `output` 字段把源分流成多条通道，返回 {通道名: 频道列表}。

    `output` 缺省即 `main`，进 dist/live.txt（主订阅）；写了别的名字就单独出路
    独立的 dist/<名字>.txt。之所以要分流，是因为有的上游根本不是电视台 ——
    CCSH 的 live_platforms.m3u 有 2600 多条「一起看」主播直播间，并进主订阅会
    把 600 多个电视频道直接淹掉。分开放，用户要就单独导入。
    """
    ua = cfg.get("ua", "Mozilla/5.0")
    timeout = int(cfg.get("timeout_seconds", 30))
    retries = int(cfg.get("retries", 2))

    streams: dict[str, list[dict]] = {}
    seen: dict[str, set[tuple[str, str, str]]] = {}
    report: list[dict] = []

    for src in cfg["sources"]:
        stream = src.get("output") or "main"
        bucket = streams.setdefault(stream, [])
        taken = seen.setdefault(stream, set())

        if not src.get("merge"):
            # 不参与频道合并的源（如加密的 MoonTV 配置）只做可达性体检
            raw, status, err = fetch(src["url"], ua, timeout, retries)
            report.append(
                {
                    "id": src["id"],
                    "name": src["name"],
                    "url": src["url"],
                    "type": src.get("type", "LIVE"),
                    "format": src.get("format", "txt"),
                    "http": status,
                    "bytes": len(raw),
                    "merged": False,
                    "stream": stream,
                    "accepted": 0,
                    "duplicated": 0,
                    "error": err,
                    "note": src.get("note"),
                    "sha256": hashlib.sha256(raw).hexdigest()[:16] if raw else None,
                }
            )
            continue

        raw, status, err = fetch(src["url"], ua, timeout, retries)
        entry = {
            "id": src["id"],
            "name": src["name"],
            "url": src["url"],
            "type": src.get("type", "LIVE"),
            "format": src.get("format", "txt"),
            "http": status,
            "bytes": len(raw),
            "merged": True,
            "stream": stream,
            "accepted": 0,
            "duplicated": 0,
            "filtered": [],
            "error": err,
        }

        if raw:
            text = decode(raw)
            fmt = src.get("format", "txt").lower()
            if fmt == "m3u" or text.lstrip().startswith("#EXTM3U"):
                channels, junk = parse_m3u(text)
            else:
                channels, junk = parse_txt(text)

            f = merged_filter(cfg, src)
            marker_junk: list[str] = []
            for ch in channels:
                reason = is_marker(ch["name"], f)
                if reason:
                    entry["filtered"].append({"name": ch["name"], "url": ch["url"], "reason": reason})
                    continue
                if is_point_file(ch["url"], f):
                    entry["filtered"].append(
                        {"name": ch["name"], "url": ch["url"], "reason": "单文件点播地址（.mp4 等），不是直播流"}
                    )
                    continue
                if is_lan_url(ch["url"], f):
                    entry["filtered"].append(
                        {"name": ch["name"], "url": ch["url"], "reason": "内网地址（udpxy 指向上传者自家路由器），别人连不上"}
                    )
                    continue
                ch["name"] = clean_channel_name(ch["name"])
                # 组名归一只作用在主订阅。独立通道保留上游原组名，
                # 否则「一起看」「原创IP」这类会被 local_suffix 规则误折进地方频道。
                # 例外：源自己声明了 `canonical_groups`（组名本来就是 CCTV/卫视/四川
                # 这类电视台分组，只是没写成本仓的统一名），就照样归一。
                if stream == "main" or src.get("canonical_groups"):
                    ch["group"] = canonical_group(ch["group"], cfg)
                ch["url"] = normalize_url(ch["url"])
                key = (ch["group"], ch["name"], ch["url"])
                if key in taken:
                    entry["duplicated"] += 1
                    continue
                taken.add(key)
                bucket.append({**ch, "source": src["id"]})
                entry["accepted"] += 1

            # 解析不出来的行也留档：格式变化时这里是第一现场
            for line in (junk + marker_junk)[:20]:
                entry["filtered"].append({"name": line[:80], "url": "", "reason": "无法解析为「名称,地址」"})

        report.append(entry)

    return streams, report


def order_groups(channels: list[dict], order: list[str]) -> list[str]:
    present: list[str] = []
    for ch in channels:
        if ch["group"] not in present:
            present.append(ch["group"])
    head = [g for g in order if g in present]
    tail = [g for g in present if g not in head]
    return head + tail


def build_txt(channels: list[dict], order: list[str]) -> str:
    lines: list[str] = []
    for group in order_groups(channels, order):
        lines.append(f"{group},{GENRE_MARK}")
        for ch in channels:
            if ch["group"] == group:
                lines.append(f"{ch['name']},{ch['url']}")
        lines.append("")
    # BOM：Windows 侧播放器（PotPlayer 等）靠它认 UTF-8
    return "\ufeff" + "\n".join(lines).rstrip() + "\n"


def build_m3u(channels: list[dict], order: list[str], epg_urls: list[str] | None = None) -> str:
    """M3U 输出。带 `x-tvg-url` 与 `tvg-id`，播放器才会自己去拉节目单。

    `x-tvg-url` 是 Kodi / TiviMate / APTV 都认的写法，写在 `#EXTM3U` 同一行。
    没有 `tvg-id` 的条目不给它编一个 —— 编出来的 id 一旦撞上别的台，节目单
    会张冠李戴，比「这个台没有节目单」更让人困惑。这类条目靠播放器按台名兜底匹配，
    而本仓台名（`CCTV1` `湖南卫视`）恰好就是上游 EPG 的 channel id。
    """
    head = "#EXTM3U"
    if epg_urls:
        head += f' x-tvg-url="{",".join(epg_urls)}"'
    out = [head]
    for group in order_groups(channels, order):
        for ch in channels:
            if ch["group"] != group:
                continue
            safe = ch["name"].replace('"', "'")
            attrs = ""
            tvg_id = (ch.get("tvg_id") or "").strip().replace('"', "'")
            if tvg_id:
                attrs += f' tvg-id="{tvg_id}"'
            out.append(f'#EXTINF:-1{attrs} tvg-name="{safe}" group-title="{group}",{ch["name"]}')
            out.append(ch["url"])
    return "\n".join(out) + "\n"


def build_sources_json(cfg: dict) -> str:
    """LeimaoTV「导入源」可直接吃的格式（SourceJson v1，SET-03）。

    这里放的是**上游原始地址**而非本仓聚合文件 —— 用户导入后三条源各自进列表、
    各自刷新，任一上游挂掉只影响它自己那一栏。

    `format` 字段是给客户端识别源格式的：moontv 加密订阅要标成 `moontv`，
    否则客户端会当成普通苹果CMS 接口去解析（拿一个 Base58 串当 JSON 解析必失败）。
    config 里写的是人类可读的 `moontv-encrypted`，这里转成客户端的枚举名 `moontv`。

    地址优先取 `client_url`：抓取在本仓 Actions 的海外机器上跑，直连 GitHub 最快；
    而这份文件是给**国内电视盒子**里的客户端导入的，raw.githubusercontent.com
    在那边基本连不上。两个场景最优解不同，所以分成两个字段，别混用。
    """
    items = []
    for src in cfg["sources"]:
        item = {
            "type": src.get("type", "LIVE"),
            "name": src["name"],
            "url": src.get("client_url") or src["url"],
            # enabled 跟 config 走：MoonTV 加密配置默认为 false
            "enabled": bool(src.get("enabled", True)),
        }
        fmt = normalize_format(src.get("format"))
        if fmt:
            item["format"] = fmt
        items.append(item)
    return json.dumps({"version": 1, "sources": items}, ensure_ascii=False, indent=2) + "\n"


def normalize_format(fmt: str | None) -> str | None:
    """把 config 里的格式名归一成客户端枚举名。直播源（txt/m3u）不标 format。"""
    if not fmt:
        return None
    f = fmt.lower()
    if f in ("moontv", "moontv-encrypted", "moontv_encrypted"):
        return "moontv"
    if f == "applecms":
        return "applecms"
    return None  # txt / m3u 等直播格式不需要客户端做格式区分


# --------------------------------------------------------------------------- #
# 节目单（EPG）体检
# --------------------------------------------------------------------------- #
CHANNEL_TAG = re.compile(r"<channel\b")
PROGRAMME_TAG = re.compile(r"<programme\b")


def probe_epg(epg: dict, ua: str, timeout: int, retries: int) -> dict:
    """抓一次节目单，报「能不能下、解开有多少台多少档」。

    只体检、不搬运：XMLTV 一天更新好几次、解压后几 MB 起步，每天提交一份
    几 MB 的 XML 会把仓库撑成垃圾桶。本仓交付的是**地址**，这里只负责证明
    那个地址还活着 —— 否则 README 里写着「有节目单」，实际 404 了也没人知道。
    """
    raw, status, err = fetch(epg["url"], ua, timeout, retries)
    entry = {
        "id": epg.get("id"),
        "name": epg.get("name"),
        "url": epg["url"],
        "http": status,
        "bytes": len(raw),
        "channels": 0,
        "programmes": 0,
        "error": err,
    }
    if not raw:
        return entry
    try:
        data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    except OSError as exc:
        entry["error"] = f"gzip 解压失败：{exc}"
        return entry
    text = decode(data)
    entry["channels"] = len(CHANNEL_TAG.findall(text))
    entry["programmes"] = len(PROGRAMME_TAG.findall(text))
    if entry["channels"] == 0:
        entry["error"] = "不是 XMLTV：解开后一个 <channel> 都没有"
    return entry


def write_if_changed(path: Path, content: str) -> dict:
    data = content.encode("utf-8")
    old = path.read_bytes() if path.exists() else None
    changed = old != data
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "changed": changed,
    }


def manifest_payload(channels: list[dict], counts: dict[str, int], order: list[str],
                     outputs: list[dict], report: list[dict], epg: list[dict]) -> dict:
    """manifest 里**不含时间**的部分。时间单独处理，否则每天都会因为时间戳产生提交。"""
    return {
        "total_channels": len(channels),
        "total_groups": len(counts),
        "groups": [{"name": g, "count": counts[g]} for g in order_groups(channels, order)],
        # changed 是本次运行的临时状态，不进产物：它会让指纹每次都不同
        "outputs": [{"path": o["path"], "bytes": o["bytes"], "sha256": o["sha256"]} for o in outputs],
        "upstreams": report,
        "epg": epg,
    }


# 节目单不进内容指纹：EPG 一天更新好几次，字节数天天变，算进去等于让本仓
# 每天都产出一笔「只改了节目单条数」的提交 —— 那正是 fingerprint 要防的事。
# 代价是「上游频道全没变、只有 EPG 挂了」这种情况不会触发提交，但 manifest
# 里的 epg 段会在下一次真实变化时刷新，体检目的（能看见挂没挂）已经达到。
FINGERPRINT_SKIP = ("epg",)


def fingerprint_of(payload: dict) -> str:
    stable = {k: v for k, v in payload.items() if k not in FINGERPRINT_SKIP}
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def read_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("fingerprint")
    except (ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="聚合多个上游源为 dist/ 订阅文件")
    parser.add_argument("--check", action="store_true", help="只抓取与体检，不写文件")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    streams, report = aggregate(cfg)
    channels = streams.get("main", [])
    order = cfg.get("group_order", [])
    epg_cfg = [e for e in cfg.get("epg", []) if e.get("enabled", True)]
    epg_urls = [e["url"] for e in epg_cfg]

    ua = cfg.get("ua", "Mozilla/5.0")
    timeout = int(cfg.get("timeout_seconds", 30))
    retries = int(cfg.get("retries", 2))
    epg = [probe_epg(e, ua, timeout, retries) for e in epg_cfg]

    counts: dict[str, int] = {}
    for ch in channels:
        counts[ch["group"]] = counts.get(ch["group"], 0) + 1

    generated = datetime.now(timezone.utc).astimezone()

    print(f"上游 {len(report)} 条，主订阅 {len(channels)} 个频道、{len(counts)} 个分组")
    for item in report:
        state = "OK " if not item["error"] else "ERR"
        stream = item.get("stream", "main")
        line = f"  [{state}] {item['id']:<15} http={item['http']:<3} bytes={item['bytes']:<7}"
        if item["merged"]:
            line += f" 收录={item['accepted']:<4} 重复={item['duplicated']:<3} 过滤={len(item.get('filtered', []))}"
        if item["error"]:
            line += f"  错误={item['error']}"
        if stream != "main":
            line += f"  → 独立通道 {stream}"
        print(line)

    for item in epg:
        state = "OK " if not item["error"] else "ERR"
        line = f"  [{state}] EPG {item['id']:<11} http={item['http']:<3} bytes={item['bytes']:<7}"
        line += f" 台={item['channels']:<4} 档={item['programmes']}"
        if item["error"]:
            line += f"  错误={item['error']}"
        print(line)

    # 独立通道各自出文件；空通道不落盘，免得上游空窗期留下 0 频道的死文件
    extra = {name: chans for name, chans in streams.items() if name != "main" and chans}

    if args.check:
        print("--check：不写文件。")
        return 0 if channels else 1

    outputs = []
    outputs.append(write_if_changed(DIST / "live.txt", build_txt(channels, order)))
    outputs.append(write_if_changed(DIST / "live.m3u", build_m3u(channels, order, epg_urls)))
    for name, chans in extra.items():
        outputs.append(write_if_changed(DIST / f"{name}.txt", build_txt(chans, order)))
        outputs.append(write_if_changed(DIST / f"{name}.m3u", build_m3u(chans, order, epg_urls)))
    outputs.append(write_if_changed(DIST / "sources.json", build_sources_json(cfg)))

    payload = manifest_payload(channels, counts, order, outputs, report, epg)
    fingerprint = fingerprint_of(payload)
    manifest_path = DIST / "manifest.json"

    if read_fingerprint(manifest_path) == fingerprint:
        # 内容一字未变：保留上次的 generated_at。
        # 否则每跑一次时间戳就变，Actions 会天天产出「只改了时间」的空提交。
        print(f"  未变 dist/manifest.json（内容指纹 {fingerprint} 与上次一致）")
    else:
        manifest = {
            "generated_at": generated.strftime("%Y-%m-%d %H:%M:%S %z"),
            "generated_at_iso": generated.isoformat(timespec="seconds"),
            "fingerprint": fingerprint,
            **payload,
        }
        outputs.append(
            write_if_changed(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        )

    for item in outputs:
        print(f"  {'写入' if item['changed'] else '未变'} {item['path']} ({item['bytes']} B)")
    return 0 if channels else 1


if __name__ == "__main__":
    sys.exit(main())
