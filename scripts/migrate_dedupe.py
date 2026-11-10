"""一次性数据清理迁移脚本（只跑一次，由临时工作流手动触发）。

清理内容（所谓“删除”均为归档到 Notion 回收站，可恢复）：
  (1) 归档 22 个重复单集行；
  (2) 归档 1 个重复脑图页；
  (3) 把 2 期单集的 Podcast 关联改到保留的播客行，再归档重复播客行；
  (4) 归档 2 个已无单集链接的重复播客行（须在步骤 (1) 之后执行）；
  (5) 归档 4 个重复日行和 1 个重复周行；
  (6) 把 46 期单集的“年”关联改到年份行“2026”；
  (7) 把 3 个月份行的“年”关联改到年份行“2026”，再归档年份行“2026年”和“2025年”；
  (8) 新建缺失的月份行“2026年06月”；
  (9) 补填 5 个 2023 年月份行的收听时长（秒）和收听天数。

本脚本只用仓库已有的 NotionHelper，token 全部来自环境变量
NOTION_TOKEN / NOTION_PAGE，脚本内不保存任何 token。
所有页面 ID 在脚本中均以 32 位无横线 hex 写出，调用前统一用
uuid.UUID(hex) 规范化成标准 UUID 再传给 Notion API。
每个写操作都用 notion_helper.retry_on_rate_limit 做限流重试，
写操作之间停 0.3 秒。单个页面失败时打印原因后继续，不中断全程，
最后打印各步骤的汇总（处理数 / 成功数 / 失败的 ID）。
"""

import time
import uuid

from notion_helper import NotionHelper, retry_on_rate_limit

# 写操作之间的停顿（秒）。
WRITE_INTERVAL_SECONDS = 0.3

# 保留的年份行“2026”。
YEAR_2026_HEX = "2db1435a95608158932cf0eb3ea213b6"
# 保留的播客行“蒋方舟·一寸”。
KEPT_PODCAST_HEX = "2b21435a956081529ce9e7d7875f2bc6"

# (1) 22 个重复单集行。
DUPLICATE_EPISODE_HEXS = [
    "2af1435a95608199ae73dd34c0c88957",
    "2aa1435a9560818a989bd087f061eb02",
    "2b81435a9560812f988df78b041c2cb8",
    "29b1435a956081ccac12d1ee830a0608",
    "2a21435a95608140b3fae3be52bd2c97",
    "2a11435a956081b7aba4f85f95d99ee6",
    "2981435a9560818c9c24e2941668662f",
    "2a21435a9560814b87d1d1d398e3345b",
    "2ac1435a9560815d93f2e3b1a1fe91ae",
    "3271435a9560817a88cec6c688fa96a2",
    "28f1435a95608113aaaafbbf7328c71a",
    "29a1435a956081a8b706ec010e4a509f",
    "2961435a956081f5aa44cc82033a5d5e",
    "2991435a956081849246cc4cc9e341c0",
    "2a31435a956081d08a0aea41b90cfe6f",
    "2a61435a95608184a226f5f47dfd432a",
    "2aa1435a956081258bbaca2cd420817e",
    "2b51435a956081eba3ced8896abc3cfe",
    "2b51435a956081b59fcecd9bb43ed589",
    "2b51435a9560814bbe22f19ba37b3f59",
    "2b51435a956081ef94f0f750cf06db5b",
    "3281435a95608125b787dc24d87b59aa",
]

# (2) 1 个重复脑图页（标题“♫1.替我保管一些秘密”）。
DUPLICATE_MINDMAP_HEX = "3271435a95608191ac49cd259d0fb495"

# (3) 要改 Podcast 关联的 2 期单集，和随后要归档的重复播客行。
REPOINT_EPISODE_HEXS = [
    "2bf1435a956081a18a78cf1fd03d2ace",
    "33b1435a9560812c8703c330f9f7ee20",
]
DUPLICATE_PODCAST_HEX = "2b21435a956081dd8e77f439798a9e13"

# (4) 2 个已无单集链接的重复播客行。
ORPHAN_PODCAST_HEXS = [
    "29a1435a956081ccbe34caa81266818e",
    "2991435a956081198967cb1f424cdb4b",
]

# (5) 4 个重复日行和 1 个重复周行。
DUPLICATE_DAY_HEXS = [
    "3271435a956081d5a233fe2febe0572e",
    "2aa1435a9560818a8d68cd3a0603d0b1",
    "2aa1435a956081b989abff11920e5b8a",
    "2aa1435a9560819ca907d7bcc9947e3c",
]
DUPLICATE_WEEK_HEX = "2ae1435a9560816496b6cf9666e8cdc1"

# (6) 46 期要改“年”关联的单集。
REPOINT_YEAR_EPISODE_HEXS = [
    "4141435a956081f28914c6dc0f3c1941",
    "4141435a956081628886c82a55efe88d",
    "4141435a956081aaaa3ad1a762d568a3",
    "4141435a95608101b075ce0f1f0770f9",
    "4141435a956081e19233c276f3bf1468",
    "4141435a9560818dbed8c738d2e3a439",
    "4141435a956081af9e4be34dd64a68eb",
    "4141435a95608181936ae30faff704e6",
    "4141435a956081e99cccffc5ca57c71f",
    "4141435a95608104aab9fcf69948e462",
    "4141435a9560810abf42e996ec235014",
    "4141435a956081f988f5d3a65ef2c096",
    "4141435a956081849c55df6596082055",
    "4141435a9560814d8fcfcb7c7a7402e1",
    "4141435a9560813ca397ff5bb5d22210",
    "4141435a956081d29954ca1b857fa98f",
    "4141435a9560814588c4c0d08be40c64",
    "4141435a9560811488aed07420dbf4bc",
    "4141435a956081de9738e090fe4fe68c",
    "4141435a956081a8a8e9ed2e74f885ea",
    "4141435a95608185929ff2a52be01f1f",
    "4141435a956081b8bf80f7d35281a148",
    "4141435a956081ffbfa3d1a04d529ad9",
    "4141435a956081539406c782caefb802",
    "4141435a95608190b678fc3c6a1c362f",
    "4141435a9560811681f4e1fc9e9d6e9c",
    "4141435a9560818f8c51fdf371fe087e",
    "4141435a95608126a67cfd87e9af8e44",
    "4141435a9560819eb15cc7cdf0b07a48",
    "4141435a956081468e4ccb77c52bebed",
    "4141435a956081509d08c78e842e5548",
    "4141435a9560811eb3f9eb3a2d21563b",
    "4141435a95608130bbabe9718f60d36a",
    "4141435a956081e39561f4849830e189",
    "4141435a95608132a414ee37b4ead60e",
    "4141435a95608178b866e1c6a78e1a4c",
    "4141435a9560815b8417d236dcd9776e",
    "4141435a956081ccbf81d4cebf5929d4",
    "4141435a9560816a808bdcb435c13f34",
    "4141435a95608119941fd1f68fc0f1ae",
    "4141435a95608101b9f3e1f2f5e194af",
    "4141435a9560811fa5dbe6c361910f4c",
    "4141435a956081adbe5cec46d5942239",
    "4141435a956081f19a9dcf75d95ab70f",
    "3b91435a956081c1a766f7504630c344",
    "3b81435a956081f5a4fbc9fca8695acd",
]

# (7) 3 个要改“年”关联的月份行，和随后要归档的年份行。
REPOINT_YEAR_MONTH_HEXS = [
    "4141435a95608107b6c7d1e85fc832d6",
    "4141435a956081f28654c066695be80a",
    "4141435a956081d2a468c7f6bebdedb3",
]
DUPLICATE_YEAR_HEXS = [
    "4141435a956081428555d8eac93a936c",
    "4141435a95608109bbd0ca664b85fe92",
]

# (9) 5 个 2023 年月份行：(页面 ID, 收听时长/秒, 收听天数)。
MONTH_BACKFILL = [
    ("28a1435a95608159a7b8f2e1d7cfbf2c", 63488, 9),
    ("28a1435a956081f1a3c1d12c0e2e9d7", 46881, 11),
    ("28a1435a9560816cb85dc074b10d04bc", 41950, 6),
    ("28a1435a956081b4a3f4e060e288650c", 34893, 8),
    ("28a1435a956081d58960c9566559ebb1", 1801, 1),
]


def normalize_id(hex_id):
    """把 32 位无横线 hex 规范化成带横线的标准 UUID 字符串。"""
    return str(uuid.UUID(hex_id))


def relation_value(target_hex):
    """关联属性的值，如 {"年": {"relation": [{"id": "<标准 UUID>"}]}}。"""
    return {"relation": [{"id": normalize_id(target_hex)}]}


class StepStats:
    """记录一个步骤的处理 / 成功 / 失败情况。"""

    def __init__(self, name):
        self.name = name
        self.total = 0
        self.succeeded = 0
        self.failed = []

    def record_success(self):
        self.total += 1
        self.succeeded += 1

    def record_failure(self, label, error):
        self.total += 1
        self.failed.append(label)
        print(f"  [失败] {label}: {error}")

    def report(self):
        print(f"{self.name}: 共 {self.total} 个，成功 {self.succeeded} 个，失败 {len(self.failed)} 个")
        if self.failed:
            print(f"  失败的 ID: {', '.join(self.failed)}")


NOTION = None


@retry_on_rate_limit
def archive_page(page_id):
    """归档页面（移到回收站，可恢复）。"""
    return NOTION.client.pages.update(page_id=page_id, archived=True)


@retry_on_rate_limit
def update_page_properties(page_id, properties):
    return NOTION.client.pages.update(page_id=page_id, properties=properties)


@retry_on_rate_limit
def create_page(parent, properties):
    return NOTION.client.pages.create(parent=parent, properties=properties)


def run_archive(stats, hex_id, note=""):
    try:
        archive_page(normalize_id(hex_id))
        stats.record_success()
        print(f"  [完成] 归档 {hex_id} {note}".rstrip())
    except Exception as exc:
        stats.record_failure(hex_id, exc)
    time.sleep(WRITE_INTERVAL_SECONDS)


def run_update(stats, hex_id, properties, note=""):
    try:
        update_page_properties(normalize_id(hex_id), properties)
        stats.record_success()
        print(f"  [完成] 更新 {hex_id} {note}".rstrip())
    except Exception as exc:
        stats.record_failure(hex_id, exc)
    time.sleep(WRITE_INTERVAL_SECONDS)


def main():
    global NOTION
    NOTION = NotionHelper()
    print("迁移开始：使用仓库现有的 NotionHelper（环境变量 NOTION_TOKEN / NOTION_PAGE）。")
    stats_list = []

    # (1) 归档 22 个重复单集行。
    print("步骤 (1)：归档 22 个重复单集行……")
    stats = StepStats("步骤 (1) 归档重复单集行")
    for hex_id in DUPLICATE_EPISODE_HEXS:
        run_archive(stats, hex_id)
    stats_list.append(stats)

    # (2) 归档 1 个重复脑图页。
    print("步骤 (2)：归档 1 个重复脑图页……")
    stats = StepStats("步骤 (2) 归档重复脑图页")
    run_archive(stats, DUPLICATE_MINDMAP_HEX, "♫1.替我保管一些秘密")
    stats_list.append(stats)

    # (3) 先改关联，再归档重复播客行。
    print("步骤 (3)：2 期单集的 Podcast 关联改到保留的播客行，再归档重复播客行……")
    stats = StepStats("步骤 (3) 改 Podcast 关联并归档重复播客行")
    for hex_id in REPOINT_EPISODE_HEXS:
        run_update(stats, hex_id, {"Podcast": relation_value(KEPT_PODCAST_HEX)}, "Podcast 关联改到保留的播客行")
    run_archive(stats, DUPLICATE_PODCAST_HEX)
    stats_list.append(stats)

    # (4) 归档 2 个已无单集链接的重复播客行（必须在步骤 (1) 之后）。
    print("步骤 (4)：归档 2 个已无单集链接的重复播客行……")
    stats = StepStats("步骤 (4) 归档无单集链接的重复播客行")
    for hex_id in ORPHAN_PODCAST_HEXS:
        run_archive(stats, hex_id)
    stats_list.append(stats)

    # (5) 归档 4 个重复日行和 1 个重复周行。
    print("步骤 (5)：归档 4 个重复日行和 1 个重复周行……")
    stats = StepStats("步骤 (5) 归档重复日行和重复周行")
    for hex_id in DUPLICATE_DAY_HEXS:
        run_archive(stats, hex_id)
    run_archive(stats, DUPLICATE_WEEK_HEX, "2025年第47周")
    stats_list.append(stats)

    # (6) 46 期单集的“年”关联改到年份行“2026”。
    print("步骤 (6)：46 期单集的“年”关联改到年份行“2026”……")
    stats = StepStats("步骤 (6) 单集“年”关联改到 2026")
    for hex_id in REPOINT_YEAR_EPISODE_HEXS:
        run_update(stats, hex_id, {"年": relation_value(YEAR_2026_HEX)}, "“年”关联改到 2026")
    stats_list.append(stats)

    # (7) 3 个月份行的“年”关联改到年份行“2026”，再归档两个年份行。
    print("步骤 (7)：3 个月份行的“年”关联改到年份行“2026”，再归档年份行“2026年”和“2025年”……")
    stats = StepStats("步骤 (7) 月份“年”关联改到 2026 并归档多余年份行")
    for hex_id in REPOINT_YEAR_MONTH_HEXS:
        run_update(stats, hex_id, {"年": relation_value(YEAR_2026_HEX)}, "“年”关联改到 2026")
    for hex_id in DUPLICATE_YEAR_HEXS:
        run_archive(stats, hex_id)
    stats_list.append(stats)

    # (8) 新建月份行“2026年06月”。
    print("步骤 (8)：新建月份行“2026年06月”……")
    stats = StepStats("步骤 (8) 新建月份行 2026年06月")
    try:
        response = create_page(
            parent={"type": "data_source_id", "data_source_id": NOTION.month_database_id},
            properties={
                "标题": {"title": [{"text": {"content": "2026年06月"}}]},
                "日期": {"date": {"start": "2026-06-01", "end": "2026-06-30"}},
                "年": relation_value(YEAR_2026_HEX),
            },
        )
        stats.record_success()
        print(f"  [完成] 新建月份行 2026年06月，新页面 ID: {response.get('id')}")
    except Exception as exc:
        stats.record_failure("2026年06月", exc)
    time.sleep(WRITE_INTERVAL_SECONDS)
    stats_list.append(stats)

    # (9) 补填 5 个 2023 年月份行的收听时长和收听天数。
    print("步骤 (9)：补填 5 个 2023 年月份行的收听时长和收听天数……")
    stats = StepStats("步骤 (9) 补填 2023 年月份行")
    for hex_id, seconds, days in MONTH_BACKFILL:
        run_update(
            stats,
            hex_id,
            {"收听时长": {"number": seconds}, "收听天数": {"number": days}},
            f"收听时长 {seconds} 秒、收听天数 {days} 天",
        )
    stats_list.append(stats)

    print("========== 迁移汇总 ==========")
    for stats in stats_list:
        stats.report()
    print("迁移完成。")


if __name__ == "__main__":
    main()
