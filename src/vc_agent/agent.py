import json
import os
import re
import ssl
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

from urllib import error, request

from .preferences import FEEDBACK_PATH, load_preferences, preference_multiplier

YOUTUBE_RSS_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
# 完整清单见 data/youtube_channels.json（60 频道）；可通过环境变量 YOUTUBE_CHANNELS_JSON 覆盖路径。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YOUTUBE_CHANNELS_JSON = PROJECT_ROOT / "data" / "youtube_channels.json"


def _resolve_llm_config() -> tuple[Optional[str], str, Optional[str]]:
    api_key = load_env("OPENAI_API_KEY")
    model = load_env("LLM_MODEL", "qwen-turbo")
    base_url = load_env("OPENAI_BASE_URL")
    return api_key, model, base_url

# TODO(Twitter/X): 恢复 v2 recent search 采集。需 Bearer + API Credits；易遇 SSL/402。README §3.1 曾说明。
# TODO(公众号): 恢复 WECHAT_RSS_URLS + RSSHub 等 Atom/RSS 拉取。README §3.2 曾说明。
# 当前仅启用下方 YouTube 频道 RSS，便于稳定演示与定时任务。

DEFAULT_TOPICS = {
    "AI": [
        "llm",
        "gpt",
        "diffusion",
        "transformer",
        "foundation model",
        "large language",
        "machine learning",
        "deep learning",
        "neural network",
        "computer vision",
        "speech recognition",
        "multimodal",
        "agentic",
        "机器学习",
        "人工智能",
        "神经网络",
    ],
    "芯片": [
        "semiconductor",
        "foundry",
        "fabrication",
        "wafer",
        "euv",
        "tsmc",
        "asic",
        "chiplet",
        "process node",
        "半导体",
        "芯片",
        "晶圆",
        "光刻",
        "封装测试",
    ],
    "机器人": [
        "robot",
        "robotics",
        "humanoid",
        "boston dynamics",
        "quadruped",
        "manipulator",
        "warehouse robot",
        "mobile robot",
        "industrial robot",
        "quadcopter",
        "delivery drone",
        "autonomous drone",
        "机械臂",
        "人形机器人",
        "协作机器人",
        "无人机",
    ],
}

# 白名单 YouTube 频道 → 简报固定分栏（通过噪音过滤后即可入池，无赛道关键词也归入该栏）。
# 由 load_youtube_channel_registry() 从 data/youtube_channels.json 填充；缺失文件时回退演示三频道。
CHANNEL_DEFAULT_TOPIC: Dict[str, str] = {}
CHANNEL_FEED_TITLE_HINT: Dict[str, str] = {}

# 冷启动演示（与 README 一致）；仅当 youtube_channels.json 不存在时使用。
_DEMO_CHANNEL_DEFAULT_TOPIC: Dict[str, str] = {
    "UCbfYPyITQ-7l4upoX8nvctg": "AI",
    "UCBHcMCGaiJhv-ESTcWGJPcw": "芯片",
    "UC7vVhkEfw4nOGp8TyDk7RcQ": "机器人",
}
_DEMO_CHANNEL_FEED_TITLE_HINT: Dict[str, str] = {
    "UCbfYPyITQ-7l4upoX8nvctg": "Two Minute Papers",
    "UCBHcMCGaiJhv-ESTcWGJPcw": "NVIDIA Developer",
    "UC7vVhkEfw4nOGp8TyDk7RcQ": "Boston Dynamics",
}


def load_youtube_channel_registry() -> List[str]:
    """从 JSON 加载 60 频道清单并写入 CHANNEL_DEFAULT_TOPIC / CHANNEL_FEED_TITLE_HINT；失败则回退演示三频道。"""
    global CHANNEL_DEFAULT_TOPIC, CHANNEL_FEED_TITLE_HINT
    path_str = load_env("YOUTUBE_CHANNELS_JSON")
    path = Path(path_str) if path_str else DEFAULT_YOUTUBE_CHANNELS_JSON
    if not path.is_file():
        CHANNEL_DEFAULT_TOPIC = dict(_DEMO_CHANNEL_DEFAULT_TOPIC)
        CHANNEL_FEED_TITLE_HINT = dict(_DEMO_CHANNEL_FEED_TITLE_HINT)
        print(f"[INFO] 未找到频道清单 {path}，使用演示频道 {len(CHANNEL_DEFAULT_TOPIC)} 个。")
        return list(CHANNEL_DEFAULT_TOPIC.keys())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        CHANNEL_DEFAULT_TOPIC = dict(_DEMO_CHANNEL_DEFAULT_TOPIC)
        CHANNEL_FEED_TITLE_HINT = dict(_DEMO_CHANNEL_FEED_TITLE_HINT)
        print(f"[WARN] 读取频道清单失败 ({exc})，回退演示频道。")
        return list(CHANNEL_DEFAULT_TOPIC.keys())
    rows = data.get("channels")
    if not isinstance(rows, list) or not rows:
        CHANNEL_DEFAULT_TOPIC = dict(_DEMO_CHANNEL_DEFAULT_TOPIC)
        CHANNEL_FEED_TITLE_HINT = dict(_DEMO_CHANNEL_FEED_TITLE_HINT)
        print("[WARN] youtube_channels.json 无有效 channels 数组，回退演示频道。")
        return list(CHANNEL_DEFAULT_TOPIC.keys())

    topic_map: Dict[str, str] = {}
    hint_map: Dict[str, str] = {}
    order: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("channel_id") or "").strip()
        dom = str(row.get("domain") or "").strip()
        name = str(row.get("name") or "").strip()
        if not cid or dom not in ("AI", "芯片", "机器人"):
            continue
        if cid not in topic_map:
            order.append(cid)
        topic_map[cid] = dom
        if name:
            hint_map[cid] = name
    if not order:
        CHANNEL_DEFAULT_TOPIC = dict(_DEMO_CHANNEL_DEFAULT_TOPIC)
        CHANNEL_FEED_TITLE_HINT = dict(_DEMO_CHANNEL_FEED_TITLE_HINT)
        print("[WARN] 频道清单解析后为空，回退演示频道。")
        return list(CHANNEL_DEFAULT_TOPIC.keys())

    CHANNEL_DEFAULT_TOPIC = topic_map
    CHANNEL_FEED_TITLE_HINT = hint_map
    by_dom: Dict[str, int] = {}
    for d in topic_map.values():
        by_dom[d] = by_dom.get(d, 0) + 1
    if not _env_quiet():
        print(f"[INFO] 已加载 YouTube 频道清单: {path}（共 {len(order)} 个；分栏计数 {by_dom}）")
    return order


NOISE_PATTERNS = [r"giveaway", r"sponsored", r"subscribe now", r"抽奖", r"广告", r"优惠码"]
SCORING_PROFILE_PATH = PROJECT_ROOT / "src" / "vc_agent" / "scoring_profile.json"
_SCORING_CACHE: Optional[Dict[str, Any]] = None


def load_scoring_profile() -> Dict[str, Any]:
    global _SCORING_CACHE
    if _SCORING_CACHE is not None:
        return _SCORING_CACHE
    default_profile: Dict[str, Any] = {
        "weights": {
            "topic_relevance": 0.5,
            "title_density": 0.2,
            "summary_density": 0.2,
            "freshness": 0.1,
            "business_signal": 0.0,
            "industry_position": 0.0,
            "risk_penalty": 0.0,
        },
        "signals": {
            "business_positive": [],
            "industry_position": [],
            "risk_negative": [],
        },
    }
    try:
        profile = json.loads(SCORING_PROFILE_PATH.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("invalid profile")
        weights = profile.get("weights") or {}
        signals = profile.get("signals") or {}
        if not isinstance(weights, dict) or not isinstance(signals, dict):
            raise ValueError("invalid sections")
        merged = dict(default_profile)
        merged["weights"] = {**default_profile["weights"], **weights}
        merged["signals"] = {**default_profile["signals"], **signals}
        _SCORING_CACHE = merged
        return merged
    except Exception:
        _SCORING_CACHE = default_profile
        return default_profile


def _kw_match(text_lower: str, kw: str) -> bool:
    """中英文关键词匹配：中文子串；含空格的英文短语子串；短英文 token 用边界避免误伤。"""
    raw = (kw or "").strip()
    if not raw:
        return False
    low = raw.lower()
    t = text_lower if text_lower.islower() else text_lower.lower()
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        return low in t
    if " " in low:
        return low in t
    if len(low) <= 6 and re.match(r"^[a-z0-9][a-z0-9+./-]*$", low):
        return re.search(rf"(?<![a-z0-9]){re.escape(low)}(?![a-z0-9])", t) is not None
    return low in t


def _topic_keyword_hits(text: str) -> Dict[str, int]:
    t = (text or "").lower()
    out: Dict[str, int] = {}
    for topic, keywords in DEFAULT_TOPICS.items():
        out[topic] = sum(1 for kw in keywords if _kw_match(t, kw))
    return out


@dataclass
class RawItem:
    source: str
    title: str
    author: str
    published: str
    link: str
    summary: str
    channel_id: Optional[str] = None  # YouTube 频道，用于分栏路由


@dataclass
class ScoredItem:
    raw: RawItem
    topic: str
    score: float
    reason: str


@dataclass
class BriefRow:
    """简报展示行：可合并多条来源为一条。"""
    topic: str
    links: List[str]
    scored_items: List[ScoredItem]
    merged_summary: Dict[str, Any]


def load_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        return None
    return value.strip()


def _env_quiet() -> bool:
    """VC_AGENT_QUIET=1 时精简终端输出（run.sh 默认开启）。"""
    v = (os.getenv("VC_AGENT_QUIET") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


_RSS_QUIET_MISMATCH = 0
_RSS_QUIET_FETCH_FAIL = 0


def _rss_quiet_reset() -> None:
    global _RSS_QUIET_MISMATCH, _RSS_QUIET_FETCH_FAIL
    _RSS_QUIET_MISMATCH = 0
    _RSS_QUIET_FETCH_FAIL = 0


def allow_insecure_ssl() -> bool:
    val = (load_env("ALLOW_INSECURE_SSL", "false") or "false").lower()
    return val in {"1", "true", "yes", "on"}


def build_ssl_context() -> Optional[ssl.SSLContext]:
    if allow_insecure_ssl():
        return ssl._create_unverified_context()
    return None


def _strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = unescape(re.sub(r"\s+", " ", t).strip())
    return t


def _pubdate_to_iso(pub: str) -> str:
    pub = (pub or "").strip()
    if not pub:
        return ""
    try:
        return parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return pub


def parse_atom_feed(xml_text: str, source: str) -> List[RawItem]:
    """解析 Atom（YouTube、多数 RSSHub Atom 输出）。"""
    root = ET.fromstring(xml_text)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    items: List[RawItem] = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        author = (
            entry.find("atom:author/atom:name", ns).text.strip()
            if entry.find("atom:author/atom:name", ns) is not None
            else "unknown"
        )
        published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
        if not published:
            published = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
        link_node = entry.find("atom:link", ns)
        link = link_node.attrib.get("href", "").strip() if link_node is not None else ""
        summary = (entry.findtext("media:group/media:description", default="", namespaces=ns) or "").strip()
        if not summary:
            summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        if not summary:
            summary = (entry.findtext("atom:content", default="", namespaces=ns) or "").strip()
        summary = _strip_html(summary)

        if not title or not link:
            continue
        items.append(
            RawItem(source=source, title=title, author=author, published=published, link=link, summary=summary)
        )
    return items


def parse_rss2_channel(xml_text: str, source: str) -> List[RawItem]:
    """解析 RSS 2.0（常见公众号聚合、部分 RSSHub 路由）。"""
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    dc_ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    content_tag = "{http://purl.org/rss/1.0/modules/content/}encoded"
    items: List[RawItem] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        title = _strip_html(title)
        link = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()
        if not summary:
            enc = item.find(content_tag)
            if enc is not None and enc.text:
                summary = enc.text
        summary = _strip_html(summary)
        pub = (item.findtext("pubDate") or "").strip()
        published = _pubdate_to_iso(pub) if pub else ""
        author_el = item.find("dc:creator", dc_ns)
        author = (author_el.text.strip() if author_el is not None and author_el.text else "") or (
            item.findtext("author") or ""
        ).strip()
        if not author:
            author = channel.findtext("title") or "公众号"

        if not title or not link:
            continue
        items.append(
            RawItem(source=source, title=title, author=author, published=published, link=link, summary=summary)
        )
    return items


def parse_any_feed(xml_text: str, source: str) -> List[RawItem]:
    """先尝试 Atom，若无条目再尝试 RSS 2.0。"""
    atom_items = parse_atom_feed(xml_text, source)
    if atom_items:
        return atom_items
    return parse_rss2_channel(xml_text, source)


def fetch_url(url: str, timeout: int = 20) -> str:
    req = request.Request(url, headers={"User-Agent": "vc-agent/1.0 (+https://github.com)"})
    with request.urlopen(req, timeout=timeout, context=build_ssl_context()) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _atom_feed_title(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        el = root.find("atom:title", ns)
        return (el.text or "").strip() if el is not None else ""
    except ET.ParseError:
        return ""


def fetch_youtube_channel_rss(channel_id: str, max_items: int = 30) -> List[RawItem]:
    global _RSS_QUIET_MISMATCH, _RSS_QUIET_FETCH_FAIL
    url = YOUTUBE_RSS_TEMPLATE.format(channel_id=quote_plus(channel_id))
    try:
        body = fetch_url(url, timeout=15)
        ft = _atom_feed_title(body)
        hint = CHANNEL_FEED_TITLE_HINT.get(channel_id)
        if hint and ft and hint.lower() not in ft.lower() and ft.lower() not in hint.lower():
            if _env_quiet():
                _RSS_QUIET_MISMATCH += 1
            else:
                print(
                    f"[WARN] channel_id={channel_id} RSS 顶层标题={ft!r} 与期望「{hint}」明显不符，"
                    "请核对频道 ID（错误 ID 会把无关视频整栏写进芯片/机器人）。"
                )
        items = parse_any_feed(body, source="YouTube")
        return [replace(it, channel_id=channel_id) for it in items[:max_items]]
    except Exception as exc:
        if _env_quiet():
            _RSS_QUIET_FETCH_FAIL += 1
        else:
            print(f"[WARN] 拉取频道 RSS 失败 channel_id={channel_id}: {exc}")
        return []


def classify_and_score(item: RawItem, prefs: Optional[Dict[str, Any]] = None) -> Optional[ScoredItem]:
    text = f"{item.title} {item.summary}".lower()
    profile = load_scoring_profile()
    weights = profile.get("weights") or {}
    signals = profile.get("signals") or {}

    for p in NOISE_PATTERNS:
        if re.search(p, text):
            return None

    topic_hits = _topic_keyword_hits(text)
    best_any = max(topic_hits.values())

    cid = (item.channel_id or "").strip()
    forced = CHANNEL_DEFAULT_TOPIC.get(cid)

    if forced is not None:
        # 演示三频道：不依赖关键词也固定进对应栏，避免过滤过严后整栏为空
        th = dict(topic_hits)
        if th.get(forced, 0) < 1:
            th[forced] = 1
        topic_hits = th
        topic = forced
        rel = max(topic_hits.get(forced, 0), 1)
    else:
        if best_any == 0:
            return None
        topic = max(topic_hits, key=topic_hits.get)
        rel = topic_hits[topic]

    title_len_score = min(len(item.title) / 60.0, 1.0)
    summary_len_score = min(len(item.summary) / 240.0, 1.0)
    freshness_score = 1.0
    if item.published:
        try:
            dt = datetime.fromisoformat(item.published.replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            freshness_score = max(0.2, 1 - min(hours, 96) / 96)
        except Exception:
            pass

    biz_kw = [str(x).strip() for x in (signals.get("business_positive") or []) if str(x).strip()]
    ind_kw = [str(x).strip() for x in (signals.get("industry_position") or []) if str(x).strip()]
    risk_kw = [str(x).strip() for x in (signals.get("risk_negative") or []) if str(x).strip()]
    biz_hits = sum(1 for kw in biz_kw if _kw_match(text, kw))
    ind_hits = sum(1 for kw in ind_kw if _kw_match(text, kw))
    risk_hits = sum(1 for kw in risk_kw if _kw_match(text, kw))
    biz_score = min(biz_hits / 2.0, 1.0) if biz_kw else 0.0
    ind_score = min(ind_hits / 2.0, 1.0) if ind_kw else 0.0
    risk_penalty = min(risk_hits / 2.0, 1.0) if risk_kw else 0.0

    w_rel = float(weights.get("topic_relevance", 0.5))
    w_title = float(weights.get("title_density", 0.2))
    w_sum = float(weights.get("summary_density", 0.2))
    w_fresh = float(weights.get("freshness", 0.1))
    w_biz = float(weights.get("business_signal", 0.0))
    w_ind = float(weights.get("industry_position", 0.0))
    w_risk = float(weights.get("risk_penalty", 0.0))

    base = (
        w_rel * min(rel / 3.0, 1.0)
        + w_title * title_len_score
        + w_sum * summary_len_score
        + w_fresh * freshness_score
        + w_biz * biz_score
        + w_ind * ind_score
        - w_risk * risk_penalty
    )
    base = max(0.0, min(base, 1.5))
    prefs = prefs or load_preferences()
    mult = preference_multiplier(item.source, item.author, item.link, prefs)
    score = round(base * mult, 4)
    route = f"ch={cid}" if cid else "kw"
    reason = (
        f"topic={topic}, hit={rel}, {route}, len={len(item.title)}/{len(item.summary)}, "
        f"biz={biz_hits}, industry={ind_hits}, risk={risk_hits}, "
        f"w=({w_rel:.2f},{w_title:.2f},{w_sum:.2f},{w_fresh:.2f},{w_biz:.2f},{w_ind:.2f},{w_risk:.2f}), "
        f"pref×{mult}"
    )
    return ScoredItem(raw=item, topic=topic, score=score, reason=reason)


def select_for_brief(scored: List[ScoredItem], *, per_topic: int = 5) -> List[ScoredItem]:
    """每栏取分数最高的至多 per_topic 条；若某栏关键词池不足，用 CHANNEL_DEFAULT_TOPIC 白名单频道条目回填该栏。"""
    if not scored:
        return []
    topics_order = ["AI", "芯片", "机器人"]
    by_topic: Dict[str, List[ScoredItem]] = {t: [] for t in topics_order}
    for it in scored:
        if it.topic in by_topic:
            by_topic[it.topic].append(it)
    for t in topics_order:
        by_topic[t].sort(key=lambda x: -x.score)

    picked: List[ScoredItem] = []
    seen: set[str] = set()
    for t in topics_order:
        for it in by_topic[t][:per_topic]:
            if it.raw.link in seen:
                continue
            picked.append(it)
            seen.add(it.raw.link)

    def _count(topic: str) -> int:
        return sum(1 for x in picked if x.topic == topic)

    # 白名单频道：该栏未满时，从未入选的高分条目中按「频道默认栏」划入（避免关键词收紧后整栏为空）
    by_score = sorted(scored, key=lambda x: -x.score)
    for t in topics_order:
        need = per_topic - _count(t)
        while need > 0:
            moved = False
            for it in by_score:
                cid = (it.raw.channel_id or "").strip()
                if it.raw.link in seen:
                    continue
                if CHANNEL_DEFAULT_TOPIC.get(cid) != t:
                    continue
                picked.append(
                    replace(
                        it,
                        topic=t,
                        reason=it.reason + f",栏位回填(channel→{t})",
                    )
                )
                seen.add(it.raw.link)
                need -= 1
                moved = True
                break
            if not moved:
                break

    short: Dict[str, int] = {}
    for t in topics_order:
        got = _count(t)
        if got < per_topic:
            short[t] = per_topic - got
    if short:
        hint = "；".join(f"{t}栏尚缺{n}条（关键词池或白名单频道可用条目不足）" for t, n in short.items())
        if not _env_quiet():
            print(f"[WARN] 未满每栏 {per_topic} 条: {hint}。可增加 RSS 频道或略放宽关键词。")

    by_final: Dict[str, List[ScoredItem]] = {t: [] for t in topics_order}
    for it in picked:
        if it.topic in by_final:
            by_final[it.topic].append(it)
    for t in topics_order:
        by_final[t].sort(key=lambda x: -x.score)
    out: List[ScoredItem] = []
    for t in topics_order:
        out.extend(by_final[t])
    return out


def deduplicate(items: List[ScoredItem]) -> List[ScoredItem]:
    seen = set()
    output = []
    for i in sorted(items, key=lambda x: x.score, reverse=True):
        key = re.sub(r"\W+", "", i.raw.title.lower())
        if key in seen:
            continue
        seen.add(key)
        output.append(i)
    return output


def _tier_from_signal_line(line: str) -> str:
    """从单条 investment_signal 文本解析等级（兼容历史「待观察」）。"""
    s = (line or "").strip()
    head = s[:24]
    if "「风险」" in head or head.startswith("风险") or (head.startswith("「") and "风险" in head[:6]):
        return "风险"
    if "「利好」" in head or head.startswith("利好") or (head.startswith("「") and "利好" in head[:6]):
        return "利好"
    if "中性偏弱" in head or "「中性偏弱」" in head:
        return "中性偏弱"
    if "建议跟踪" in head or "「建议跟踪」" in head:
        return "建议跟踪"
    if "待观察" in s:
        return "建议跟踪"
    return "建议跟踪"


def merge_signal_tiers(tiers: List[str]) -> str:
    """合并多条来源的投资信号等级：风险优先揭示；否则取利好 > 建议跟踪 > 中性偏弱。"""
    if any(t == "风险" for t in tiers):
        return "风险"
    if any(t == "利好" for t in tiers):
        return "利好"
    if any(t == "建议跟踪" for t in tiers):
        return "建议跟踪"
    return "中性偏弱"


def _fallback_merge_groups(pack: List[tuple[ScoredItem, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """LLM 失败时每条单独成组。"""
    return [{"indices": [i]} for i in range(len(pack))]


def llm_merge_topic_cluster(
    topic: str,
    pack: List[tuple[ScoredItem, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    将同一赛道下多条摘要合并为若干组（同公司或同核心技术主题）。
    返回 [{"indices": [...], "merged_summary": dict}, ...]
    """
    n = len(pack)
    if n <= 0:
        return []
    if n == 1:
        sm = dict(pack[0][1])
        sm["meta_source_count"] = 1
        return [{"indices": [0], "merged_summary": sm}]

    api_key, model, base_url = _resolve_llm_config()

    lines: List[str] = []
    for i, (si, sm) in enumerate(pack):
        sig0 = (sm.get("investment_signal") or [""])[0]
        subj = str(sm.get("subject") or "").strip()
        tz = str(sm.get("title_zh") or "").strip()
        ol = str(sm.get("one_line") or "").strip()
        lines.append(
            f"[{i}] 主体={subj} | 标题={tz} | 结论={ol[:80]} | 信号={sig0[:72]} | 链接={si.raw.link}"
        )
    bundle = "\n".join(lines)

    if not api_key or not base_url:
        out_sm: List[Dict[str, Any]] = []
        for g in _fallback_merge_groups(pack):
            merged = _merge_group_dict_from_indices(g["indices"], pack, topic, merge_signal_logic="（离线合并）")
            out_sm.append({"indices": g["indices"], "merged_summary": merged})
        return out_sm

    prompt = textwrap.dedent(
        f"""
        你是 VC 投研简报编辑。下列为「{topic}」赛道的 {n} 条候选摘要（索引 0～{n - 1}）。
        若若干条指向**同一公司/机构**或**同一核心技术主题**（例如均围绕 ASML High NA EUV），请合并为一组；否则每条单独成组。
        输出**严格 JSON**，勿 markdown。字段说明：
        - groups: 数组；须**划分完**全部索引 0～{n - 1}，每组 indices 升序、互不重复。
        - 每组含：
          - title_zh: 14～22 字中文标题，概括合并后主题
          - body: 60～120 字，提炼共性技术点或市场影响；若组内多于 1 条，文中必须含「综合自 X 篇相关报道」（X=该组 indices 长度）
          - key_points: 长度恰好 2 的数组，每条不超过 28 字
          - merged_signal_logic: 一句话（不超过 60 字），说明合并后的投资逻辑：为何是利好/风险/为何建议跟踪或中性偏弱（勿使用「待观察」字样）
          - merge_reason: 不超过 40 字，说明**为何合并**（如：均讨论同一产品线）

        **严禁**出现：简报编号、Python 文件名、终端命令、「需本机运行」、任何开发操作提示。

        材料：
        {bundle}
        """
    ).strip()

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "只输出合法 JSON，groups 覆盖全部索引且无重复。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    url = f"{base_url.rstrip('/')}/chat/completions"

    try:
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=55, context=build_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        content = json.loads(body)["choices"][0]["message"]["content"]
        data = json.loads(content)
        groups = data.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("empty groups")
        validated = _validate_merge_groups(groups, n)
        if validated is None:
            raise ValueError("invalid partition")
        out: List[Dict[str, Any]] = []
        for g in validated:
            idxs = [int(x) for x in g["indices"]]
            if len(idxs) == 1:
                sm = dict(pack[idxs[0]][1])
                sm["meta_source_count"] = 1
                out.append({"indices": idxs, "merged_summary": sm})
                continue
            merged = _merge_group_dict_from_llm(g, idxs, pack, topic)
            out.append({"indices": idxs, "merged_summary": merged})
        return out
    except Exception as exc:
        if not _env_quiet():
            print(f"[WARN] 同栏合并 LLM 失败，改为逐条展示: {exc}")
        out_sm = []
        for g in _fallback_merge_groups(pack):
            merged = _merge_group_dict_from_indices(
                g["indices"], pack, topic, merge_signal_logic="（合并步骤降级，按单条展示）"
            )
            out_sm.append({"indices": g["indices"], "merged_summary": merged})
        return out_sm


def _validate_merge_groups(groups: List[Any], n: int) -> Optional[List[Dict[str, Any]]]:
    if n <= 0:
        return []
    used: set[int] = set()
    cleaned: List[Dict[str, Any]] = []
    for g in groups:
        if not isinstance(g, dict):
            return None
        idxs = g.get("indices")
        if not isinstance(idxs, list):
            return None
        for x in idxs:
            if not isinstance(x, int) or x < 0 or x >= n:
                return None
            if x in used:
                return None
            used.add(x)
        cleaned.append(g)
    if used != set(range(n)):
        return None
    return cleaned


def _merge_group_dict_from_llm(
    g: Dict[str, Any],
    idxs: List[int],
    pack: List[tuple[ScoredItem, Dict[str, Any]]],
    topic: str,
) -> Dict[str, Any]:
    tiers = [
        _tier_from_signal_line((pack[i][1].get("investment_signal") or [""])[0]) for i in idxs
    ]
    tier = merge_signal_tiers(tiers)
    logic = str(g.get("merged_signal_logic") or "").strip()
    mreason = str(g.get("merge_reason") or "").strip()
    if len(idxs) > 1 and mreason:
        logic = f"{logic}（合并说明：{mreason}）" if logic else f"合并说明：{mreason}"
    title_zh = str(g.get("title_zh") or "").strip()
    body = str(g.get("body") or "").strip()
    kp = g.get("key_points") or []
    if not isinstance(kp, list):
        kp = []
    kp = [str(x).strip() for x in kp[:2] if str(x).strip()]
    while len(kp) < 2:
        kp.append("可对照原文核实数据与口径")
    sig_line = f"「{tier}」逻辑：{logic[:120]}" if logic else f"「{tier}」逻辑：合并组内信号取等级为「{tier}」。"
    return {
        "title_zh": title_zh[:44] or "合并条目",
        "subject": str(pack[idxs[0]][1].get("subject") or "")[:60],
        "why_matters": body[:200] if body else topic + " 赛道相关动态。",
        "one_line": body[:100] if body else (pack[idxs[0]][1].get("one_line") or "")[:80],
        "key_points": [kp[0][:40], kp[1][:40]],
        "investment_signal": [sig_line[:200]],
        "meta_source_count": len(idxs),
    }


def _merge_group_dict_from_indices(
    idxs: List[int],
    pack: List[tuple[ScoredItem, Dict[str, Any]]],
    topic: str,
    *,
    merge_signal_logic: str,
) -> Dict[str, Any]:
    if len(idxs) == 1:
        sm = dict(pack[idxs[0]][1])
        sm["meta_source_count"] = 1
        return sm
    tiers = [_tier_from_signal_line((pack[i][1].get("investment_signal") or [""])[0]) for i in idxs]
    tier = merge_signal_tiers(tiers)
    parts = [str(pack[i][1].get("one_line") or "").strip() for i in idxs]
    body = " ".join(x for x in parts if x)[:200]
    if len(idxs) > 1:
        body = f"综合自 {len(idxs)} 篇相关报道。" + (body if body else "")
    tz = str(pack[idxs[0]][1].get("title_zh") or "").strip()
    kp1 = str((pack[idxs[0]][1].get("key_points") or [""])[0] if pack[idxs[0]][1].get("key_points") else "")[:28]
    kp2 = str((pack[idxs[1]][1].get("key_points") or [""])[0] if len(idxs) > 1 and pack[idxs[1]][1].get("key_points") else "")[:28]
    kps = [kp1 or "对照组内原文交叉验证", kp2 or "关注后续财报与订单"]
    sig_line = f"「{tier}」逻辑：{merge_signal_logic}"
    return {
        "title_zh": tz[:44] or "合并条目",
        "subject": str(pack[idxs[0]][1].get("subject") or "")[:60],
        "why_matters": body[:200],
        "one_line": body[:100],
        "key_points": [kps[0][:40], kps[1][:40]],
        "investment_signal": [sig_line[:200]],
        "meta_source_count": len(idxs),
    }


def build_merged_brief_rows(
    scored: List[ScoredItem],
    summaries: Dict[str, Dict[str, Any]],
) -> List[BriefRow]:
    """按赛道分组并调用 LLM 合并同主题/同主体条目。"""
    topics_order = ["AI", "芯片", "机器人"]
    by_topic: Dict[str, List[ScoredItem]] = {t: [] for t in topics_order}
    for s in scored:
        if s.topic in by_topic:
            by_topic[s.topic].append(s)
    for t in topics_order:
        by_topic[t].sort(key=lambda x: -x.score)

    rows: List[BriefRow] = []
    for topic in topics_order:
        items = by_topic[topic]
        if not items:
            continue
        pack = [(s, summaries.get(s.raw.link) or {}) for s in items]
        groups = llm_merge_topic_cluster(topic, pack)
        scored_groups: List[tuple[float, BriefRow]] = []
        for g in groups:
            idxs = g["indices"]
            merged = g["merged_summary"]
            sis = [pack[i][0] for i in idxs]
            sis_sorted = sorted(sis, key=lambda x: -x.score)
            links = [x.raw.link for x in sis_sorted]
            mx = max(si.score for si in sis) if sis else 0.0
            scored_groups.append((mx, BriefRow(topic=topic, links=links, scored_items=sis_sorted, merged_summary=merged)))
        scored_groups.sort(key=lambda x: -x[0])
        rows.extend(br for _, br in scored_groups)
    return rows


def _short_zh_title(raw_title: str, max_len: int = 22) -> str:
    t = (raw_title or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _finalize_summary_dict(data: Dict[str, Any], item: ScoredItem) -> Dict[str, Any]:
    """统一字段，便于简报模板与降级路径共用。"""
    kp = data.get("key_points") or []
    if not isinstance(kp, list):
        kp = []
    kp = [str(x).strip() for x in kp[:2] if str(x).strip()]
    sig = data.get("investment_signal") or []
    if not isinstance(sig, list):
        sig = []
    sig = [str(x).strip() for x in sig[:1] if str(x).strip()]
    subject = str(data.get("subject") or "").strip() or "无明确主体"
    title_zh = str(data.get("title_zh") or "").strip() or _short_zh_title(item.raw.title)
    why = str(data.get("why_matters") or "").strip() or "待结合原文判断与细分赛道的关联。"
    one_line = str(data.get("one_line") or "").strip() or f"{item.topic} 相关动态，建议查看摘要与原文。"
    out = {
        "title_zh": title_zh[:40],
        "subject": subject[:60],
        "why_matters": why[:80],
        "one_line": one_line[:80],
        "key_points": [x[:40] for x in kp[:2]],
        "investment_signal": [x[:200] for x in sig[:1]],
        "meta_source_count": int(data.get("meta_source_count") or 1),
    }
    return out


def llm_summarize(item: ScoredItem) -> Dict[str, Any]:
    api_key, model, base_url = _resolve_llm_config()

    prompt = textwrap.dedent(
        f"""
        你是面向 VC 合伙人的投研简报助手。根据下列**英文标题+简介**写**中文**结构化摘要，输出**严格 JSON**，不要 markdown、不要解释。
        受众用手机扫读，句子要短、信息密度高。

        JSON 字段（全部为字符串或字符串数组，勿嵌套对象）：
        - title_zh: 14-22 个汉字以内的**中文短标题**，概括核心话题，勿照搬英文长句
        - subject: 核心主体：具体公司/机构/产品/项目名；若无法从文本判断则必须写「无明确主体」
        - why_matters: 28-40 个汉字，回答「和 {item.topic} 赛道投融资/产业格局**为何可能相关**」；若仅为通识科普则写明「偏通识，弱投融资信号」
        - one_line: 18-32 个汉字，**投资视角**一句话结论（谁/发生什么/影响是什么）
        - key_points: 长度恰好为 2 的数组，每条不超过 28 个汉字，可执行观察或验证点
        - investment_signal: 长度 1 的数组。单条格式必须为「「等级」逻辑：原因」：
          等级只能是「利好」「风险」「建议跟踪」「中性偏弱」之一（**禁止**使用「待观察」字样）。
          「逻辑：」后附 **一句话**（不超过 45 字），说明为何如此判定（例如：偏管理理念、缺乏财务数据等）。

        **严禁**在输出中出现：简报编号、任何 .py 文件名、shell/终端命令、「需本机运行」等面向开发者的文字。

        输入：
        赛道标签: {item.topic}
        来源: {item.raw.source}
        标题: {item.raw.title}
        作者: {item.raw.author}
        时间: {item.raw.published}
        简介: {item.raw.summary[:1200]}
        """
    ).strip()

    if not api_key or not base_url:
        return _finalize_summary_dict(
            {
                "title_zh": _short_zh_title(item.raw.title),
                "subject": "无明确主体",
                "why_matters": "无 API Key，仅依据标题与简介占位。",
                "one_line": f"{item.topic} 相关公开内容更新。",
                "key_points": ["建议打开原文核对事实与语境", "关注是否出现可验证商业数据"],
                "investment_signal": ["「建议跟踪」逻辑：摘要未由模型生成，请阅读原文再评估。"],
                "meta_source_count": 1,
            },
            item,
        )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是严谨的中文科技投研助手，输出合法 JSON，字段齐全，勿编造具体融资额/估值。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    url = f"{base_url.rstrip('/')}/chat/completions"

    try:
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=40, context=build_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        content = json.loads(body)["choices"][0]["message"]["content"]
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("LLM 返回非对象")
        return _finalize_summary_dict(dict(data), item)
    except (error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
        if not _env_quiet():
            print(f"[WARN] LLM 摘要失败，使用降级摘要: {exc}")
        return _finalize_summary_dict(
            {
                "title_zh": _short_zh_title(item.raw.title),
                "subject": "无明确主体",
                "why_matters": "摘要生成失败，请直接阅读原文判断相关性。",
                "one_line": f"{item.topic} 条目：{item.raw.title[:28]}",
                "key_points": ["本条未完成 LLM 结构化摘要", "以原文与频道背景为准"],
                "investment_signal": ["「建议跟踪」逻辑：摘要生成失败，结论以原文为准。"],
                "meta_source_count": 1,
            },
            item,
        )


def _merge_content_summary(sm: Dict[str, Any]) -> str:
    parts: List[str] = []
    n = int(sm.get("meta_source_count") or 1)
    ol0 = (sm.get("one_line") or "").strip()
    wm0 = (sm.get("why_matters") or "").strip()
    if n > 1 and "综合自" not in (ol0 + wm0) and "篇相关报道" not in (ol0 + wm0):
        parts.append(f"综合自 {n} 篇相关报道")
    ol = (sm.get("one_line") or "").strip()
    if ol:
        parts.append(ol)
    wm = (sm.get("why_matters") or "").strip()
    if wm:
        parts.append(wm)
    for k in sm.get("key_points") or []:
        k = str(k).strip()
        if k:
            parts.append(k)
    if not parts:
        return "（暂无内容总结）"
    text = " ".join(parts)
    if not text.endswith(("。", "！", "？", ".")):
        text += "。"
    return text


def _topic_section_heading(topic: str) -> str:
    if topic == "AI":
        return "## 🤖 AI 领域"
    if topic == "芯片":
        return "## ⚡ 芯片"
    return "## 🦾 机器人"


def _brief_max_per_topic() -> int:
    try:
        max_per = int(load_env("BRIEF_MAX_PER_TOPIC", "5") or "5")
    except ValueError:
        max_per = 5
    return max(1, min(max_per, 12))


def _brief_sections(
    scored: List[ScoredItem],
    max_per: int,
) -> List[tuple[str, List[ScoredItem], List[ScoredItem]]]:
    """返回 (topic, 该栏全部, 该栏展示切片) 列表。"""
    topics_order = ["AI", "芯片", "机器人"]
    grouped: Dict[str, List[ScoredItem]] = {t: [] for t in topics_order}
    for s in scored:
        if s.topic in grouped:
            grouped[s.topic].append(s)
    for t in topics_order:
        grouped[t].sort(key=lambda x: -x.score)
    out: List[tuple[str, List[ScoredItem], List[ScoredItem]]] = []
    for topic in topics_order:
        full = grouped[topic]
        out.append((topic, full, full[:max_per]))
    return out


def _brief_rows_sections(
    rows: List[BriefRow],
    max_per: int,
) -> List[tuple[str, List[BriefRow], List[BriefRow]]]:
    """合并后的简报行按栏切片。"""
    topics_order = ["AI", "芯片", "机器人"]
    grouped: Dict[str, List[BriefRow]] = {t: [] for t in topics_order}
    for r in rows:
        if r.topic in grouped:
            grouped[r.topic].append(r)
    out: List[tuple[str, List[BriefRow], List[BriefRow]]] = []
    for topic in topics_order:
        full = grouped[topic]
        out.append((topic, full, full[:max_per]))
    return out


def _row_source_label(row: BriefRow) -> str:
    if len(row.scored_items) == 1:
        return (row.scored_items[0].raw.source or "").strip()
    src0 = (row.scored_items[0].raw.source or "").strip()
    return f"{src0} 等 · {len(row.links)} 篇"


def build_brief_payload(
    brief_rows: List[BriefRow],
    brief_id: str,
    insights: List[str],
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    """供 Web 与 SQLite 后端共用的简报 JSON 结构。"""
    max_per = _brief_max_per_topic()
    today = datetime.now().strftime("%Y-%m-%d")
    sections_out: List[Dict[str, Any]] = []
    for topic, full, chunk in _brief_rows_sections(brief_rows, max_per):
        heading = _topic_section_heading(topic).replace("## ", "")
        items_out: List[Dict[str, Any]] = []
        for row in chunk:
            sm = row.merged_summary
            primary = row.scored_items[0]
            items_out.append(
                {
                    "url": row.links[0],
                    "urls": list(row.links),
                    "title": (sm.get("title_zh") or _short_zh_title(primary.raw.title)).strip(),
                    "content": _merge_content_summary(sm),
                    "signal": (sm.get("investment_signal") or ["「建议跟踪」逻辑：信号待补充"])[0],
                    "source": _row_source_label(row),
                    "author": (primary.raw.author or "").strip(),
                    "topic": topic,
                }
            )
        sections_out.append(
            {
                "topic": topic,
                "heading": heading,
                "shown": len(chunk),
                "total": len(full),
                "items": items_out,
            }
        )
    return {
        "brief_id": brief_id,
        "date": today,
        "insights": insights[:3],
        "stats": {
            "monitored_total": stats.get("monitored_total", 0),
            "passed_count": stats.get("passed_count", 0),
            "platform_dist_brief": dict(stats.get("platform_dist_brief") or {}),
            "pref_hint": stats.get("pref_hint", ""),
            "coverage_ratio": stats.get("coverage_ratio", 0.0),
            "novelty_ratio_7d": stats.get("novelty_ratio_7d", 0.0),
            "feedback_coverage_ratio": stats.get("feedback_coverage_ratio", 0.0),
            "exploration_ratio": stats.get("exploration_ratio", 0.0),
        },
        "sections": sections_out,
    }


def write_brief_latest_json(
    brief_rows: List[BriefRow],
    brief_id: str,
    insights: List[str],
    stats: Dict[str, Any],
) -> tuple[Path, Dict[str, Any]]:
    """写入 data/brief_latest.json，并返回路径与 payload。"""
    payload = build_brief_payload(brief_rows, brief_id, insights, stats)
    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / "brief_latest.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path, payload


def _count_feedback_lines() -> int:
    try:
        if not FEEDBACK_PATH.exists():
            return 0
        return sum(1 for _ in FEEDBACK_PATH.open(encoding="utf-8") if _.strip())
    except OSError:
        return 0


def _feedback_items_set() -> set[str]:
    out: set[str] = set()
    try:
        if not FEEDBACK_PATH.exists():
            return out
        for line in FEEDBACK_PATH.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            u = str(rec.get("item") or "").strip()
            if u:
                out.add(u)
    except OSError:
        return out
    return out


def _health_snapshot_line(
    *,
    fetched: int,
    selected: int,
    topic_counts: Dict[str, int],
    llm_ok: int,
    llm_total: int,
) -> str:
    coverage = ",".join(f"{k}:{topic_counts.get(k, 0)}" for k in ("AI", "芯片", "机器人"))
    llm_ratio = 0.0 if llm_total <= 0 else (llm_ok / llm_total)
    return (
        f"[HEALTH] fetched={fetched} selected={selected} "
        f"coverage=({coverage}) llm_ok={llm_ok}/{llm_total}({llm_ratio:.0%})"
    )


def llm_daily_core_insights(brief_rows: List[BriefRow]) -> List[str]:
    """生成三条跨栏「今日核心洞察」（基于合并后的简报行）。"""
    topics_order = ["AI", "芯片", "机器人"]
    by_topic: Dict[str, List[BriefRow]] = {t: [] for t in topics_order}
    for row in brief_rows:
        if row.topic in by_topic:
            by_topic[row.topic].append(row)

    pack: List[str] = []
    for t in topics_order:
        for row in by_topic[t][:2]:
            sm = row.merged_summary
            sig = (sm.get("investment_signal") or [""])[0]
            tz = str(sm.get("title_zh") or "").strip() or "（无标题）"
            ol = str(sm.get("one_line") or "").strip()
            pack.append(f"[{t}] {tz[:40]} / {ol[:60]} / 信号:{sig[:48]}")
    bundle = "\n".join(pack) if pack else "（无入选摘要）"

    api_key, model, base_url = _resolve_llm_config()
    if not api_key or not base_url:
        out = []
        for t in topics_order:
            if by_topic[t]:
                sm = by_topic[t][0].merged_summary
                out.append((sm.get("one_line") or f"{t} 赛道有更新。")[:52])
        while len(out) < 3:
            out.append("详见下方分栏原文与摘要。")
        return out[:3]

    prompt = textwrap.dedent(
        f"""
        你是 VC 合伙人晨会用的简报编辑。根据下列「多源摘要要点」，写 **3 条** 跨赛道「今日核心洞察」。
        输出严格 JSON：{{"insights":["句1","句2","句3"]}}
        要求：每句 **36～55 个汉字**；独立成句；可含政策/产业链/商业化判断；勿编造具体公司融资额；勿重复同义句。
        **严禁**出现：文件名、终端命令、简报编号、面向开发者的说明。

        材料：
        {bundle}
        """
    ).strip()

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=45, context=build_ssl_context()) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        content = json.loads(body)["choices"][0]["message"]["content"]
        data = json.loads(content)
        ins = data.get("insights") or data.get("core_insights")
        if not isinstance(ins, list):
            raise ValueError("no list")
        out = [str(x).strip() for x in ins[:3] if str(x).strip()]
        while len(out) < 3:
            out.append("详见下文分栏要点。")
        return [s[:80] for s in out[:3]]
    except Exception as exc:
        if not _env_quiet():
            print(f"[WARN] 核心洞察 LLM 失败，使用降级: {exc}")
        out = []
        for t in topics_order:
            if by_topic[t]:
                sm = by_topic[t][0].merged_summary
                out.append((sm.get("one_line") or f"{t} 方向有更新。")[:52])
        while len(out) < 3:
            out.append("详见下方分栏。")
        return out[:3]


def compose_markdown(
    brief_rows: List[BriefRow],
    *,
    insights: List[str],
    stats: Dict[str, Any],
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    max_per = _brief_max_per_topic()

    lines = [
        f"# 每日投资信息简报 | {today}",
        "",
        "## 📈 今日核心洞察 (AI生成)",
    ]
    for ins in insights[:3]:
        lines.append(f"- {ins}")
    lines.append("")

    for topic, full, chunk in _brief_rows_sections(brief_rows, max_per):
        n_show = len(chunk)
        n_total = len(full)
        lines.append(f"{_topic_section_heading(topic)} ({n_show}/{n_total}条)")
        lines.append("")
        if not chunk:
            lines.append("*本栏暂无入选条目。*")
            lines.append("")
            continue
        for row in chunk:
            sm = row.merged_summary
            primary = row.scored_items[0]
            title_line = (sm.get("title_zh") or _short_zh_title(primary.raw.title)).strip()
            lines.append(f"**标题：** {title_line}")
            lines.append(f"**内容总结：** {_merge_content_summary(sm)}")
            sig = (sm.get("investment_signal") or ["「建议跟踪」逻辑：信号待补充"])[0]
            lines.append(f"**投资信号：** {sig}")
            for j, url in enumerate(row.links):
                it = row.scored_items[j] if j < len(row.scored_items) else primary
                src = it.raw.source
                author = (it.raw.author or "").strip()
                tail = f"{src}" + (f" · @{author}" if author and author != "unknown" else "")
                label = "原文链接" if len(row.links) == 1 else f"原文链接（{j + 1}/{len(row.links)}）"
                lines.append(f"**{label}：** [🔗]({url}) {tail}")
            lines.append("**用户反馈：** 请在 Web 简报页对相应条目点击 👍 / 👎。")
            lines.append("")

    lines.append("## 📊 本日数据统计")
    lines.append("")
    lines.append(f"- **监测总数**：{stats.get('monitored_total', 0)} 条")
    lines.append(f"- **筛选通过**：{stats.get('passed_count', 0)} 条")
    pd = stats.get("platform_dist_brief") or {}
    parts = [f"{k}({v})" for k, v in sorted(pd.items(), key=lambda x: -x[1])]
    lines.append(f"- **平台分布（入选简报）**：{' | '.join(parts) if parts else '（无）'}")
    ph = str(stats.get("pref_hint") or "").strip()
    if ph:
        lines.append(f"- **偏好反馈**：{ph}")
    lines.append("")
    lines.append("简报由 **VC 信息聚合 Agent** 自动生成。欢迎在页面提交 👍/👎 反馈以优化后续排序。")
    return "\n".join(lines)


def _raw_to_dict(item: RawItem) -> Dict[str, Any]:
    return {
        "source": item.source,
        "title": item.title,
        "author": item.author,
        "published": item.published,
        "link": item.link,
        "summary": item.summary,
        "channel_id": item.channel_id,
    }


def _raw_from_dict(d: Dict[str, Any]) -> RawItem:
    return RawItem(
        source=str(d.get("source") or ""),
        title=str(d.get("title") or ""),
        author=str(d.get("author") or ""),
        published=str(d.get("published") or ""),
        link=str(d.get("link") or ""),
        summary=str(d.get("summary") or ""),
        channel_id=d.get("channel_id"),
    )


def run_pipeline() -> None:
    """
    高频流水线：抓取 → 规则过滤 → LLM 摘要 → 写入 pipeline_items 候选池。
    不生成完整简报，不要求每栏满额。
    """
    from . import storage

    _rss_quiet_reset()
    channel_ids = load_youtube_channel_registry()
    try:
        max_per = int(load_env("YOUTUBE_RSS_MAX_PER_CHANNEL", "15") or "15")
    except ValueError:
        max_per = 15
    max_per = max(5, min(max_per, 50))
    all_items: List[RawItem] = []
    for channel_id in channel_ids:
        all_items.extend(fetch_youtube_channel_rss(channel_id, max_items=max_per))

    if not _env_quiet():
        print("[INFO] [pipeline] Twitter / 公众号采集已关闭，当前仅 YouTube 频道 RSS。")
        print(
            f"[INFO] [pipeline] RSS 合并 {len(all_items)} 条原始条目（{len(channel_ids)} 个频道；"
            f"单频道最多 {max_per} 条）"
        )

    if not all_items:
        if _env_quiet():
            print("⚠️  [pipeline] 未抓取到 RSS 条目，跳过入库（检查网络 / ALLOW_INSECURE_SSL）")
        else:
            print("[WARN] [pipeline] 未抓取到任何条目，跳过入库。")
        return

    prefs = load_preferences()
    scored: List[ScoredItem] = []
    for item in all_items:
        si = classify_and_score(item, prefs)
        if si is not None:
            scored.append(si)

    scored = deduplicate(scored)
    scored.sort(key=lambda x: x.score, reverse=True)
    try:
        per_topic = int(load_env("PIPELINE_PER_TOPIC", "12") or "12")
    except ValueError:
        per_topic = 12
    per_topic = max(3, min(per_topic, 30))
    selected = select_for_brief(scored, per_topic=per_topic)
    _cnt = {}
    for s in selected:
        _cnt[s.topic] = _cnt.get(s.topic, 0) + 1
    if not _env_quiet():
        print(
            f"[INFO] [pipeline] 候选入选 {len(selected)} 条（分栏 {dict(_cnt)}），"
            f"PIPELINE_PER_TOPIC={per_topic}"
        )

    if not selected:
        if _env_quiet():
            print("⚠️  [pipeline] 无条目通过过滤，跳过 LLM 与入库")
        else:
            print("[WARN] [pipeline] 无条目通过过滤，跳过 LLM 与入库。")
        return

    storage.init_db()
    n_ok = 0
    llm_ok = 0
    for item in selected:
        sm = llm_summarize(item)
        if "摘要生成失败" not in (sm.get("investment_signal") or [""])[0]:
            llm_ok += 1
        storage.upsert_pipeline_item(
            item.raw.link,
            item.topic,
            item.score,
            item.reason,
            _raw_to_dict(item.raw),
            sm,
        )
        n_ok += 1
    if not _env_quiet():
        print(_health_snapshot_line(fetched=len(all_items), selected=len(selected), topic_counts=_cnt, llm_ok=llm_ok, llm_total=n_ok))
    if _env_quiet():
        tip = ""
        if _RSS_QUIET_MISMATCH or _RSS_QUIET_FETCH_FAIL:
            tip = f" · ⚠️ RSS 标题不符 {_RSS_QUIET_MISMATCH} · 拉取失败 {_RSS_QUIET_FETCH_FAIL}"
        print(f"✅ 候选池已更新 · {n_ok} 条 · RSS 原始 {len(all_items)} 条{tip}")
    else:
        print(f"[OK] [pipeline] 已写入/更新候选池 {n_ok} 条（SQLite pipeline_items）")


def run_daily_brief() -> None:
    """
    低频简报：从过去 PIPELINE_BRIEF_LOOKBACK_HOURS（默认 24）小时的候选池取条，
    每栏至多 BRIEF_PER_TOPIC 条，生成简报并写入 briefs + brief_latest.json。
    """
    from . import storage

    try:
        hours = int(load_env("PIPELINE_BRIEF_LOOKBACK_HOURS", "24") or "24")
    except ValueError:
        hours = 24
    hours = max(1, min(hours, 168))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = storage.list_pipeline_since(cutoff)
    if not _env_quiet():
        print(f"[INFO] [daily_brief] 过去 {hours} 小时内候选池条目: {len(rows)}")

    try:
        per_topic = int(load_env("BRIEF_PER_TOPIC", "5") or "5")
    except ValueError:
        per_topic = 5
    per_topic = max(1, min(per_topic, 12))
    try:
        exploration_ratio = float(load_env("BRIEF_EXPLORATION_RATIO", "0.1") or "0.1")
    except ValueError:
        exploration_ratio = 0.1
    exploration_ratio = max(0.0, min(exploration_ratio, 0.5))

    topics_order = ["AI", "芯片", "机器人"]
    by_topic: Dict[str, List[Dict[str, Any]]] = {t: [] for t in topics_order}
    for row in rows:
        t = row.get("topic") or ""
        if t in by_topic:
            by_topic[t].append(row)
    for t in topics_order:
        by_topic[t].sort(key=lambda x: -float(x.get("score") or 0))

    scored: List[ScoredItem] = []
    summaries: Dict[str, Dict[str, Any]] = {}
    seen: set[str] = set()
    for t in topics_order:
        rows_t = by_topic[t]
        top_n = max(1, int(round(per_topic * (1.0 - exploration_ratio))))
        top_n = min(top_n, per_topic, len(rows_t))
        pick_rows = list(rows_t[:top_n])
        remain = rows_t[top_n:]
        need_explore = max(0, per_topic - len(pick_rows))
        if need_explore > 0 and remain:
            step = max(1, len(remain) // need_explore)
            idx = 0
            while len(pick_rows) < per_topic and idx < len(remain):
                pick_rows.append(remain[idx])
                idx += step
        for row in pick_rows[:per_topic]:
            u = str(row.get("url") or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            raw = _raw_from_dict(row["raw"])
            si = ScoredItem(
                raw=raw,
                topic=t,
                score=float(row.get("score") or 0),
                reason=str(row.get("reason") or ""),
            )
            scored.append(si)
            summaries[u] = row.get("summary") or {}

    if not scored:
        if _env_quiet():
            print(f"⚠️  [daily_brief] 过去 {hours}h 内无可用候选，跳过简报")
        else:
            print("[WARN] [daily_brief] 候选池为空或无法分栏，跳过简报生成。")
        return

    short_warn = []
    for t in topics_order:
        n = sum(1 for s in scored if s.topic == t)
        if n < per_topic:
            short_warn.append(f"{t}:{n}/{per_topic}")
    if short_warn and not _env_quiet():
        print(f"[WARN] [daily_brief] 部分栏目未满额: {', '.join(short_warn)}")

    brief_rows = build_merged_brief_rows(scored, summaries)
    cnt_topic = dict(Counter(br.topic for br in brief_rows))
    insights = llm_daily_core_insights(brief_rows)
    n_feedback = _count_feedback_lines()
    pref_hint = (
        f"已积累 {n_feedback} 条用户反馈，用于优化内容排序。"
        if n_feedback
        else "暂无偏好反馈，排序采用默认权重。"
    )
    stats: Dict[str, Any] = {
        "monitored_total": len(rows),
        "passed_count": len(brief_rows),
        "platform_dist_brief": dict(Counter(br.scored_items[0].raw.source for br in brief_rows)),
        "pref_hint": pref_hint,
        "coverage_ratio": round(len(brief_rows) / max(1, 3 * per_topic), 4),
        "exploration_ratio": exploration_ratio,
    }
    feedback_set = _feedback_items_set()
    brief_urls = {u for br in brief_rows for u in br.links}
    stats["feedback_coverage_ratio"] = round(
        len([u for u in brief_urls if u in feedback_set]) / max(1, len(brief_urls)), 4
    )
    stats["novelty_ratio_7d"] = 1.0

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    brief_id = f"brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_path = output_dir / f"{brief_id}.md"
    md_text = compose_markdown(brief_rows, insights=insights, stats=stats)
    out_path.write_text(md_text, encoding="utf-8")

    brief_json, brief_payload = write_brief_latest_json(brief_rows, brief_id, insights, stats)
    rid: Optional[int] = None
    if not _env_quiet():
        print(f"[OK] [daily_brief] 最新简报数据: {brief_json}")
    try:
        storage.init_db()
        rid = storage.save_brief(brief_id, brief_payload, md_text)
        if not _env_quiet():
            print(f"[OK] [daily_brief] 已写入 SQLite briefs: id={rid}")
    except Exception as exc:
        if not _env_quiet():
            print(f"[WARN] [daily_brief] 持久化失败: {exc}")

    try:
        from .feishu_push import push_daily_brief_to_feishu

        push_daily_brief_to_feishu(brief_payload, md_text, brief_id=brief_id, md_path=out_path)
        push_ok = 1
    except Exception as exc:
        push_ok = 0
        if not _env_quiet():
            print(f"[WARN] [daily_brief] 飞书推送异常（已忽略，本地简报已保存）: {exc}")
    if not _env_quiet():
        print(
            _health_snapshot_line(
                fetched=len(rows),
                selected=len(brief_rows),
                topic_counts=cnt_topic,
                llm_ok=len(brief_rows),
                llm_total=len(brief_rows),
            )
            + f" push_ok={push_ok}"
        )

    if _env_quiet():
        sw = f" · 栏目 {', '.join(short_warn)}" if short_warn else ""
        if rid is not None:
            print(f"✅ 每日简报已生成 · {len(brief_rows)} 条（合并后） → SQLite #{rid} · {brief_id}{sw}")
        else:
            print(f"⚠️  每日简报已写文件但 SQLite 未入库 · {brief_id}{sw}")
    else:
        print(f"[OK] [daily_brief] 已生成简报: {out_path}，合并后条目数: {len(brief_rows)}")


def run() -> None:
    _rss_quiet_reset()
    channel_ids = load_youtube_channel_registry()
    try:
        max_per = int(load_env("YOUTUBE_RSS_MAX_PER_CHANNEL", "15") or "15")
    except ValueError:
        max_per = 15
    max_per = max(5, min(max_per, 50))
    all_items: List[RawItem] = []
    for channel_id in channel_ids:
        all_items.extend(fetch_youtube_channel_rss(channel_id, max_items=max_per))

    if not _env_quiet():
        print("[INFO] Twitter / 公众号采集已关闭（见文件顶部 TODO），当前仅 YouTube 频道 RSS。")
        print(
            f"[INFO] RSS 合并 {len(all_items)} 条原始条目（{len(channel_ids)} 个频道；"
            f"单频道最多取 {max_per} 条，可用 YOUTUBE_RSS_MAX_PER_CHANNEL 调整）"
        )
    by_ch: Dict[str, List[RawItem]] = {}
    for it in all_items:
        cid = (it.channel_id or "").strip()
        by_ch.setdefault(cid, []).append(it)
    empty_ch = 0
    for cid in channel_ids:
        rows = by_ch.get(cid, [])
        if not rows:
            empty_ch += 1
            if not _env_quiet():
                print(f"[WARN] RSS channel_id={cid} 无条目，请检查网络或 ID 是否正确")
            continue
        if not _env_quiet():
            a0 = (rows[0].author or "").strip()
            t0 = (rows[0].title or "").strip()
            tail = t0 if len(t0) <= 48 else t0[:47] + "…"
            bar = CHANNEL_DEFAULT_TOPIC.get(cid, "?")
            print(f"[INFO] RSS 校验 channel={cid} →分栏「{bar}」: {len(rows)} 条, 首条作者={a0!r}, 标题={tail!r}")
    if _env_quiet() and len(all_items) > 0:
        extra = ""
        if _RSS_QUIET_MISMATCH or _RSS_QUIET_FETCH_FAIL:
            extra += f" · ⚠️ 标题不符 {_RSS_QUIET_MISMATCH} · 拉取失败 {_RSS_QUIET_FETCH_FAIL}"
        if empty_ch:
            extra += f" · ⚠️ {empty_ch} 个频道无条目"
        print(f"✅ RSS 已拉取 {len(all_items)} 条（{len(channel_ids)} 频道 · 每频道≤{max_per}）{extra}")

    if not all_items:
        raise RuntimeError(
            "未抓取到任何 YouTube 数据。请检查网络；若 VPN 下 SSL 报错可设置 ALLOW_INSECURE_SSL=true。"
        )

    prefs = load_preferences()
    scored = []
    for item in all_items:
        si = classify_and_score(item, prefs)
        if si is not None:
            scored.append(si)

    scored = deduplicate(scored)
    scored.sort(key=lambda x: x.score, reverse=True)
    try:
        per_topic = int(load_env("BRIEF_PER_TOPIC", "5") or "5")
    except ValueError:
        per_topic = 5
    per_topic = max(1, min(per_topic, 12))
    try:
        exploration_ratio = float(load_env("BRIEF_EXPLORATION_RATIO", "0.1") or "0.1")
    except ValueError:
        exploration_ratio = 0.1
    exploration_ratio = max(0.0, min(exploration_ratio, 0.5))
    scored = select_for_brief(scored, per_topic=per_topic)
    _cnt = {}
    for s in scored:
        _cnt[s.topic] = _cnt.get(s.topic, 0) + 1
    if not _env_quiet():
        print(
            f"[INFO] 简报入选 {len(scored)} 条（分栏 {dict(_cnt)}），"
            f"每栏 BRIEF_PER_TOPIC={per_topic}；白名单频道见 CHANNEL_DEFAULT_TOPIC（固定分栏）"
        )

    if not scored:
        raise RuntimeError("抓取到数据但没有条目通过质量过滤。")

    for col in ("芯片", "机器人"):
        n = _cnt.get(col, 0)
        if n < per_topic:
            raise RuntimeError(
                f"栏目「{col}」仅入选 {n} 条，少于要求的 {per_topic} 条。"
                "单个频道 RSS 常见约 15 条；请在 agent.run 的 channel_ids 中为该栏增加频道，"
                "或调低环境变量 BRIEF_PER_TOPIC。"
            )

    summaries: Dict[str, Dict[str, Any]] = {}
    for item in scored:
        summaries[item.raw.link] = llm_summarize(item)

    brief_rows = build_merged_brief_rows(scored, summaries)
    insights = llm_daily_core_insights(brief_rows)
    n_feedback = _count_feedback_lines()
    pref_hint = (
        f"已积累 {n_feedback} 条用户反馈，用于优化内容排序。"
        if n_feedback
        else "暂无偏好反馈，排序采用默认权重。"
    )
    stats: Dict[str, Any] = {
        "monitored_total": len(all_items),
        "passed_count": len(brief_rows),
        "platform_dist_brief": dict(Counter(br.scored_items[0].raw.source for br in brief_rows)),
        "pref_hint": pref_hint,
        "coverage_ratio": round(len(brief_rows) / max(1, 3 * per_topic), 4),
        "exploration_ratio": exploration_ratio,
    }
    feedback_set = _feedback_items_set()
    brief_urls = {u for br in brief_rows for u in br.links}
    stats["feedback_coverage_ratio"] = round(
        len([u for u in brief_urls if u in feedback_set]) / max(1, len(brief_urls)), 4
    )
    stats["novelty_ratio_7d"] = 1.0

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    brief_id = f"brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_path = output_dir / f"{brief_id}.md"
    md_text = compose_markdown(brief_rows, insights=insights, stats=stats)
    out_path.write_text(md_text, encoding="utf-8")

    brief_json, brief_payload = write_brief_latest_json(brief_rows, brief_id, insights, stats)
    rid_run: Optional[int] = None
    if not _env_quiet():
        print(f"[OK] 最新简报数据: {brief_json}")
    try:
        from . import storage

        storage.init_db()
        rid_run = storage.save_brief(brief_id, brief_payload, md_text)
        if not _env_quiet():
            print(f"[OK] 已写入后端 SQLite: id={rid_run}（vc_agent.db）")
    except Exception as exc:
        if not _env_quiet():
            print(f"[WARN] 后端持久化失败（文件简报仍可用）: {exc}")

    if _env_quiet():
        db_line = f"SQLite #{rid_run}" if rid_run is not None else "SQLite（未写入）"
        print(f"✅ 简报已生成 · {len(brief_rows)} 条（合并后） → {db_line} · {out_path.name}")
    else:
        print(f"[OK] 已生成简报: {out_path}")
        print(f"[OK] 合并后条目数: {len(brief_rows)}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"[ERROR] 运行失败: {e}")
        raise
