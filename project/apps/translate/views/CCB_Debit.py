import re
# import logging

# from project.apps.translate.utils import InitStrategy, BILL_CCB_DEBIT
from project.apps.maps.models import Assets
from project.apps.translate.services.mapping_provider import extract_account_string
# from datetime import datetime

# ccb_debit_sourcefile_identifier = "中国建设银行个人活期账户全部交易明细"  # 能唯一标识所属银行及账单类型的原始上传文件
# ccb_debit_csvfile_identifier = "中国建设银行储蓄卡账单明细"  # 能唯一标识所属银行及账单类型


# class CcbDebitInitStrategy(InitStrategy):
#     def init(self, bill, **kwargs):
#         import itertools
#         card = kwargs.get('card_number', None)
#         bill = itertools.islice(bill, 1, None)
#         records = []
#         try:
#             for row in bill:
#                 record = {
#                     'transaction_time': datetime.strptime(row[3], '%Y%m%d').strftime('%Y-%m-%d %H:%M:%S'),  # 交易时间
#                     'transaction_category': row[0],  # 交易类型
#                     'counterparty': row[8],  # 交易对方
#                     'commodity': row[6],  # 商品
#                     'transaction_type': "支出" if "-" in row[4] else "收入",  # 收支类型（收入/支出/不计收支）
#                     'amount': row[4].replace("-", "") if "-" in row[4] else row[4],  # 金额
#                     'payment_method': "中国建设银行储蓄卡(" + card + ")",  # 支付方式
#                     'transaction_status': BILL_CCB_DEBIT + " - 交易成功",  # 交易状态
#                     'notes': row[6],  # 备注
#                     'bill_identifier': BILL_CCB_DEBIT,  # 账单类型
#                     'balance': row[5],
#                     'card_number': row[7],
#                 }
#                 records.append(record)
#         except UnicodeDecodeError as e:
#             logging.error("Unicode decode error at row=%s: %s", row, e)
#         except Exception as e:
#             logging.error("Unexpected error: %s", e)

#         return records


# def ccb_debit_xls_convert_to_string(file):
#     """
#     从Excel文件读取数据并转换为列表格式。

#     Args:
#         file(str): Excel文件的路径

#     Returns:
#         list: 包含Excel文件中所有数据的列表
#     """
#     import pandas as pd

#     # 读取Excel文件
#     data = pd.read_excel(file, header=None)
#     # 将DataFrame转换为嵌套列表
#     data_list = data.values.tolist()
#     return data_list


def ccb_debit_string_convert_to_csv(df):
    """接收字符串，返回CSV格式文件

    Args:
        data (string): _description_
        card_number (string): 储蓄卡/信用卡 完整的卡号

    Returns:
        csv: _description_
    """
    import pandas as pd


    data_list = df.fillna('').values.tolist()

    # 提取标题和账号信息
    title = data_list[0][4].replace('个人活期账户全部交易明细', '储蓄卡账单明细')
    card_number = data_list[1][1].split(':')[1]

    # 构造输出字符串
    output = f"{title} 卡号: {card_number}\n"

    # 添加列名
    columns = data_list[2]
    output += f"{columns[1]},{columns[2]},{columns[3]},{columns[4]},{columns[5]},{columns[6]},{columns[7].split('/')[0]},对方账号,户名\n"

    # 添加数据行
    for row in data_list[3:]:
        cleaned_row = [str(item).replace(',', '') if not pd.isna(item) else '（空）' for item in row]
        # 处理交易金额，为正数添加'+'
        transaction_amount = cleaned_row[5]
        if transaction_amount.startswith('-'):
            transaction_amount = transaction_amount.replace('-', '-')
        else:
            transaction_amount = '+' + transaction_amount

        cleaned_row[5] = transaction_amount  # 更新交易金额的格式

        account, name = (cleaned_row[8].split('/') + ['（空）', '（空）'])[:2]  # 处理对方账号与户名字段

        output += f"{cleaned_row[1]},{cleaned_row[2]},{cleaned_row[3]},{cleaned_row[4]},{transaction_amount},{cleaned_row[6]},{cleaned_row[7]},{account},{name}\n"

    # 输出结果
    return output.encode('utf-8-sig')


def ccb_debit_get_status(data):
    """接收字符串，返回条目状态

    Args:
        data (string): _description_

    Returns:
        status: 各交易状态（支付成功、退款成功等）
    """
    return data['transaction_status']


def ccb_debit_get_amount(data):
    """接收字符串，返回金额

    Args:
        data (string): _description_

    Returns:
        amount: 金额
    """
    return "{:.2f}".format(float(data['amount']))


def ccb_debit_get_note(data):
    """接收字符串，返回备注

    Args:
        data (string): _description_

    Returns:
        notes:备注
    """
    return data['notes']


def ccb_debit_init_key(data):
    """从账单文件中获取能唯一标识的关键字

    如"中国银行储蓄卡(0814)"，与"映射管理" "资产映射"中的"账户"一致

    用于在所有账户中找到对应的映射账户

    Args:
        data (string): _description_

    Returns:
        key (string):
    """
    return data['payment_method']


def ccb_debit_get_expense(self, ownerid):
    """根据bank_type_init_key得到的key来查找对应的expense

    Args:
        self (string): _description_
        ownerid (string): _description_

    Returns:
        expense: _description_
    """
    pass


def ccb_debit_get_account(self, ownerid):
    """根据账单中的付款方式解析对应的账本账户。"""
    from project.apps.translate.services.account_resolution import resolve_account_from_text
    from project.apps.translate.utils import ASSETS_OTHER

    result = resolve_account_from_text(ownerid, self.key, bill=self.bill)
    return result.account if result.ok else ASSETS_OTHER


def ccb_debit_get_balance(data):
    return data['balance']


def ccb_debit_get_card_number(content):
    """
    从账单文件中获取银行卡号
    """
    ccb_debit_card_number = re.search(r'\d{19}', content).group()
    return ccb_debit_card_number
