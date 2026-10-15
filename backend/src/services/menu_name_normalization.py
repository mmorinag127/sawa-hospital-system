"""Canonical menu-name comparison without application or database imports."""
import re
import unicodedata


_MENU_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def _normalize_menu_name(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).translate(_MENU_TRANSLATION)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("・", "").replace("／", "/")
    normalized = re.sub(r"[‐‑‒–—―ーｰ]+", "-", normalized)
    normalized = normalized.translate(str.maketrans({"（": "(", "）": ")", "［": "[", "］": "]"}))
    normalized = re.sub(r"[()\\[\\]{}「」『』<>＜＞]", "", normalized)
    return normalized.strip().lower()
