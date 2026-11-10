# -*- coding: utf-8 -*-
"""
一次性修复脚本：按上海日期重算全部单集的日/周/月/年关联。

背景：866 期单集逐条核对后发现 22 期单集的日/月关联是错的——都指向了
2026 年 5 月的一行（2026 年 6 月的 18 期全错挂到了 5 月，另有 2025 年
11 月 2 日 1 期、2026 年 7 月 2 日 1 期、2026 年 5 月 31 日 2 期只错了
日关联）。根因是历史遗留的错误链接，现在的同步代码（get_date_relation
按日期查标题）本身是对的，所以按日期重算一次就能修好且不会复发。

本脚本只做四件事（只跑一次，由临时工作流手动触发）：
  (1) 按日期重算全部 Episode 行的日/周/月/年关联，只更新不一致的项；
  (2) 把月份行 2023年09月（28a1435a-9560-81b4-a3f4-e060e288650c）的
      “收听天数”改成 9（原来是 8；之前按 UTC 日期数了 8 天，按上海日期
      是 9 天，其中 2023-09-21 23:04 UTC 的一次收听是上海 09-22）；
  (3) 归档没有任何单集链接的空白日行和周行（须在步骤 (1) 之后做）；
  (4) 同一次运行内复核：重新读取以下 22 个单集，确认四个关联都已正确，
      再拉取“年”数据源的 4 行并原样打印每行的完整 properties。

用法（GitHub Actions，见 .github/workflows/migrate-fix-links.yml）：
    python scripts/migrate_fix_links.py

token 只从环境变量 NOTION_TOKEN / NOTION_PAGE 读取，
脚本里不写任何 token。
"""

import json
import os
import sys
import time
import uuid

import pendulum

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utils
from notion_helper import NotionHelper, retry_on_rate_limit

# 写操作之间的停顿（秒）。
WRITE_INTERVAL_SECONDS = 0.3

# (2) 2023年09月月份行，只改“收听天数”这一个数字。
MONTH_2023_09_PAGE_ID = "28a1435a-9560-81b4-a3f4-e060e288650c"
MONTH_2023_09_LISTEN_DAYS = 9

# (4) 复核的 22 个单集 id（带横线 UUID，逐字照抄）。
CHECK_EPISODE_IDS = [
    "29d1435a-9560-8199-8803-f36e820a44e2",
    "35d1435a-9560-81a2-9387-e23f8fb03188",
    "36f1435a-9560-8131-a8b5-cf49d5f6f49e",
    "3921435a-9560-81dc-a6a8-ee06d9482dc1",
    "3921435a-9560-81ff-aa03-d23d8ff20a79",
    "3921435a-9560-811d-b755-d050a56ecaad",
    "3921435a-9560-819f-93e4-db1bcdba5160",
    "3921435a-9560-81ab-8cb9-dbd1d5324f23",
    "3921435a-9560-81b0-9700-c1352ca984af",
    "3921435a-9560-8152-85fc-e1d634523cb7",
    "3921435a-9560-814e-b78a-d997aa664b9f",
    "3921435a-9560-8183-930d-d5acb79ad723",
    "3921435a-9560-81ef-9427-fb7b8dc13541",
    "3921435a-9560-811d-9e1f-e046af5e538d",
    "3921435a-9560-8187-b627-f959ae2b4403",
    "3921435a-9560-81b9-8bb3-d8f1eabe009a",
    "3921435a-9560-811f-b00a-d8dde7337edf",
    "3921435a-9560-81e7-a239-d2a07fba13cf",
    "3921435a-9560-81e7-ad48-fd214a6f3690",
    "3921435a-9560-8104-be0a-f256bebee409",
    "3921435a-9560-8108-8da4-e93d77960936",
    "3921435a-9560-81ed-887a-c337c981d059",
]

RELATION_KEYS = ("日", "周", "月", "年")

NOTION = None


def normalize_id(page_id):
    """把 id 规范化成标准带横线 UUID 字符串；空值返回 None。"""
    if not page_id:
        return None
    return str(uuid.UUID(str(page_id)))


def first_relation_id(value):
    """从 relation 属性值（utils.get_property_value 的返回值）取第一个 id。"""
    if not value:
        return None
    if isinstance(value, list) and value:
        return normalize_id(value[0].get("id"))
    return None


def current_relation_ids(properties):
    """从一行原始 properties 里取当前的日/周/月/年 relation id。"""
    return {
        key: first_relation_id(utils.get_property_value(properties.get(key)))
        for key in RELATION_KEYS
    }


def expected_relation_ids(date):
    """按上海日期算出应有的四个关联 id（按标题查找、缺失时新建，
    同一个标题在 NotionHelper 内只查一次、有缓存）。"""
    return {
        "日": normalize_id(NOTION.get_day_relation_id(date)),
        "周": normalize_id(NOTION.get_week_relation_id(date)),
        "月": normalize_id(NOTION.get_month_relation_id(date)),
        "年": normalize_id(NOTION.get_year_relation_id(date)),
    }


def has_episode_link(properties):
    """日/周行是否还链接着单集：先看名为 Episode 的关联属性；
    若该属性不存在，再看该行全部 relation 属性是否都为空。"""
    episode = properties.get("Episode")
    if episode is not None:
        return bool(utils.get_property_value(episode))
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "relation":
            if utils.get_property_value(prop):
                return True
    return False


@retry_on_rate_limit
def archive_page(page_id):
    """归档页面（移到回收站，可恢复）。"""
    return NOTION.client.pages.update(page_id=page_id, archived=True)


@retry_on_rate_limit
def retrieve_page(page_id):
    return NOTION.client.pages.retrieve(page_id=page_id)


def step1_fix_episode_links():
    """步骤 (1)：按日期重算全部单集的日/周/月/年关联。"""
    print("步骤 (1)：按日期重算全部单集的日/周/月/年关联……")
    episodes = NOTION.query_all(NOTION.episode_database_id)
    print(f"  拉取到 Episode 行 {len(episodes)} 个")
    checked = 0
    fixed = 0
    failed = []
    skipped = 0
    for row in episodes:
        page_id = row.get("id")
        properties = row.get("properties") or {}
        try:
            timestamp = utils.get_property_value(properties.get("日期"))
            if not timestamp:
                skipped += 1
                continue
            checked += 1
            date = pendulum.from_timestamp(timestamp, tz="Asia/Shanghai")
            expected = expected_relation_ids(date)
            current = current_relation_ids(properties)
            changed = {
                key: {"relation": [{"id": expected[key]}]}
                for key in RELATION_KEYS
                if expected[key] != current.get(key)
            }
            if not changed:
                continue
            NOTION.update_page(page_id, changed)
            fixed += 1
            print(
                f"  [修复] 单集 {page_id} 上海日期 {date.to_date_string()} "
                f"改了：{', '.join(changed.keys())}"
            )
            time.sleep(WRITE_INTERVAL_SECONDS)
        except Exception as exc:
            failed.append(page_id)
            print(f"  [失败] 单集 {page_id}: {exc}")
    print(
        f"步骤 (1) 完成：检查 {checked} 个（无日期跳过 {skipped} 个），"
        f"修复 {fixed} 个，失败 {len(failed)} 个"
    )
    if failed:
        print(f"  失败的单集 id: {', '.join(failed)}")
    return checked, fixed, failed


def step2_fix_month_listen_days():
    """步骤 (2)：把 2023年09月月份行的“收听天数”改成 9，只改这一个数字。"""
    print("步骤 (2)：把月份行 2023年09月 的“收听天数”改成 9……")
    try:
        NOTION.update_page(
            MONTH_2023_09_PAGE_ID,
            {"收听天数": {"number": MONTH_2023_09_LISTEN_DAYS}},
        )
        print(
            f"  [完成] 月份行 {MONTH_2023_09_PAGE_ID}（2023年09月）"
            f"“收听天数”已改成 {MONTH_2023_09_LISTEN_DAYS}，其他不动"
        )
        time.sleep(WRITE_INTERVAL_SECONDS)
        return True
    except Exception as exc:
        print(f"  [失败] 月份行 {MONTH_2023_09_PAGE_ID}（2023年09月）: {exc}")
        return False


def archive_empty_rows(database_id, label):
    """归档一个数据源里没有任何单集链接的行，返回（归档数，失败 id）。"""
    rows = NOTION.query_all(database_id)
    print(f"  拉取到{label}行 {len(rows)} 个")
    archived = 0
    failed = []
    for row in rows:
        page_id = row.get("id")
        properties = row.get("properties") or {}
        if has_episode_link(properties):
            continue
        title = utils.get_property_value(properties.get("标题"))
        try:
            archive_page(page_id)
            archived += 1
            print(f"  [归档] {label}行 {title}（{page_id}）")
            time.sleep(WRITE_INTERVAL_SECONDS)
        except Exception as exc:
            failed.append(page_id)
            print(f"  [失败] {label}行 {title}（{page_id}）: {exc}")
    return archived, failed


def step3_archive_empty_day_week_rows():
    """步骤 (3)：归档没有任何单集链接的空白日行和周行。
    注意：这一步必须在步骤 (1) 之后做。"""
    print("步骤 (3)：归档没有任何单集链接的空白日行和周行……")
    day_archived, day_failed = archive_empty_rows(NOTION.day_database_id, "日")
    week_archived, week_failed = archive_empty_rows(NOTION.week_database_id, "周")
    print(f"步骤 (3) 完成：归档日行 {day_archived} 个，周行 {week_archived} 个")
    if day_failed or week_failed:
        print(f"  失败的 id: {', '.join(day_failed + week_failed)}")
    return day_archived, week_archived, day_failed + week_failed


def step4_verify():
    """步骤 (4)：同一次运行内复核 22 个单集，并打印“年”数据源 4 行的完整 properties。"""
    print("步骤 (4)：同一次运行内复核 22 个单集……")
    ok = 0
    failed = []
    for page_id in CHECK_EPISODE_IDS:
        try:
            page = retrieve_page(page_id)
            properties = page.get("properties") or {}
            timestamp = utils.get_property_value(properties.get("日期"))
            if not timestamp:
                failed.append(page_id)
                print(f"  [复核失败] 单集 {page_id}：没有日期，无法复核")
                continue
            date = pendulum.from_timestamp(timestamp, tz="Asia/Shanghai")
            expected = expected_relation_ids(date)
            current = current_relation_ids(properties)
            mismatched = [
                key for key in RELATION_KEYS if expected[key] != current.get(key)
            ]
            if mismatched:
                failed.append(page_id)
                print(
                    f"  [复核失败] 单集 {page_id} 上海日期 {date.to_date_string()} "
                    f"不一致：{', '.join(mismatched)}"
                )
            else:
                ok += 1
                print(
                    f"  [复核通过] 单集 {page_id} 上海日期 {date.to_date_string()} "
                    "日/周/月/年关联全部一致"
                )
        except Exception as exc:
            failed.append(page_id)
            print(f"  [复核失败] 单集 {page_id}: {exc}")
    print(f"复核单集：通过 {ok} 个，失败 {len(failed)} 个")
    if failed:
        print(f"  复核失败的单集 id: {', '.join(failed)}")
    print("拉取“年”数据源的全部行，原样打印每行的完整 properties……")
    year_rows = NOTION.query_all(NOTION.year_database_id)
    print(f"  “年”数据源共 {len(year_rows)} 行")
    for row in year_rows:
        title = utils.get_property_value((row.get("properties") or {}).get("标题"))
        print(f"  年行 {title}（{row.get('id')}）properties:")
        print(json.dumps(row.get("properties"), ensure_ascii=False))
    return ok, failed, len(year_rows)


def main():
    global NOTION
    if not os.getenv("NOTION_TOKEN") or not os.getenv("NOTION_PAGE"):
        print("失败：缺少环境变量 NOTION_TOKEN 或 NOTION_PAGE")
        return 1
    NOTION = NotionHelper()
    print("修复开始：使用仓库现有的 NotionHelper（环境变量 NOTION_TOKEN / NOTION_PAGE）。")

    checked, fixed, failed1 = step1_fix_episode_links()
    month_ok = step2_fix_month_listen_days()
    day_archived, week_archived, failed3 = step3_archive_empty_day_week_rows()
    verified_ok, failed4, year_count = step4_verify()

    print("========== 全程汇总 ==========")
    print(f"步骤 (1) 单集关联重算：检查 {checked} 个，修复 {fixed} 个，失败 {len(failed1)} 个")
    print(f"步骤 (2) 2023年09月收听天数改成 9：{'成功' if month_ok else '失败'}")
    print(f"步骤 (3) 归档空白行：日行 {day_archived} 个，周行 {week_archived} 个，失败 {len(failed3)} 个")
    print(f"步骤 (4) 复核 22 个单集：通过 {verified_ok} 个，失败 {len(failed4)} 个；“年”数据源共 {year_count} 行")
    all_failed = failed1 + failed3 + failed4
    if not month_ok:
        all_failed.append(MONTH_2023_09_PAGE_ID)
    if all_failed:
        print(f"失败的 id: {', '.join(all_failed)}")
        print("修复完成（有失败项，见上）。")
        return 1
    print("修复完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
