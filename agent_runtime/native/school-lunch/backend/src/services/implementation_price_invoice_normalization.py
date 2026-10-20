"""Normalize an explicitly reviewed invoice price, retaining the original basis.

No commodity names, schools or dates participate in conversion. Missing units,
package contents, tax meaning and price validity are operator-visible blockers.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
import unicodedata


def _number(value, field, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field}を数値で指定してください。") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{field}は{'正の' if positive else '0以上の'}有限数が必要です。")
    return result


def unit(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower().replace("円/", "")
    aliases = {"キログラム": "kg", "グラム": "g", "個": "個", "本": "本", "袋": "袋", "缶": "缶", "尾": "個", "枚": "個"}
    return aliases.get(text, text)


def normalize_invoice_cost(cost, *, implementation_date, tax_rate):
    name = str(cost.get("name") or "品目")
    try:
        start = date.fromisoformat(str(cost.get("valid_from") or ""))
        end = date.fromisoformat(str(cost.get("valid_until") or ""))
        day = date.fromisoformat(str(implementation_date))
    except ValueError as exc:
        raise ValueError(f"{name}: 単価を使用する開始日・終了日を確認してください。") from exc
    if start > end or not start <= day <= end:
        raise ValueError(f"{name}: 実施日が確認済み単価の使用期間外です。")
    basis = cost.get("price_tax_basis")
    if basis not in {"included", "excluded"}:
        raise ValueError(f"{name}: 納品書の税抜・税込を確認してください。")
    price = _number(cost.get("invoice_unit_price"), f"{name} 納品書単価")
    original_unit = unit(cost.get("invoice_unit"))
    target_unit = unit(cost.get("price_unit"))
    if not original_unit or not target_unit:
        raise ValueError(f"{name}: 納品書の単位・計算に使う単位を確認してください。")
    supported = {"g", "kg", "個", "本", "袋", "缶", "箱", "ケース", "パック", "l", "ml"}
    if original_unit not in supported or target_unit not in {"kg", "個", "本", "袋"}:
        raise ValueError(f"{name}: 単位換算を確認してください（{original_unit} → {target_unit}）。")
    package_provided = cost.get("package_quantity") not in (None, "") or cost.get("package_unit") not in (None, "")
    if original_unit == target_unit:
        if package_provided:
            raise ValueError(f"{name}: 同じ単位の換算に包装量は使いません。包装量を空欄にしてください。")
        converted = price
    elif original_unit == "g" and target_unit == "kg":
        if package_provided:
            raise ValueError(f"{name}: gからkgの換算に包装量は使いません。")
        converted = price * Decimal(1000)
    else:
        # E.g. a bag contains 500g, a can contains 16.5kg, a bag contains 50 fish.
        if original_unit not in {"袋", "缶", "箱", "ケース", "パック", "個", "本", "l", "ml"}:
            raise ValueError(f"{name}: 異なる単位の換算は包装内容量で確認してください。")
        size = _number(cost.get("package_quantity"), f"{name} 1{original_unit}あたりの内容量", positive=True)
        content_unit = unit(cost.get("package_unit"))
        if content_unit == "g" and target_unit == "kg":
            size /= Decimal(1000)
        elif content_unit != target_unit:
            raise ValueError(f"{name}: 包装の内容量単位と計算単位が一致しません。")
        converted = price / size
    if basis == "excluded":
        if tax_rate is None or tax_rate == "":
            raise ValueError(f"{name}: 外税から税込への換算には税率が必要です。")
        rate = _number(tax_rate, "税率")
        converted *= Decimal(1) + rate
    precision = cost.get("unit_price_precision")
    rounding = cost.get("unit_price_rounding")
    if precision not in (None, "") or rounding not in (None, ""):
        digits = _number(precision, f"{name} 単価の小数桁")
        if digits != digits.to_integral_value() or digits > 6 or rounding not in {"half_up", "down"}:
            raise ValueError(f"{name}: 単価の小数桁と丸め方を両方指定してください。")
        converted = converted.quantize(Decimal(1).scaleb(-int(digits)), rounding=ROUND_HALF_UP if rounding == "half_up" else ROUND_DOWN)
    return {
        **cost,
        "unit_price_tax_included": converted,
        "unit_price_tax_excluded": None,
        "price_tax_basis": "included",
        "invoice_original_price": price,
        "invoice_original_unit": original_unit,
        "invoice_original_tax_included": basis == "included",
    }
