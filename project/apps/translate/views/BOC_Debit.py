# project/apps/translate/views/BOC_Debit.py
import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import pdfplumber

from project.utils.exceptions import DecryptionError
from project.apps.translate.services.init.strategies.boc_debit_init_strategy import BOCDebitInitStrategy
from project.apps.translate.utils import ASSETS_OTHER
from project.apps.translate.services.mapping_provider import extract_account_string
# from project.apps.translate.utils import InitStrategy, IgnoreData, BILL_BOC_DEBIT

logger = logging.getLogger(__name__)

# boc_debit_sourcefile_identifier = "中国银行交易流水明细清单"
# boc_debit_csvfile_identifier = "中国银行储蓄卡账单明细"


# class BocDebitInitStrategy(InitStrategy):
#     def init(self, bill, **kwargs):
#         import itertools

#         card = kwargs.get('card_number', None)
#         bill = itertools.islice(bill, 1, None)
#         records = []
#         try:
#             for row in bill:
#                 record = {
#                     'transaction_time': row[0] + " " + row[1],  # 交易时间
#                     'transaction_category': row[5],  # 交易类型
#                     'counterparty': row[5] if "-------------------" in row[9] else row[9],  # 交易对方
#                     'commodity': row[2],  # 商品
#                     'transaction_type': "支出" if "-" in row[3] else "收入" ,  # 收支类型（收入/支出/不计收支）
#                     'amount': row[3].replace("-", "") if "-" in row[3] else row[3],  # 金额
#                     'payment_method': "中国银行储蓄卡(" + card + ")",  # 支付方式
#                     'transaction_status': BILL_BOC_DEBIT + " - 交易成功",  # 交易状态
#                     'notes': row[8],  # 备注
#                     'bill_identifier': BILL_BOC_DEBIT,  # 账单类型
#                     'balance': row[4],
#                     'card_number': row[10],
#                     'counterparty_bank': row[11],
#                 }
#                 records.append(record)
#         except UnicodeDecodeError as e:
#             logging.error("Unicode decode error at row=%s: %s", row, e)
#         except Exception as e:
#             logging.error("Unexpected error: %s", e)

#         return records


# def boc_debit_ignore(self, data, boc_debit_ignore):
#     if data['bill_identifier'] == BILL_BOC_DEBIT and ("支付宝" in data['counterparty'] or "财付通" in data['counterparty']):
#         return boc_debit_ignore == "true"


def boc_debit_pdf_convert_to_string(file, password):
    """接收PDF文件，返回字符串

    Args:
        file(_type_): PDF文件
        password (str): PDF文件的密码，如果文件受保护

    Returns:
        string: 以List形式返回
    """
    with pdfplumber.open(file, password=password) as pdf:
        content = []
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables is None:
                raise DecryptionError("PDF解密失败：无法提取内容，请提供密码后重试")
            content += tables
    return content


def boc_debit_string_convert_to_csv(data, card_number):
    """接收字符串，返回CSV格式文件

    Args:
        data (string): _description_
        card_number (string): 储蓄卡/信用卡 完整的卡号

    Returns:
        csv: _description_
    """
    # output_lines = [boc_debit_csvfile_identifier + " 卡号: " + card_number]
    output_lines = [BOCDebitInitStrategy.HEADER_MARKER + " 卡号: " + card_number]

    # 打印头部行并添加至结果列表
    header = data[0][0]
    output_lines.append(",".join(header))

    # 对每个block中的交易记录进行处理
    for block in data:
        transactions = block[1:]
        for transaction in transactions:
            # 处理数据: 移除数字中的逗号, 替换附言中的换行符
            processed_transaction = [
                field.replace(',', '').replace('\n', '') if isinstance(field, str) else field
                for field in transaction
            ]
            # 添加处理后的单行交易记录至结果列表
            output_lines.append(",".join(processed_transaction))

    # 将最终的结果列表转换为一个字符串
    final_output = "\n".join(output_lines)

    return final_output


def boc_debit_get_uuid(data):
    return data['uuid']


def boc_debit_get_status(data):
    return data['transaction_status']


def boc_debit_get_amount(data):
    return "{:.2f}".format(float(data['amount']))


def boc_debit_get_note(data):
    if data['transaction_category'] == "跨行转账":
        return data['counterparty_bank']
    else:
        return data['notes']


def boc_debit_mapping_match_blob(data: dict) -> str:
    """拼接用于映射关键字匹配的文本。对方卡号/对手行信息常在附言、对手行等列，而非对方户名列。"""
    return "|".join(
        str(x or "")
        for x in (
            data.get("counterparty"),
            data.get("commodity"),
            data.get("notes"),
            data.get("counterparty_bank"),
            data.get("card_number"),
        )
    )


def boc_debit_filter_keys_in_blob(key_list: List[str], data: dict) -> List[str]:
    """中行账单：在附言/对手行等拼接文本中匹配映射关键字。"""
    blob = boc_debit_mapping_match_blob(data)
    return [k for k in key_list if k and k in blob]


class BocIncomePeerResult(NamedTuple):
    peer_account: str
    selected_key: str
    expense_candidates_with_score: List[Dict[str, Any]]
    mapping_tags: List[Any]


def _is_bill_own_card(mapping, payment_method: str) -> bool:
    """判断映射是否就是这笔账单所属的卡。"""
    from project.apps.external_accounts.hints import (
        infer_external_account_hint,
        infer_hint_from_account_path,
    )
    from project.apps.translate.services.mapping_provider import extract_account_string

    if not mapping.assets:
        return False
    own = infer_external_account_hint(payment_method, bill='BOC_Debit')
    mapping_hint = infer_hint_from_account_path(extract_account_string(mapping.assets))
    if not own.institution_code or not mapping_hint.institution_code:
        return False
    return (
        own.institution_code,
        own.kind,
        own.tail,
    ) == (
        mapping_hint.institution_code,
        mapping_hint.kind,
        mapping_hint.tail,
    )


def boc_debit_try_income_peer_asset(
    data: dict,
    asset_mappings: List,
    model: str,
    similarity_model: Any,
) -> Optional[BocIncomePeerResult]:
    """收入流水：若附言/对手行等命中非本卡资产映射，对端为资产账户（同名划转），否则返回 None。"""
    blob = boc_debit_mapping_match_blob(data)
    pm = data.get("payment_method") or ""
    conflict_candidates: List[Tuple[int, Any]] = []
    for m in asset_mappings or []:
        if not m.key or m.key not in blob:
            continue
        if _is_bill_own_card(m, pm):
            continue
        if not m.assets:
            continue
        pri = extract_account_string(m.assets).count(":") * 100
        conflict_candidates.append((pri, m))
    if not conflict_candidates:
        return None
    max_order = max(p for p, _ in conflict_candidates)
    winners = [m for p, m in conflict_candidates if p == max_order]
    best = winners[0]
    if len(winners) > 1 and model != "None":
        tx = (
            f"类型：{data['transaction_category']} 商户：{data['counterparty']} "
            f"商品：{data['commodity']} 金额：{data['amount']}元"
        )
        sim_result = similarity_model.calculate_similarity(tx, [m.key for m in winners])
        bk = sim_result["best_match"]
        best = next(m for m in winners if m.key == bk)
        scores = sim_result["scores"]
        expense_candidates_with_score = [
            {"key": m.key, "score": round(scores.get(m.key, 0), 4)} for m in winners
        ]
    else:
        if len(winners) > 1:
            best = max(winners, key=lambda mm: len(mm.key))
        expense_candidates_with_score = [{"key": m.key, "score": 1.0} for m in winners]

    try:
        mapping_tags = list(best.tags.filter(enable=True))
    except Exception as e:
        logger.error(f"加载资产映射标签失败: {str(e)}")
        mapping_tags = []

    acc = extract_account_string(best.assets)
    return BocIncomePeerResult(
        peer_account=acc,
        selected_key=best.key,
        expense_candidates_with_score=expense_candidates_with_score,
        mapping_tags=mapping_tags,
    )


def boc_debit_init_key(data):
    return data['payment_method']


def boc_debit_get_account(self, ownerid):
    from project.apps.translate.services.account_resolution import resolve_account_from_text

    result = resolve_account_from_text(ownerid, self.key, bill=self.bill)
    return result.account if result.ok else ASSETS_OTHER


def boc_debit_get_balance(data):
    return data['balance']


def boc_debit_get_card_number(content):
    """
    从账单文件中获取中国银行卡号
    """
    boc_debit_card_number = re.search(r'\d{19}', content).group()
    return boc_debit_card_number

# IgnoreData.boc_debit_ignore = boc_debit_ignore