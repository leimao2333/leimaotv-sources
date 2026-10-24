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
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.json"
DIST = ROOT / "dist"

GENRE_MARK = "#genre#"
# 判断「这一行是不是能拆出名称 + 地址」时，用 http 的位置而不是逗号：
# 名称里带逗号（`CCTV5+,高清`）、地址里带逗号都不至于拆错。
HTTP_AT = re.compile(r"https?://", re.IGNORECASE)
DATE_MARKER = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
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
def clean_name(name: str) -> str:
    name = ZERO_WIDTH.sub("", name)
    name = NAME_CLEAN.sub(" ", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def split_entry(line: str) -> tuple[str, str] | None:
    m = HTTP_AT.search(line)
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
    pending: tuple[str, str] | None = None  # (group, name)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR.findall(line))
            group = clean_name(attrs.get("group-title", "其他")) or "其他"
            named = line.rsplit(",", 1)[-1] if "," in line else ""
            name = clean_name(attrs.get("tvg-name") or named) or "未命名"
            pending = (group, name)
            continue
        if line.startswith("#"):
            continue
        url = line.strip()
        if pending is not None and HTTP_AT.match(url):
            channels.append({"group": pending[0], "name": pending[1], "url": url})
        else:
            junk.append(line)
        pending = None
    return channels, junk


def is_marker(name: str, cfg: dict) -> str | None:
    """返回过滤原因；None 表示留着。原因会原样写进 manifest，方便事后追溯。"""
    f = cfg.get("filter", {})
    if f.get("drop_date_marker") and DATE_MARKER.match(name):
        return "日期标记行（上游的更新时间戳，不是频道）"
    if f.get("drop_bare_domain") and BARE_DOMAIN.match(name):
        return "域名标记行（上游的站点署名，不是频道）"
    return None


def is_point_file(url: str, cfg: dict) -> bool:
    """单文件点播地址（历史春晚回放、短视频）不该混进直播列表。

    只认 `.mp4/.m4v` 这类点播容器。**故意不包含 `.flv`** —— HTTP-FLV 是
    地方台直播的常用承载（如 `channellive/gztv.flv`），按扩展名一刀切会把
    真直播误杀。
    """
    if not cfg.get("filter", {}).get("drop_mp4"):
        return False
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith((".mp4", ".m4v"))


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    # 只做无损归一：去空白。不补 scheme、不改大小写、不动查询串 ——
    # 这些参数（authid / key）恰恰是许多源能播的关键。
    return url.strip()


def aggregate(cfg: dict) -> tuple[dict, list[dict]]:
    ua = cfg.get("ua", "Mozilla/5.0")
    timeout = int(cfg.get("timeout_seconds", 30))
    retries = int(cfg.get("retries", 2))

    merged: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    report: list[dict] = []

    for src in cfg["sources"]:
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

            marker_junk: list[str] = []
            for ch in channels:
                reason = is_marker(ch["name"], cfg)
                if reason:
                    entry["filtered"].append({"name": ch["name"], "url": ch["url"], "reason": reason})
                    continue
                if is_point_file(ch["url"], cfg):
                    entry["filtered"].append(
                        {"name": ch["name"], "url": ch["url"], "reason": "单文件点播地址（.mp4 等），不是直播流"}
                    )
                    continue
                ch["url"] = normalize_url(ch["url"])
                key = (ch["group"], ch["name"], ch["url"])
                if key in seen:
                    entry["duplicated"] += 1
                    continue
                seen.add(key)
                merged.append({**ch, "source": src["id"]})
                entry["accepted"] += 1

            # 解析不出来的行也留档：格式变化时这里是第一现场
            for line in (junk + marker_junk)[:20]:
                entry["filtered"].append({"name": line[:80], "url": "", "reason": "无法解析为「名称,地址」"})

        report.append(entry)

    return {"channels": merged}, report


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


def build_m3u(channels: list[dict], order: list[str]) -> str:
    out = ["#EXTM3U"]
    for group in order_groups(channels, order):
        for ch in channels:
            if ch["group"] != group:
                continue
            safe = ch["name"].replace('"', "'")
            out.append(f'#EXTINF:-1 tvg-name="{safe}" group-title="{group}",{ch["name"]}')
            out.append(ch["url"])
    return "\n".join(out) + "\n"


def build_sources_json(cfg: dict) -> str:
    """LeimaoTV「导入源」可直接吃的格式（SourceJson v1，SET-03）。

    这里放的是**上游原始地址**而非本仓聚合文件 —— 用户导入后三条源各自进列表、
    各自刷新，任一上游挂掉只影响它自己那一栏。
    """
    items = []
    for src in cfg["sources"]:
        items.append(
            {
                "type": src.get("type", "LIVE"),
                "name": src["name"],
                "url": src["url"],
                # enabled 跟 config 走：MoonTV 加密配置默认为 false
                "enabled": bool(src.get("enabled", True)),
            }
        )
    return json.dumps({"version": 1, "sources": items}, ensure_ascii=False, indent=2) + "\n"


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


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="聚合多个上游源为 dist/ 订阅文件")
    parser.add_argument("--check", action="store_true", help="只抓取与体检，不写文件")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    merged, report = aggregate(cfg)
    channels = merged["channels"]
    order = cfg.get("group_order", [])

    counts: dict[str, int] = {}
    for ch in channels:
        counts[ch["group"]] = counts.get(ch["group"], 0) + 1

    generated = datetime.now(timezone.utc).astimezone()

    print(f"上游 {len(report)} 条，聚合出 {len(channels)} 个频道，{len(counts)} 个分组")
    for item in report:
        state = "OK " if not item["error"] else "ERR"
        line = f"  [{state}] {item['id']:<8} http={item['http']:<3} bytes={item['bytes']:<7}"
        if item["merged"]:
            line += f" 收录={item['accepted']:<4} 重复={item['duplicated']:<3} 过滤={len(item.get('filtered', []))}"
        if item["error"]:
            line += f"  错误={item['error']}"
        print(line)

    if args.check:
        print("--check：不写文件。")
        return 0 if channels else 1

    outputs = []
    outputs.append(write_if_changed(DIST / "live.txt", build_txt(channels, order)))
    outputs.append(write_if_changed(DIST / "live.m3u", build_m3u(channels, order)))
    outputs.append(write_if_changed(DIST / "sources.json", build_sources_json(cfg)))

    manifest = {
        "generated_at": generated.strftime("%Y-%m-%d %H:%M:%S %z"),
        "generated_at_iso": generated.isoformat(timespec="seconds"),
        "total_channels": len(channels),
        "total_groups": len(counts),
        "groups": [{"name": g, "count": counts[g]} for g in order_groups(channels, order)],
        "outputs": outputs,
        "upstreams": report,
    }
    outputs.append(write_if_changed(DIST / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"))

    for item in outputs:
        print(f"  {'写入' if item['changed'] else '未变'} {item['path']} ({item['bytes']} B)")
    return 0 if channels else 1


if __name__ == "__main__":
    sys.exit(main())
