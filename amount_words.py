"""A sum written out in words, for the line every invoice in Guinea carries:
"Arrêtée la présente facture à la somme de ...".

Whole GNF only -- the franc has no subdivision in use. French follows the
usual rules: "vingt et un", "soixante et onze", "quatre-vingts" but
"quatre-vingt-un", "deux cents" but "deux cent un", "mille" never takes an
s, "million" and "milliard" do.
"""

_FR_UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
             "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
             "dix-sept", "dix-huit", "dix-neuf"]
_FR_TENS = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}

_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
             "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
             "seventeen", "eighteen", "nineteen"]
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
            70: "seventy", 80: "eighty", 90: "ninety"}


def _fr_below_100(n):
    if n < 20:
        return _FR_UNITS[n]
    if n < 70:
        tens, unit = n - n % 10, n % 10
        if unit == 0:
            return _FR_TENS[tens]
        if unit == 1:
            return _FR_TENS[tens] + " et un"
        return _FR_TENS[tens] + "-" + _FR_UNITS[unit]
    if n < 80:
        rest = n - 60
        if rest == 11:
            return "soixante et onze"
        return "soixante-" + _FR_UNITS[rest]
    rest = n - 80
    if rest == 0:
        return "quatre-vingts"
    return "quatre-vingt-" + _FR_UNITS[rest]


def _fr_below_1000(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds == 1:
        parts.append("cent")
    elif hundreds > 1:
        parts.append(_FR_UNITS[hundreds] + " cent" + ("s" if rest == 0 else ""))
    if rest or not parts:
        parts.append(_fr_below_100(rest))
    return " ".join(parts)


def _fr(n):
    if n == 0:
        return "zéro"
    parts = []
    for scale, one, many in ((10 ** 9, "un milliard", "milliards"),
                             (10 ** 6, "un million", "millions")):
        q, n = divmod(n, scale)
        if q == 1:
            parts.append(one)
        elif q > 1:
            parts.append(_fr_below_1000(q) + " " + many)
    q, n = divmod(n, 1000)
    if q == 1:
        parts.append("mille")
    elif q > 1:
        parts.append(_fr_below_1000(q) + " mille")
    if n:
        parts.append(_fr_below_1000(n))
    return " ".join(parts)


def _en_below_1000(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(_EN_UNITS[hundreds] + " hundred")
    if rest:
        if rest < 20:
            parts.append(_EN_UNITS[rest])
        else:
            tens, unit = rest - rest % 10, rest % 10
            parts.append(_EN_TENS[tens] + ("-" + _EN_UNITS[unit] if unit else ""))
    return " ".join(parts)


def _en(n):
    if n == 0:
        return "zero"
    parts = []
    for scale, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        q, n = divmod(n, scale)
        if q:
            parts.append(_en_below_1000(q) + " " + name)
    if n:
        parts.append(_en_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount, lang="fr", currency="GNF"):
    """"cinquante-six millions huit cent douze mille cinq cents francs guinéens"
    for 56 812 500 GNF. Negative or non-integer amounts are rounded to whole
    units and the sign dropped: a bill is never negative."""
    n = abs(int(round(amount or 0)))
    words = _fr(n) if lang == "fr" else _en(n)
    if currency == "GNF":
        unit = "francs guinéens" if lang == "fr" else "Guinean francs"
        if n == 1:
            unit = "franc guinéen" if lang == "fr" else "Guinean franc"
    else:
        unit = currency
    return f"{words} {unit}"
