import re
import pdfplumber
# import logging

from project.utils.exceptions import DecryptionError
from project.apps.maps.models import Assets
from project.apps.translate.services.init.strategies.icbc_debit_init_strategy import ICBCDebitInitStrategy
from project.apps.translate.services.mapping_provider import extract_account_string
from project.apps.translate.utils import ASSETS_OTHER
# from project.apps.translate.utils import InitStrategy, BILL_ICBC_DEBIT

# icbc_debit_sourcefile_identifier = "中国工商银行借记账户历史明细"
# icbc_debit_csvfile_identifier = "中国工商银行储蓄卡账单明细"


# class IcbcDebitInitStrategy(InitStrategy):
#     def init(self, bill, **kwargs):
#         import itertools
#         card = kwargs.get('card_number', None)
#         bill = itertools.islice(bill, 1, None)
#         records = []
#         try:
#             for row in bill:
#                 record = {
#                     'transaction_time': row[0],  # 交易时间
#                     'transaction_category': row[6],  # 交易类型
#                     'counterparty': row[10],  # 交易对方
#                     'commodity': row[4],  # 商品
#                     'transaction_type': "支出" if "-" in row[8] else "收入",  # 收支类型（收入/支出/不计收支）
#                     'amount': row[8].replace("+", "").replace("-", ""),  # 金额
#                     'payment_method': "中国工商银行储蓄卡(" + card + ")",  # 支付方式
#                     'transaction_status': BILL_ICBC_DEBIT + " - 交易成功",  # 交易状态
#                     'notes': row[6],  # 备注
#                     'bill_identifier': BILL_ICBC_DEBIT,  # 账单类型
#                     'balance': row[9],
#                     'card_number': row[11],
#                 }
#                 records.append(record)
#         except UnicodeDecodeError as e:
#             logging.error("Unicode decode error at row=%s: %s", row, e)
#         except Exception as e:
#             logging.error("Unexpected error: %s", e)

#         return records


def text_with_specific_font(page, fontname, max_fontsize, min_fontsize):
    text = ""
    char_objects = page.chars  # 获取页面的字符对象列表
    for char_obj in char_objects:
        # char_obj['fontname'] == fontname  # 获取字体,截至2024-04-24已废弃
        if char_obj['size'] <= max_fontsize and char_obj['size'] >= min_fontsize:
            text += char_obj['text']
    return text


def icbc_debit_pdf_convert_to_csv(file, card_number, password):
    """接收字符串，返回CSV格式文件

    Args:
        data (string): _description_
        card_number (string): 储蓄卡/信用卡 完整的卡号

    Returns:
        csv: _description_
    """
    with pdfplumber.open(file, password=password) as pdf:
        num_pages = len(pdf.pages)
        header = ["交易日期,账号,储种,序号,币种,钞汇,摘要,地区,收入/支出金额,余额,对方户名,对方账号,渠道"]
        # output_lines = [icbc_debit_csvfile_identifier + " 卡号: " + card_number]
        output_lines = [ICBCDebitInitStrategy.HEADER_MARKER + " 卡号: " + card_number]
        output_lines.append(",".join(header))
        for page_num in range(num_pages):
            page = pdf.pages[page_num]
            specific_text = text_with_specific_font(page, "GSSDFL+SimHei", 7.0,0.900000000000034)  # 假设的字体和大小
            # 定义正则表达式
            pattern = re.compile(
                r'(\d{4}-\d{2}-\d{2})'    # 日期 yyyy-mm-dd
                r'(\d{2}:\d{2}:\d{2})'    # 时间 hh:mm:ss
                r'(\d+)'                   # 账号
                r'活期'                    # 储种 (固定为活期)
                r'(\d+)'                   # 序号
                r'人民币钞'                # 币种+钞汇 (固定为人民币钞)
                r'(.*?)'                   # 摘要 (任意字符，非贪婪)
                r'(\d{4})'                 # 地区 (4个数字)
                r'([-+]\d+,?\d*\.\d{2}|\d+\.\d+[+\-]\d+\.\d{2}|\d+\.\d{2})' # 收入/支出金额(考虑正负和逗号)
                r'(\d+,?\d*\.\d{2})'         # 余额 (考虑逗号)
                r'(（空）|[^0-9]+)'         # 对方户名 (非数字序列或（空）)
                # r'(（空）|\d+)'            # 对方账号 (数字或（空）)
                r'(（空）|\d+\*\*\*\*\d+|\d+)'            # 对方账号 (数字或（空）)
                r'([^0-9]+(?=本页支出)|[^0-9]+)' # 渠道 (非数字序列)
            )

            for match in re.finditer(pattern, specific_text):
                groups = match.groups()
                # 解构赋值，同时替换余额和金额中的逗号
                date, time, account, serial, summary, area, amount, balance, name, opponent_account, channel = groups
                amount = amount.replace(',', '')  # 去除金额中的逗号
                balance = balance.replace(',', '')  # 去除余额中的逗号
                # 拼接字符串
                csv_row = f"{date} {time},{account},活期,{serial},人民币,钞,{summary},{area},{amount},{balance},{name},{opponent_account},{channel}"
                output_lines.append(csv_row)
            final_output = "\n".join(output_lines)
    return final_output



def icbc_debit_get_status(data):
    """接收字符串，返回条目状态

    Args:
        data (string): _description_

    Returns:
        status: 各交易状态（支付成功、退款成功等）
    """
    return data['transaction_status']


def icbc_debit_get_amount(data):
    """接收字符串，返回金额

    Args:
        data (string): _description_

    Returns:
        amount: 金额
    """
    return data['amount']


def icbc_debit_get_note(data):
    """接收字符串，返回备注

    Args:
        data (string): _description_

    Returns:
        notes:备注
    """
    return data['notes']


def icbc_debit_init_key(data):
    """从账单文件中获取能唯一标识的关键字

    如"中国银行储蓄卡(0814)"，与"映射管理" "资产映射"中的"账户"一致

    用于在所有账户中找到对应的映射账户

    Args:
        data (string): _description_

    Returns:
        key (string):
    """
    return data['payment_method']


def icbc_debit_get_account(self, ownerid):
    """根据账单中的付款方式解析对应的账本账户。"""
    from project.apps.translate.services.account_resolution import resolve_account_from_text

    result = resolve_account_from_text(ownerid, self.key, bill=self.bill)
    return result.account if result.ok else ASSETS_OTHER


def icbc_debit_get_balance(data):
    return data['balance']


def icbc_debit_get_card_number(content):
    """
    从账单文件中获取银行卡号
    """
    icbc_debit_card_number = re.search(r'\d{19}', content).group()
    return icbc_debit_card_number
