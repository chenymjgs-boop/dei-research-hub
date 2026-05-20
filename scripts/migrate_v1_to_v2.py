"""v1 → v2 schema migration · idempotent.

Migrates BOTH:
1. The local SQLite `items` table (rename + add columns)
2. The Feishu Bitable schema (rename + add fields)

Run repeatedly is safe — every step checks current state before applying changes.

Usage:
    python scripts/migrate_v1_to_v2.py             # do everything
    python scripts/migrate_v1_to_v2.py --sqlite    # only SQLite
    python scripts/migrate_v1_to_v2.py --feishu    # only Feishu Bitable
    python scripts/migrate_v1_to_v2.py --dry-run   # show what would change
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import requests

# Make project root importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import SETTINGS  # noqa: E402

BASE = "https://open.feishu.cn/open-apis"

# ----------------------------------------------------------------------------
# SQLite migration
# ----------------------------------------------------------------------------

# Columns to RENAME (v1 name -> v2 name).  SQLite supports `ALTER TABLE … RENAME COLUMN`
# since 3.25 (2018).
RENAME_COLUMNS = [
    ("china_implication", "implication_mnc_china"),
    ("relevance_score",   "overall_relevance"),
]

# Columns to ADD if missing.  All nullable / default-0 so existing rows are fine.
ADD_COLUMNS = [
    ("title_zh",                   "TEXT"),
    ("source_subtype",             "TEXT"),
    ("is_competitor",              "INTEGER DEFAULT 0"),
    ("pillars_json",               "TEXT"),
    ("implication_esg_listing",    "TEXT"),
    ("implication_going_global",   "TEXT"),
    ("relevance_mnc_china",        "INTEGER"),
    ("relevance_esg_listing",      "INTEGER"),
    ("relevance_going_global",     "INTEGER"),
    ("competitor_intelligence",    "TEXT"),
    # v2.1
    ("stance",                     "TEXT"),
]


def migrate_sqlite(dry_run: bool = False) -> dict:
    """Apply rename + additive migrations to the items table. Idempotent."""
    db_path = SETTINGS.db_path
    if not Path(db_path).exists():
        print(f"  ℹ  No SQLite DB at {db_path} — nothing to migrate (will be created fresh on next run)")
        return {"renamed": [], "added": [], "skipped": []}

    actions: dict = {"renamed": [], "added": [], "skipped": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        print(f"  current items columns: {sorted(existing_cols)}")
        print()

        # 1. Renames
        for old_name, new_name in RENAME_COLUMNS:
            if new_name in existing_cols:
                actions["skipped"].append(f"rename {old_name}→{new_name} (already done)")
                continue
            if old_name not in existing_cols:
                actions["skipped"].append(f"rename {old_name}→{new_name} (old column missing — fresh DB)")
                continue
            sql = f"ALTER TABLE items RENAME COLUMN {old_name} TO {new_name}"
            print(f"  ▸ {sql}")
            if not dry_run:
                conn.execute(sql)
            actions["renamed"].append(f"{old_name} → {new_name}")

        # Refresh column set after renames
        existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}

        # 2. Additive columns
        for col_name, col_type in ADD_COLUMNS:
            if col_name in existing_cols:
                actions["skipped"].append(f"add {col_name} (already present)")
                continue
            sql = f"ALTER TABLE items ADD COLUMN {col_name} {col_type}"
            print(f"  ▸ {sql}")
            if not dry_run:
                conn.execute(sql)
            actions["added"].append(f"{col_name} ({col_type})")

        # 3. Index for new column
        if not dry_run:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_competitor ON items(is_competitor)")
        actions["skipped"].append("ensured idx_items_competitor")

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return actions


# ----------------------------------------------------------------------------
# Feishu Bitable migration
# ----------------------------------------------------------------------------

# Map v1 field name → v2 field name.  Field types do not change for renames.
BITABLE_RENAMES = [
    ("类别",         "来源类别"),
    ("地域",         "来源地域"),
    ("对中国的启示",   "对跨国在华客户的启示"),
    ("相关性",         "综合相关性"),
]

# Fields to ADD if missing.  Format: (name, feishu_type_int, property_dict_or_None)
# Feishu types: 1=text, 2=number, 3=single_select, 4=multi_select, 5=datetime, 15=url
BITABLE_ADDS = [
    ("中文标题",                  1,  None),
    ("板块",                     4,
        {"options": [{"name": "global"}, {"name": "mnc_china"}, {"name": "china_going_global"}]}),
    ("是否竞品",                  3,
        {"options": [{"name": "是"}, {"name": "否"}]}),
    ("来源子类型",                3,
        {"options": [
            {"name": "wechat_recruiting"},
            {"name": "wechat_media"},
            {"name": "wechat_thinktank"},
            {"name": "wechat_corporate"},
        ]}),
    ("对中国ESG/上市客户的启示",      1,  None),
    ("对中国企业出海客户的启示",      1,  None),
    ("在华跨国相关度",             2,  {"formatter": "0"}),
    ("ESG上市相关度",             2,  {"formatter": "0"}),
    ("出海相关度",                 2,  {"formatter": "0"}),
    ("竞品情报",                   1,  None),
    # v2.1
    ("立场",                       3,
        {"options": [
            {"name": "backlash"},
            {"name": "persist"},
            {"name": "controversy"},
            {"name": "mainstream"},
        ]}),
]

# Source category options need to be expanded for v2.  We update the existing
# 来源类别 (renamed from 类别) with the v2 option list.
SOURCE_CATEGORY_V2_OPTIONS = [
    "academic", "consulting", "international_org",
    "media", "regulator", "china_local", "wechat",
]


def _feishu_token() -> str:
    r = requests.post(
        f"{BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": SETTINGS.feishu_app_id, "app_secret": SETTINGS.feishu_app_secret},
        timeout=15,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {data}")
    return data["tenant_access_token"]


def migrate_feishu_bitable(dry_run: bool = False) -> dict:
    """Apply rename + additive migrations to the Feishu Bitable schema.

    Idempotent: each step checks current field set before applying.
    """
    if not SETTINGS.feishu_app_id or "xxxx" in SETTINGS.feishu_app_id.lower():
        print("  ℹ  Feishu credentials not configured — skipping Bitable migration")
        return {"renamed": [], "added": [], "skipped": []}

    token = _feishu_token()
    H = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    app = SETTINGS.feishu_bitable_app_token
    tbl = SETTINGS.feishu_bitable_table_id

    # Snapshot existing fields
    r = requests.get(f"{BASE}/bitable/v1/apps/{app}/tables/{tbl}/fields", headers=H, timeout=15)
    rd = r.json()
    if rd.get("code") != 0:
        raise RuntimeError(f"Cannot list fields: {rd}")
    fields = rd.get("data", {}).get("items", [])
    by_name = {f["field_name"]: f for f in fields}
    print(f"  current Bitable fields ({len(by_name)}): {sorted(by_name.keys())}")
    print()

    actions: dict = {"renamed": [], "added": [], "skipped": []}

    # 1. Renames (PUT /fields/{field_id} with new field_name)
    for old_name, new_name in BITABLE_RENAMES:
        if new_name in by_name:
            actions["skipped"].append(f"rename {old_name}→{new_name} (target already exists)")
            continue
        if old_name not in by_name:
            actions["skipped"].append(f"rename {old_name}→{new_name} (source missing — fresh table)")
            continue
        f = by_name[old_name]
        body = {"field_name": new_name, "type": f["type"]}
        if f.get("property"):
            body["property"] = f["property"]
        print(f"  ▸ rename {old_name} → {new_name}")
        if not dry_run:
            rr = requests.put(
                f"{BASE}/bitable/v1/apps/{app}/tables/{tbl}/fields/{f['field_id']}",
                headers=H,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=15,
            )
            d = rr.json()
            if d.get("code") != 0:
                raise RuntimeError(f"Rename {old_name}→{new_name} failed: {d}")
        actions["renamed"].append(f"{old_name} → {new_name}")

    # Refresh after renames
    if not dry_run:
        r = requests.get(f"{BASE}/bitable/v1/apps/{app}/tables/{tbl}/fields", headers=H, timeout=15)
        by_name = {f["field_name"]: f for f in r.json().get("data", {}).get("items", [])}

    # 2. Expand 来源类别 options if it now exists
    if "来源类别" in by_name:
        f = by_name["来源类别"]
        existing_opts = {o["name"] for o in (f.get("property") or {}).get("options", [])}
        target_opts = set(SOURCE_CATEGORY_V2_OPTIONS)
        missing = target_opts - existing_opts
        if missing:
            new_opts = list((f.get("property") or {}).get("options", []))
            for name in SOURCE_CATEGORY_V2_OPTIONS:
                if name not in existing_opts:
                    new_opts.append({"name": name})
            print(f"  ▸ expand 来源类别 options: + {sorted(missing)}")
            if not dry_run:
                rr = requests.put(
                    f"{BASE}/bitable/v1/apps/{app}/tables/{tbl}/fields/{f['field_id']}",
                    headers=H,
                    data=json.dumps({
                        "field_name": "来源类别",
                        "type": 3,
                        "property": {"options": new_opts},
                    }, ensure_ascii=False).encode("utf-8"),
                    timeout=15,
                )
                d = rr.json()
                if d.get("code") != 0:
                    print(f"    ⚠  expand options failed: {d.get('msg', '')[:120]}")
            actions["added"].append(f"来源类别 options: + {sorted(missing)}")

    # 3. Additive fields
    for name, ftype, prop in BITABLE_ADDS:
        if name in by_name:
            actions["skipped"].append(f"add {name} (already present)")
            continue
        body = {"field_name": name, "type": ftype}
        if prop:
            body["property"] = prop
        print(f"  ▸ add {name}  type={ftype}")
        if not dry_run:
            rr = requests.post(
                f"{BASE}/bitable/v1/apps/{app}/tables/{tbl}/fields",
                headers=H,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=15,
            )
            d = rr.json()
            if d.get("code") != 0:
                raise RuntimeError(f"Add {name} failed: {d}")
        actions["added"].append(name)

    return actions


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------

def print_summary(label: str, actions: dict) -> None:
    print(f"\n=== {label} ===")
    if actions["renamed"]:
        print("  Renamed:")
        for x in actions["renamed"]:
            print(f"    ✓ {x}")
    if actions["added"]:
        print("  Added:")
        for x in actions["added"]:
            print(f"    ✓ {x}")
    if actions["skipped"]:
        print("  Skipped (already in v2 state):")
        for x in actions["skipped"]:
            print(f"    · {x}")


def main() -> int:
    p = argparse.ArgumentParser(description="v1 → v2 schema migration")
    p.add_argument("--sqlite", action="store_true", help="Migrate SQLite only")
    p.add_argument("--feishu", action="store_true", help="Migrate Feishu Bitable only")
    p.add_argument("--dry-run", action="store_true", help="Show planned changes without applying")
    args = p.parse_args()

    # If neither --sqlite nor --feishu specified, run both
    do_sqlite = args.sqlite or not args.feishu
    do_feishu = args.feishu or not args.sqlite

    if args.dry_run:
        print(">>> DRY RUN — no changes will be applied <<<\n")

    if do_sqlite:
        print("Migrating SQLite...")
        sqlite_actions = migrate_sqlite(dry_run=args.dry_run)
        print_summary("SQLite", sqlite_actions)

    if do_feishu:
        print("\nMigrating Feishu Bitable...")
        try:
            feishu_actions = migrate_feishu_bitable(dry_run=args.dry_run)
            print_summary("Feishu Bitable", feishu_actions)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ Bitable migration failed: {e}")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
