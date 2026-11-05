# project/apps/account/management/commands/official_templates_loader.py
"""
从 project/fixtures/official_templates/ 目录加载官方模板 JSON 数据。
文件不存在或解析失败时返回 None，由调用方决定是否回退到内嵌数据。
"""
import json
import logging
from pathlib import Path
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

OFFICIAL_TEMPLATES_DIR_NAME = "official_templates"
ACCOUNT_JSON = "account.json"
MAPPING_EXPENSE_JSON = "mapping_expense.json"
MAPPING_INCOME_JSON = "mapping_income.json"
MAPPING_ASSETS_JSON = "mapping_assets.json"
TAG_JSON = "tag.json"

# BASE_DIR 在 settings 中为 backend 根目录，project 在其下
PROJECT_DIR = Path(settings.BASE_DIR) / "project"
FIXTURES_OFFICIAL_DIR = PROJECT_DIR / "fixtures" / OFFICIAL_TEMPLATES_DIR_NAME


def get_official_templates_dir() -> Path:
    """返回官方模板 JSON 所在目录路径。"""
    return FIXTURES_OFFICIAL_DIR


def _load_json_file(path: Path) -> Optional[dict]:
    """读取 JSON 文件，失败返回 None 并打日志。"""
    if not path.exists():
        logger.debug("官方模板文件不存在: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("加载官方模板 JSON 失败 %s: %s", path, e)
        return None


def _validate_account_item(item: dict) -> bool:
    """校验账户模板项必有 account_path。"""
    if not isinstance(item, dict):
        return False
    return isinstance(item.get("account_path"), str) and len(item["account_path"].strip()) > 0


def load_official_account_data() -> Optional[dict]:
    """
    从 account.json 加载官方账户模板数据。
    返回格式: {"name", "description", "version", "update_notes", "items": [{...}]}
    文件不存在或校验失败返回 None。
    """
    path = FIXTURES_OFFICIAL_DIR / ACCOUNT_JSON
    data = _load_json_file(path)
    if not data or "items" not in data:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    for i, item in enumerate(items):
        if not _validate_account_item(item):
            logger.warning("account.json items[%d] 缺少有效 account_path，已跳过", i)
            return None

    # Beancount 不允许只含根账户的路径；模板中的根账户只作分组，不落库
    items = [item for item in items if ':' in item["account_path"].strip()]
    if not items:
        return None

    data = dict(data)
    data["items"] = items
    return data


def _validate_mapping_tags(item: dict) -> bool:
    tags = item.get("tags")
    if tags is None:
        return True
    if not isinstance(tags, list):
        return False
    return all(isinstance(tag, str) and tag.strip() for tag in tags)


def normalize_mapping_tag_paths(item: dict) -> list[str]:
    """从映射模板项提取标签路径列表。"""
    tags = item.get("tags")
    if not tags:
        return []
    if not isinstance(tags, list):
        return []
    return [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]


def _validate_mapping_expense_item(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and len(item["key"].strip()) > 0
        and _validate_mapping_tags(item)
    )


def _validate_mapping_income_item(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and len(item["key"].strip()) > 0
        and isinstance(item.get("account"), str)
        and len(item["account"].strip()) > 0
    )


def _validate_mapping_assets_item(item: dict) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and len(item["key"].strip()) > 0
        and isinstance(item.get("full"), str)
        and isinstance(item.get("account"), str)
        and len(item["account"].strip()) > 0
    )


def load_official_mapping_data(template_type: str) -> Optional[dict]:
    """
    从 mapping_<type>.json 加载官方映射模板数据。
    template_type 为 'expense' | 'income' | 'assets'。
    返回格式: {"name", "description", "version", "update_notes", "items": [{...}]}
    文件不存在或校验失败返回 None。
    """
    filename_map = {
        "expense": MAPPING_EXPENSE_JSON,
        "income": MAPPING_INCOME_JSON,
        "assets": MAPPING_ASSETS_JSON,
    }
    validator_map = {
        "expense": _validate_mapping_expense_item,
        "income": _validate_mapping_income_item,
        "assets": _validate_mapping_assets_item,
    }
    if template_type not in filename_map:
        return None
    path = FIXTURES_OFFICIAL_DIR / filename_map[template_type]
    data = _load_json_file(path)
    if not data or "items" not in data:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    validate = validator_map[template_type]
    for i, item in enumerate(items):
        if not validate(item):
            logger.warning(
                "mapping_%s.json items[%d] 校验失败，已跳过",
                template_type,
                i,
            )
            return None
    return data


def _validate_tag_item(item: dict) -> bool:
    return isinstance(item, dict) and isinstance(item.get("tag_path"), str) and len(item["tag_path"].strip()) > 0


def load_official_tag_data() -> Optional[dict]:
    """
    从 tag.json 加载官方标签模板数据。
    返回格式: {"name", "description", "version", "update_notes", "items": [{...}]}
    文件不存在或校验失败返回 None。
    """
    path = FIXTURES_OFFICIAL_DIR / TAG_JSON
    data = _load_json_file(path)
    if not data or "items" not in data:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    for i, item in enumerate(items):
        if not _validate_tag_item(item):
            logger.warning("tag.json items[%d] 缺少有效 tag_path，已跳过", i)
            return None
    return data


def load_official_expense_tag_paths_by_key() -> dict[str, list[str]]:
    """从 mapping_expense.json 加载 key -> 标签路径列表。"""
    data = load_official_mapping_data('expense')
    if not data:
        return {}
    result: dict[str, list[str]] = {}
    for item in data.get('items', []):
        paths = normalize_mapping_tag_paths(item)
        if paths:
            result[item['key']] = paths
    return result


def resolve_expense_template_item_tag_paths(item, json_tag_map: dict[str, list[str]] | None = None) -> list[str]:
    """解析模板项的有效标签路径：优先 DB，为空时回退 JSON。"""
    db_paths = list(item.tag_paths or [])
    if db_paths:
        return db_paths
    if json_tag_map is None:
        json_tag_map = load_official_expense_tag_paths_by_key()
    return list(json_tag_map.get(item.key, []))


def load_all_official_templates_data() -> dict[str, Any]:
    """
    一次性加载所有官方模板 JSON（若存在）。
    返回: {"account": dict | None, "mapping_expense": dict | None, "mapping_income": dict | None, "mapping_assets": dict | None, "tag": dict | None}
    """
    return {
        "account": load_official_account_data(),
        "mapping_expense": load_official_mapping_data("expense"),
        "mapping_income": load_official_mapping_data("income"),
        "mapping_assets": load_official_mapping_data("assets"),
        "tag": load_official_tag_data(),
    }
