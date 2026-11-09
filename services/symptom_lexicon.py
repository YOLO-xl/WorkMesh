"""用户反馈的症状归约。

普通用户报障时很少带错误码，描述又是口语化的，直接算文本相似度会碎成
一堆孤立反馈。这里先把描述压成两类可解释信号：

1. 现象（symptom）：用户实际感知到的表现，如「加载失败 / 白屏 / 转圈」；
2. 功能实体（feature）：出问题的页面、按钮或能力，如「登录 / 直播 / 支付」。

聚类用它做加权打分的强信号，分析页用它做分布统计。词典是唯一的可调入口，
新增说法只需往 SYMPTOM_LEXICON 里补一条。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

SYMPTOM_LEXICON: dict[str, tuple[str, ...]] = {
    "加载失败": (
        "加载失败", "加载不出来", "加载不了", "加载不出", "进不去", "打不开",
        "打开不了", "无法打开", "起不来", "拉取失败", "请求失败", "获取失败",
        "页面加载", "打开就卡", "进不了", "进不去页面", "打不开页面",
    ),
    "白屏": ("白屏", "一片白", "空白页", "页面空白", "全白"),
    "黑屏": ("黑屏", "一片黑", "页面全黑", "全黑"),
    "转圈": ("转圈", "一直转", "转个不停", "loading", "缓冲", "卡在加载", "加载中"),
    "闪退": ("闪退", "秒退", "自动退出", "突然退出", "应用退出", "app退出"),
    "卡死": (
        "卡死", "卡住", "无响应", "没反应", "点了没反应", "假死", "卡着不动",
        "动不了", "没有反应", "不能操作", "操作不了",
    ),
    "卡顿": ("卡顿", "很卡", "掉帧", "不流畅", "一卡一卡", "特别卡"),
    "登录失败": (
        "登录失败", "登不上", "登陆不了", "登录不了", "无法登录", "账号异常",
        "掉线", "被踢出", "登录态失效", "自动登出", "被踢下线", "踢下线",
        "重新登录", "登录异常", "登录问题",
    ),
    "报错": ("报错", "错误提示", "提示错误", "弹窗错误", "异常提示", "错误码", "提示失败"),
    "播放失败": ("播放失败", "无法播放", "放不出来", "视频加载不了", "开播失败", "看不了视频"),
    "声音异常": ("没声音", "无声", "杂音", "噪音", "音画不同步", "声音卡", "听不到"),
    "数据异常": (
        "数据不对", "数据丢失", "数据错乱", "显示不对", "金额不对", "记录没了",
        "不同步", "没数据", "统计不对", "数字不对", "金额错误", "金额显示",
        "显示错误", "数据对不上", "对不上", "扣款不对", "价格不对", "记录不对",
    ),
    "上传下载": ("上传失败", "下载失败", "传不上去", "下载不了", "附件打不开", "传不了"),
}

FEATURE_TERMS: tuple[str, ...] = (
    "登录", "注册", "直播", "开播", "播放", "观看", "上传", "下载", "支付",
    "下单", "订单", "退款", "消息", "推送", "搜索", "首页", "详情页", "列表",
    "后台", "控制台", "开放平台", "账号", "弹幕", "评论", "分享", "导出",
    "导入", "报表", "看板", "sdk", "接口", "商城", "钱包", "优惠券", "设置",
    "个人中心", "直播间", "短视频", "权限",
)

# 反馈里的客套话与无信息量表达，只删这些，避免把否定词（"一直""不行"）误删。
FILLER_PHRASES: tuple[str, ...] = (
    "麻烦", "帮忙", "帮我看", "看一下", "看下", "我这边", "我们这边", "请问",
    "谢谢", "您好", "你好", "反馈一下", "反映一下", "提个", "就是", "那个",
    "这个", "然后", "的话", "一下", "能不能", "可不可以", "怎么办",
)

STOPWORDS: frozenset[str] = frozenset(
    """
    的 了 和 与 是 在 有 我 你 他 她 它 们 这 那 就 都 也 还 又 很 太 更
    不 没 没有 要 会 能 可以 把 被 给 让 对 从 到 为 以 及 而 但 但是 因为
    所以 如果 现在 已经 还是 一直 老是 总是 特别 非常 有点 一些 一个 什么
    怎么 为什么 如何 哪里 哪个 时候 时间 问题 情况 出现 发现 感觉 好像 应该
    重启 无效 没用 不行 无法 不能 不了 不太 有点 一下 试试 试了 还是
    点进 进入 打开 看到 就是 之后 然后 目前 这边 我这边 帮忙 麻烦 请问 谢谢
    页面 反应 东西 完全 彻底 直接 根本 实在 真的
    """.split()
)


def normalize_text(text: Optional[str]) -> str:
    """统一大小写、全半角与标点，并去掉客套话，保留信息词。"""

    if not text:
        return ""

    value = str(text).lower()
    value = value.translate(str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９",
                                          "abcdefghijklmnopqrstuvwxyz0123456789"))
    value = re.sub(r"https?://\S+", " ", value)
    for phrase in FILLER_PHRASES:
        value = value.replace(phrase, " ")
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def compact_text(text: Optional[str]) -> str:
    """去空白版本，用于字符 n-gram 相似度。"""

    return normalize_text(text).replace(" ", "")


def extract_symptoms(text: Optional[str]) -> list[str]:
    """返回描述里命中的现象标签（按词典顺序，去重）。"""

    value = str(text or "").lower()
    if not value:
        return []

    hits = [
        label
        for label, patterns in SYMPTOM_LEXICON.items()
        if any(pattern in value for pattern in patterns)
    ]
    return hits


def extract_features(text: Optional[str]) -> list[str]:
    value = str(text or "").lower()
    if not value:
        return []

    return [term for term in FEATURE_TERMS if term in value]


def text_signature(text: Optional[str]) -> str:
    """归一化文本的指纹，用于把人工标注复用到相同说法上。"""

    return compact_text(text)[:120]


@lru_cache(maxsize=1)
def _tokenizer():
    import jieba

    jieba.setLogLevel(60)
    return jieba


def tokenize(text: Optional[str]) -> list[str]:
    """轻量分词，只保留信息词。"""

    value = normalize_text(text)
    if not value:
        return []

    tokens = [
        token.strip()
        for token in _tokenizer().lcut(value)
        if len(token.strip()) >= 2
        and token.strip() not in STOPWORDS
        and not token.strip().isdigit()
    ]
    return tokens


def keywords(text: Optional[str], limit: int = 5) -> list[str]:
    """单条描述的关键词：现象、功能实体优先，其次分词结果。"""

    result: list[str] = []
    for word in extract_symptoms(text) + extract_features(text) + tokenize(text):
        if word not in result:
            result.append(word)
        if len(result) >= limit:
            break
    return result


SUGGESTION_MARKERS: tuple[str, ...] = (
    "建议", "希望", "能不能", "可不可以", "加个", "增加", "支持一下",
    "优化一下", "提个需求", "产品需求",
)


def is_suggestion(text: Optional[str]) -> bool:
    """产品建议/功能诉求不是故障，混进问题簇会稀释影响面。"""

    value = str(text or "")
    return any(marker in value for marker in SUGGESTION_MARKERS)
