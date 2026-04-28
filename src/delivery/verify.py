"""Feishu setup verifier — incremental, friendly.

Run with: python -m src.delivery.verify

Checks (in order, each independent of later ones):
  1. App auth: FEISHU_APP_ID + FEISHU_APP_SECRET → can we get a tenant token?
  2. Chat list: list groups the bot is in → discover candidate FEISHU_CHAT_ID values
  3. Chat send: send a one-line test message to FEISHU_CHAT_ID
  4. Bitable schema: FEISHU_BITABLE_APP_TOKEN + FEISHU_BITABLE_TABLE_ID exist;
                     verify all required fields present with correct types.
  5. Bitable smoke insert: insert one fake row, then delete it.
  6. Docs folder (optional): FEISHU_DOC_FOLDER_TOKEN reachable.
"""
from __future__ import annotations

import sys
import time
from typing import Optional

import requests

from ..config import SETTINGS

BASE = "https://open.feishu.cn/open-apis"

OK = "✓"
FAIL = "✗"
SKIP = "○"


def _fmt(status: str, label: str, detail: str = "") -> str:
    return f"  {status}  {label}" + (f"   — {detail}" if detail else "")


def _auth() -> Optional[str]:
    """Return tenant token, or None if creds missing/invalid."""
    if not SETTINGS.feishu_app_id or "xxxx" in SETTINGS.feishu_app_id.lower():
        return None
    if not SETTINGS.feishu_app_secret or "xxxx" in SETTINGS.feishu_app_secret.lower():
        return None
    r = requests.post(
        f"{BASE}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": SETTINGS.feishu_app_id,
            "app_secret": SETTINGS.feishu_app_secret,
        },
        timeout=15,
    )
    data = r.json() if r.ok else {}
    if data.get("code") != 0:
        return None
    return data["tenant_access_token"]


def step_1_auth(token: Optional[str]) -> None:
    if not SETTINGS.feishu_app_id or "xxxx" in SETTINGS.feishu_app_id.lower():
        print(_fmt(SKIP, "Step 1 · App auth", "FEISHU_APP_ID 未填或仍是占位符"))
        return
    if token:
        print(_fmt(OK, "Step 1 · App auth", f"App ID {SETTINGS.feishu_app_id[:10]}… 通过"))
    else:
        print(_fmt(FAIL, "Step 1 · App auth", "凭据无效 / 应用未发布 / 网络异常"))


def step_2_chats(token: Optional[str]) -> None:
    if not token:
        print(_fmt(SKIP, "Step 2 · Chat 列表", "需先通过 Step 1"))
        return
    r = requests.get(
        f"{BASE}/im/v1/chats",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = r.json() if r.ok else {}
    items = (data.get("data") or {}).get("items") or []
    if not items:
        print(
            _fmt(
                FAIL,
                "Step 2 · Chat 列表",
                "机器人不在任何群里。在飞书群里 @机器人 把它加进来后重试",
            )
        )
        return
    print(_fmt(OK, "Step 2 · Chat 列表", f"机器人在 {len(items)} 个群里："))
    for it in items[:10]:
        chat_id = it.get("chat_id", "")
        name = it.get("name") or "(私聊)"
        marker = "  ← 已配置" if chat_id == SETTINGS.feishu_chat_id else ""
        print(f"      {chat_id}  ·  {name}{marker}")
    if not SETTINGS.feishu_chat_id or "xxxx" in SETTINGS.feishu_chat_id.lower():
        print(
            "      → 请把上面想要推送的群的 chat_id 填到 .env 的 FEISHU_CHAT_ID"
        )


def step_3_chat_send(token: Optional[str]) -> None:
    if not token:
        print(_fmt(SKIP, "Step 3 · 群消息发送", "需先通过 Step 1"))
        return
    if not SETTINGS.feishu_chat_id or "xxxx" in SETTINGS.feishu_chat_id.lower():
        print(_fmt(SKIP, "Step 3 · 群消息发送", "FEISHU_CHAT_ID 未填"))
        return
    payload = {
        "receive_id": SETTINGS.feishu_chat_id,
        "msg_type": "text",
        "content": '{"text":"DEI 研究助手 · 配置验证测试消息（可忽略）"}',
    }
    r = requests.post(
        f"{BASE}/im/v1/messages?receive_id_type=chat_id",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=payload["content"]
        and __import__("json").dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=15,
    )
    data = r.json() if r.ok else {}
    if data.get("code") == 0:
        print(_fmt(OK, "Step 3 · 群消息发送", "测试消息已发出，请在群里查看"))
    else:
        msg = data.get("msg") or r.text[:120]
        print(_fmt(FAIL, "Step 3 · 群消息发送", msg))


# Required Bitable schema. Field name → category (text/number/...)
# These match what feishu.py's push_to_bitable writes.
REQUIRED_FIELDS = {
    "标题": ("text", 1),
    "链接": ("url", 15),
    "来源": ("text", 1),
    "类别": ("single_select", 3),
    "地域": ("single_select", 3),
    "发布日期": ("datetime", 5),
    "收录日期": ("datetime", 5),
    "英文摘要": ("text", 1),
    "中文摘要": ("text", 1),
    "关键要点": ("text", 1),
    "对中国的启示": ("text", 1),
    "话题": ("multi_select", 4),
    "行业": ("multi_select", 4),
    "证据类型": ("single_select", 3),
    "严谨度": ("number", 2),
    "相关性": ("number", 2),
}


def step_4_bitable_schema(token: Optional[str]) -> None:
    if not token:
        print(_fmt(SKIP, "Step 4 · Bitable schema", "需先通过 Step 1"))
        return
    app_token = SETTINGS.feishu_bitable_app_token
    table_id = SETTINGS.feishu_bitable_table_id
    if not app_token or "xxxx" in app_token.lower():
        print(_fmt(SKIP, "Step 4 · Bitable schema", "FEISHU_BITABLE_APP_TOKEN 未填"))
        return
    if not table_id or "xxxx" in table_id.lower():
        print(_fmt(SKIP, "Step 4 · Bitable schema", "FEISHU_BITABLE_TABLE_ID 未填"))
        return

    r = requests.get(
        f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    data = r.json() if r.ok else {}
    if data.get("code") != 0:
        msg = data.get("msg") or r.text[:120]
        print(_fmt(FAIL, "Step 4 · Bitable schema", f"无法读取表结构：{msg}"))
        return
    items = (data.get("data") or {}).get("items") or []
    have = {f["field_name"]: f["type"] for f in items}

    missing = []
    typed_wrong = []
    for name, (label, expected_type) in REQUIRED_FIELDS.items():
        if name not in have:
            missing.append(name)
        elif have[name] != expected_type:
            typed_wrong.append(f"{name}（应为 {label}/{expected_type}，实际 type={have[name]}）")

    if not missing and not typed_wrong:
        print(
            _fmt(
                OK,
                "Step 4 · Bitable schema",
                f"全部 {len(REQUIRED_FIELDS)} 个字段就位",
            )
        )
        return
    print(_fmt(FAIL, "Step 4 · Bitable schema", "字段缺失或类型不对："))
    for n in missing:
        kind, t = REQUIRED_FIELDS[n]
        print(f"      缺失：{n}（应为 {kind}）")
    for n in typed_wrong:
        print(f"      类型错：{n}")


def step_5_bitable_insert(token: Optional[str]) -> None:
    if not token:
        print(_fmt(SKIP, "Step 5 · Bitable 写入测试", "需先通过 Step 1"))
        return
    app_token = SETTINGS.feishu_bitable_app_token
    table_id = SETTINGS.feishu_bitable_table_id
    if (
        not app_token or "xxxx" in app_token.lower()
        or not table_id or "xxxx" in table_id.lower()
    ):
        print(_fmt(SKIP, "Step 5 · Bitable 写入测试", "Bitable 凭据未填"))
        return

    import json
    now_ms = int(time.time() * 1000)
    test_record = {
        "fields": {
            "标题": "[VERIFY] 测试条目 · 可删除",
            "链接": {"link": "https://example.com", "text": "测试"},
            "来源": "verify-script",
            "类别": "academic",
            "地域": "global",
            "英文摘要": "Test row.",
            "中文摘要": "本条用于配置验证，可手动删除。",
            "关键要点": "• 第一点\n• 第二点",
            "对中国的启示": "测试用",
            "话题": ["性别平等"],
            "行业": ["通用"],
            "证据类型": "案例分析",
            "严谨度": 1,
            "相关性": 1,
            "发布日期": now_ms,
            "收录日期": now_ms,
        }
    }
    r = requests.post(
        f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(test_record, ensure_ascii=False).encode("utf-8"),
        timeout=20,
    )
    data = r.json() if r.ok else {}
    if data.get("code") != 0:
        print(
            _fmt(
                FAIL,
                "Step 5 · Bitable 写入测试",
                data.get("msg") or r.text[:200],
            )
        )
        return
    record_id = (data.get("data") or {}).get("record", {}).get("record_id")
    print(
        _fmt(
            OK,
            "Step 5 · Bitable 写入测试",
            f"测试行已写入（record_id={record_id}），尝试自动清理…",
        )
    )

    # Cleanup
    if record_id:
        rd = requests.delete(
            f"{BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if rd.ok and rd.json().get("code") == 0:
            print("      自动清理成功")
        else:
            print(f"      自动清理失败，请手动删除标题为 [VERIFY] 的那一行")


def step_6_doc_folder(token: Optional[str]) -> None:
    if not SETTINGS.feishu_doc_folder_token:
        print(_fmt(SKIP, "Step 6 · 云文档（可选）", "FEISHU_DOC_FOLDER_TOKEN 未填，跳过"))
        return
    if not token:
        print(_fmt(SKIP, "Step 6 · 云文档", "需先通过 Step 1"))
        return
    print(
        _fmt(
            OK,
            "Step 6 · 云文档",
            f"folder_token 已配置（{SETTINGS.feishu_doc_folder_token[:10]}…）；首次创建文档时再实际验证",
        )
    )


def main() -> int:
    print("=" * 70)
    print("飞书集成验证")
    print("=" * 70)
    token = _auth()
    step_1_auth(token)
    print()
    step_2_chats(token)
    print()
    step_3_chat_send(token)
    print()
    step_4_bitable_schema(token)
    print()
    step_5_bitable_insert(token)
    print()
    step_6_doc_folder(token)
    print()
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
