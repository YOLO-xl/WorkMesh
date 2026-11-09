"""首页配置的回归测试：模块清单必须覆盖全部功能页且卡片配置合法。"""

from ui.landing_page import MODULES


def test_modules_cover_all_pages() -> None:
    pages = {module["page"] for module in MODULES}

    assert pages == {
        "数据接入",
        "证据关联",
        "问题聚类",
        "反馈分析",
        "单条诊断",
        "故障排查",
        "故障场景",
        "报告中心",
    }


def test_module_keys_and_tags_are_unique_and_valid() -> None:
    keys = [module["key"] for module in MODULES]

    assert len(keys) == len(set(keys))

    for module in MODULES:
        assert module["group"] in {"core", "support"}
        assert 2 <= len(module["tags"]) <= 4
        assert module["icon"]
        assert module["desc"]
