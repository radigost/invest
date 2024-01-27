import math

from tinkoff.invest import MoneyValue, Quotation


def to_float(value: MoneyValue | Quotation) -> float:
    return float(str(value.units) + '.' + str(value.nano))


def get_decimal_part(fl):
    return int(math.ceil((fl % 1) * pow(10, str(fl)[::-1].find('.'))))