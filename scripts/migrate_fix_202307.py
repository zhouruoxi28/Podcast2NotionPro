# -*- coding: utf-8 -*-
"""
一次性补填脚本：只补 2023年07月 这一行。

背景：一次性去重迁移（one-time dedupe migration）中，2023年07月
月汇总行的页面 ID 在脚本里少写了一位，未能补上数值。
本脚本只做一件事：把页面 28a1435a956081f1a3c1d12c0e2e9d7d
（2023年07月）的“收听时长”写成 46881（秒）、“收听天数”写成 11。

用法（GitHub Actions，见 .github/workflows/migrate-fix.yml）：
    python scripts/migrate_fix_202307.py

token 只从环境变量 NOTION_TOKEN / NOTION_PAGE 读取，
脚本里不写任何 token。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notion_helper import NotionHelper, retry_on_rate_limit

# 2023年07月 月汇总行的页面 ID（32 位，结尾 ...9d7d）
PAGE_ID = "28a1435a956081f1a3c1d12c0e2e9d7d"
LISTEN_SECONDS = 46881
LISTEN_DAYS = 11


@retry_on_rate_limit
def update_month_row(helper, page_id, properties):
    helper.update_page(page_id, properties)


def main():
    if not os.getenv("NOTION_TOKEN") or not os.getenv("NOTION_PAGE"):
        print("失败：缺少环境变量 NOTION_TOKEN 或 NOTION_PAGE")
        return 1

    helper = NotionHelper()
    properties = {
        "收听时长": {"number": LISTEN_SECONDS},
        "收听天数": {"number": LISTEN_DAYS},
    }
    try:
        update_month_row(helper, PAGE_ID, properties)
    except Exception as exc:
        print(f"失败：页面 {PAGE_ID}（2023年07月）补填失败：{exc}")
        return 1

    print(
        f"成功：页面 {PAGE_ID}（2023年07月）已补填，"
        f"收听时长={LISTEN_SECONDS} 秒，收听天数={LISTEN_DAYS} 天。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
