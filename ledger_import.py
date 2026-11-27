"""Bringing an old supplier ledger in: one Excel sheet per supplier, with its
invoices (CREDIT), payments (DEBIT) and running BALANCE.

Only each supplier's balance comes in, as its solde antérieur: the sum of
the lines the people reviewing the file kept ticked. The lines themselves
stay in the file: the payments are not tied to the bills they settled, and
some have no date.

Nothing is written while the file is reviewed, sheet by sheet, and the
import stays locked until every sheet is validated or left out.
"""
import difflib
import io
import json
import re
import unicodedata
from datetime import date, datetime

# Words that say nothing about who a supplier is, left out of name matching.
_STOP = {"SARL", "SARLU", "SA", "SAS", "SASU", "ETS", "STE", "SOCIETE", "ET", "DE", "DU", "LA", "LE",
         "LES", "DES", "D", "L", "CO", "LTD", "AND", "THE", "SURL", "GIE"}
# Common words that two different suppliers may share: they count towards a
# match, but a match needs at least one word that is not one of them.
_GENERIC = {"PNEUS", "PNEU", "LOCATION", "LOC", "CAMIONS", "CAMION", "SERVICES", "SERVICE", "BUSINESS",
            "TRANSPORT", "LOGISTIQUE", "GUINEE", "GUINEA", "INTERNATIONAL", "COMMERCE", "GENERAL",
            "GENERALE", "ENTREPRISE", "FRERES", "MOTORS", "TRADING", "SECURITE", "SECURITY", "MACHINE",
            "PIECES", "GROUP", "GROUPE", "SGRI", "SIGRI", "SIGUIRI", "CONAKRY", "CKY", "BUREAU", "LOYER",
            "ACHAT", "FACTURE", "CORPORATION", "CORPOR", "TECHNOLOGY", "INDUSTRY", "TRAVELS", "VOYAGE",
            "HOTEL", "GARAGE", "MULTI", "PLUS", "NEGOCE", "ALIMENTATION", "EQUIPEMENTS", "MINIBUS", "BUS"}
# A sheet that may not be a supplier at all, read off its name.
_NOT_SUPPLIER = ("TERRAIN", "CARBURANT", "IMPOT", "CNSS", "DOUANE", "PRET", "BOSS", "SALAIRE")
# Lines inside a supplier's sheet that are not purchases from him.
_PERSONAL = re.compile(r"\b(PRET|BOSS|PERSO)\b")
# A line billing the use of a machine.
_MACHINE = re.compile(r"\b(LOCATION|LOCAT|LOC|LOUAGE)\b")
_REPORT = re.compile(r"REP+ORT")
# How alike two names must be to ask whether they are one supplier: two
# words out of three, so a shared first name alone (SEKOU, BAH) is not enough.
NEAR = 0.66


def plain(s):
    """Upper case, no accents, no punctuation, single spaces."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()


def tokens(name):
    """The words of a name that say who it is. Initials typed with spaces
    (E P T L) are put back together into one word."""
    out, run = [], []
    for w in plain(name).split():
        if len(w) == 1 and w.isalpha():
            run.append(w)
            continue
        if run:
            out.append("".join(run)); run = []
        out.append(w)
    if run:
        out.append("".join(run))
    return [w for w in out if w not in _STOP]


def _same_word(a, b):
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def likeness(a, b):
    """How much two names look like the same supplier, 0 to 1: the share of
    the shorter name's words found in the other. Initials count for the words
    they stand for (GEG for GLOBAL ENERGY GROUP). Zero unless at least one
    shared word is not a common one."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    for x, y in ((ta, tb), (tb, ta)):
        initials = "".join(w[0] for w in y)
        if any(len(w) >= 3 and w in initials and len(y) >= 3 and w == initials[:len(w)] for w in x):
            return 1.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hit = [w for w in short if any(_same_word(w, v) for v in long_)]
    if not any(w not in _GENERIC and len(w) >= 3 for w in hit):
        return 0.0
    return len(hit) / len(short)


def fmt(n):
    return "{:,}".format(int(n)).replace(",", " ")


# ── Reading the file ───────────────────────────────────────────────────────

def _number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    if isinstance(v, str):
        d = re.sub(r"[\s \xa0]", "", v)
        if re.fullmatch(r"-?\d+", d):
            return int(d)
    return None


def _day(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _carried(text, ref):
    """The line carrying the previous year's balance forward."""
    return bool(_REPORT.search(plain(text + " " + ref)) or plain(text).startswith("SOLDE AU"))


def _clean_summary_name(s):
    s = str(s or "").replace("''", "'")
    s = re.sub(r"'?!A\d+$", "", s.strip()).strip(" '")
    return re.sub(r"\s+", " ", s)


def read_workbook(data):
    """Every sheet with DEBIT and CREDIT columns, as plain data:
    [{i, sheet, name, lines: [[day, raw_day, text, ref, debit, credit]],
      last, calc, readable}], and the latest day found in the file."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    long_names = []
    sheets, latest = [], None
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        head = None
        for n, r in enumerate(rows[:20]):
            cells = [plain(c) for c in r]
            if "DEBIT" in cells and "CREDIT" in cells:
                head = (n, cells)
                break
        title = ws.title.strip()
        if head is None:
            # A summary sheet: its first column carries the names in full,
            # where the sheet tabs were cut at 31 characters.
            long_names += [_clean_summary_name(r[0]) for r in rows if r and isinstance(r[0], str)]
            sheets.append(dict(sheet=title, name=title, lines=[], last=None, calc=0, readable=False))
            continue
        n, cells = head

        def col(*names):
            for k, c in enumerate(cells):
                if any(c == x or c.startswith(x) for x in names):
                    return k
            return None
        c_date, c_text = col("DATE"), col("DESCRIPTION", "LIBELLE", "DESIGNATION")
        c_ref, c_inv = col("DEL NOTE", "BL"), col("INVOICE", "FACTURE")
        c_deb, c_cre, c_bal = col("DEBIT"), col("CREDIT"), col("BALANCE", "SOLDE")

        def at(r, k):
            return r[k] if k is not None and k < len(r) else None
        lines, calc, last, moved = [], 0, None, False
        for r in rows[n + 1:]:
            if not r or not any(v not in (None, "") for v in r):
                continue
            text = str(at(r, c_text) or "").strip()
            deb, cre, bal = _number(at(r, c_deb)), _number(at(r, c_cre)), _number(at(r, c_bal))
            ref = " ".join(str(x).strip() for x in (at(r, c_ref), at(r, c_inv)) if x not in (None, ""))
            if _carried(text, ref) and not deb and not cre:
                # A carried-forward line with no amount of its own: the
                # opening balance when nothing came before it.
                if not moved and bal:
                    calc = bal
                    lines.append([None, None, text, ref, None, bal])
                    last = bal
                continue
            if not deb and not cre:
                continue
            moved = True
            calc += (cre or 0) - (deb or 0)
            if bal is not None:
                last = bal
            raw = at(r, c_date)
            day = _day(raw)
            if day and (latest is None or day > latest):
                latest = day
            lines.append([day, None if day else (str(raw).strip() if raw not in (None, "") else None),
                          text, ref, deb, cre])
        sheets.append(dict(sheet=title, name=title, lines=lines, last=last, calc=calc, readable=True))
    for k, s in enumerate(sheets):
        s["i"] = k
        key = plain(s["sheet"])
        full = [x for x in long_names if plain(x).startswith(key) and len(plain(x)) > len(key)]
        if s["readable"] and key and full:
            s["name"] = min(full, key=len)
    today = date.today().isoformat()
    return sheets, min(latest, today) if latest else today


# ── The review ─────────────────────────────────────────────────────────────
#
# One page per sheet: which supplier it is in the app, its lines with a box
# each (a line unticked stays out of the solde à nouveau), and, for a
# supplier the app already knows, the bills and orders the app holds that
# may be lines of the sheet too. A sheet is validated by hand; the import
# waits until every sheet is validated or left out.
#
# The answers, in imp.choices:
#   "<i>:match"   "new" | a supplier id | "sheet:<j>" (same supplier as sheet j)
#   "<i>:name"    the name of a new supplier
#   "<i>:type"    parts | machines | both
#   "<i>:off"     [line numbers unticked]
#   "<i>:skip"    "1": the sheet is not imported
#   "<i>:ok"      "1": the sheet is validated
#   "inv:<id>"    "<i>.<n>": that app bill is line n of sheet i | "none"
#   "po:<id>"     "<i>.<n>": that order was billed by line n of sheet i | "none"
#   "open:<sid>"  replace | keep: the supplier's solde antérieur already in the app


def review(imp, suppliers, open_bills, opening_rows, orders):
    """Read the file against the app and the answers given so far.

    suppliers: [Supplier]; open_bills: {supplier_id: [SupplierInvoice unpaid,
    not the opening]}; opening_rows: {supplier_id: SupplierInvoice};
    orders: {supplier_id: [PurchaseOrder still to bill]}.

    Returns (rows, groups): one row per sheet, one group per supplier that
    will receive something."""
    sheets = json.loads(imp.data)
    ans = json.loads(imp.choices or "{}")
    by_id = {s.id: s for s in suppliers}
    by_name = {plain(s.name): s for s in suppliers}
    as_of = imp.as_of or "9999"
    rows = []

    for s in sheets:
        i = s["i"]
        r = dict(i=i, sheet=s["sheet"], readable=s["readable"], default_name=s["name"],
                 name=ans.get("%d:name" % i) or s["name"], skip=ans.get("%d:skip" % i) == "1",
                 ok=ans.get("%d:ok" % i) == "1", last=s["last"], lines=[], candidates=[], sheet_cands=[],
                 pick=ans.get("%d:match" % i), auto=None, target=None, follows=None, bills=[], orders=[],
                 existing=None, open_answer=None, blocks=[], doubts=[], solde=0, owed=0)
        rows.append(r)
        if not s["readable"]:
            r["state"] = "unread"
            continue
        off = {n for n in ans.get("%d:off" % i, []) if isinstance(n, int)}
        for n, x in enumerate(s["lines"]):
            r["lines"].append(dict(n=n, day=_show_day(x), text=x[2], ref=x[3], debit=x[4] or 0, credit=x[5] or 0,
                                   carried=_carried(x[2], x[3]),
                                   doubt=bool(_PERSONAL.search(plain(x[2]))), off=n in off, claim=None))
        r["not_supplier"] = any(w in plain(s["sheet"]).split() or w in plain(s["sheet"]) for w in _NOT_SUPPLIER)
        # Who it is: the same name in the app, or names close to it, in the
        # app or among the sheets before it.
        exact = by_name.get(plain(r["name"])) or by_name.get(plain(s["sheet"]))
        r["candidates"] = sorted((x for x in suppliers if x is not exact and
                                  max(likeness(x.name, s["sheet"]), likeness(x.name, s["name"])) >= NEAR),
                                 key=lambda x: x.name)
        r["sheet_cands"] = [o for o in rows[:-1] if o["readable"] and likeness(o["sheet"], s["sheet"]) >= NEAR]
        if exact and not r["sheet_cands"]:
            r["auto"] = str(exact.id)
        elif not exact and not r["candidates"] and not r["sheet_cands"]:
            r["auto"] = "new"
        r["choice"] = r["pick"] or r["auto"]

    # Where each sheet goes: a supplier of the app, a new one, or the same
    # supplier as an earlier sheet (followed to wherever that one goes).
    by_i = {r["i"]: r for r in rows}

    def resolve(r, seen=()):
        c = r.get("choice")
        if not c:
            return None, r
        if c.startswith("sheet:"):
            j = int(c[6:]) if c[6:].isdigit() else -1
            o = by_i.get(j)
            if o is None or o["i"] in seen or o["i"] == r["i"] or o.get("state") == "unread":
                return None, r
            return resolve(o, seen + (r["i"],))
        if c == "new":
            return "new", r
        return (by_id.get(int(c)) if c.isdigit() else None), r

    for r in rows:
        if not r["readable"]:
            continue
        r["target"], head = resolve(r)
        r["head"] = head["i"]
        r["new_name"] = head["name"]
        if r["target"] is None:
            r["blocks"].append("match")

    # What the app already holds for a supplier, put against his sheets.
    for r in rows:
        tgt = r["target"]
        if not r["readable"] or tgt in (None, "new") or r["skip"]:
            continue
        credits = {}
        for l in r["lines"]:
            if l["credit"] and not l["carried"]:
                credits.setdefault(l["credit"], []).append(l["n"])

        def claim(key, label):
            a = ans.get(key)
            if a and a.startswith("%d." % r["i"]):
                n = a.split(".", 1)[1]
                if n.isdigit() and int(n) < len(r["lines"]):
                    r["lines"][int(n)]["claim"] = label
            return a
        for b in open_bills.get(tgt.id, []):
            if b.date > as_of:
                continue
            key = "inv:%d" % b.id
            a = claim(key, b.number or _dmy(b.date))
            same = credits.get(b.remaining, []) or credits.get(b.amount, [])
            r["bills"].append(dict(key=key, obj=b, day=_dmy(b.date), number=b.number, text=b.description,
                                   amount=b.amount, same=same, answer=a))
            if a is None:
                r["blocks"].append("bill")
        for po in orders.get(tgt.id, []):
            key = "po:%d" % po.id
            same = credits.get(po.total, [])
            a = ans.get(key)
            if not same and not a:
                continue
            a = claim(key, po.number)
            r["orders"].append(dict(key=key, obj=po, day=_dmy(po.date), number=po.number, amount=po.total,
                                    same=same, answer=a))
            if a is None:
                r["blocks"].append("order")
        ex = opening_rows.get(tgt.id)
        if ex is not None:
            r["existing"] = ex
            r["open_answer"] = ans.get("open:%d" % tgt.id) if ans.get("open:%d" % tgt.id) in ("replace", "keep") else None
            if r["open_answer"] is None:
                r["blocks"].append("open")

    for r in rows:
        if not r["readable"]:
            continue
        kept = [l for l in r["lines"] if not l["off"] and not l["claim"]]
        total = sum(l["credit"] - l["debit"] for l in kept)
        # A sheet where the supplier owes the company comes in with no
        # balance: a solde à nouveau is what the company owes.
        r["owed"] = -total if total < 0 else 0
        r["solde"] = max(total, 0)
        r["all_total"] = sum(l["credit"] - l["debit"] for l in r["lines"])
        k = _kind_of([[None, None, l["text"], l["ref"], l["debit"], l["credit"]] for l in kept])
        r["kind_guess"] = "both" if k is None else (k or "parts")
        r["kind"] = ans.get("%d:type" % r["i"]) if ans.get("%d:type" % r["i"]) in ("parts", "machines", "both") \
            else r["kind_guess"]
        if r["not_supplier"]:
            r["doubts"].append("name")
        # Loans and the boss's own purchases: a doubt only while the sheet
        # still has a balance they may be part of.
        if r["all_total"] and any(l["doubt"] for l in r["lines"]):
            r["doubts"].append("lines")
        else:
            for l in r["lines"]:
                l["doubt"] = False
        if r["last"] is not None and abs(r["last"] - r["all_total"]) > 1:
            r["doubts"].append("balance")
        if r["owed"]:
            r["doubts"].append("owed")

    # Suppliers: every sheet that comes in, grouped by where it goes.
    groups = {}
    for r in rows:
        if not r["readable"] or r["skip"] or r["target"] is None:
            continue
        head = by_i[r["head"]]
        if head["skip"]:
            r["blocks"].append("follows_skip")
            continue
        tgt = r["target"]
        gkey = ("s", tgt.id) if tgt != "new" else ("n", head["i"])
        g = groups.setdefault(gkey, dict(key=gkey, target=tgt, head=head, rows=[]))
        g["rows"].append(r)
        r["group"] = gkey
    new_names = {}
    for g in groups.values():
        rs = g["rows"]
        g["solde"] = sum(r["solde"] for r in rs)
        kinds = {r["kind"] for r in rs}
        g["kind"] = kinds.pop() if len(kinds) == 1 else "both"
        g["bills"] = [(o["obj"], by_i[int(o["answer"].split(".")[0])]["lines"][int(o["answer"].split(".")[1])])
                      for r in rs for o in r["orders"] if o["answer"] and o["answer"] != "none"
                      and o["answer"].startswith("%d." % r["i"])]
        ex = rs[0]["existing"]
        g["existing"] = ex
        g["keep_old"] = bool(ex is not None and rs[0]["open_answer"] == "keep")
        g["opening"] = ex.amount if g["keep_old"] else g["solde"]
        if ex is not None and not g["keep_old"] and g["opening"] < ex.paid_amount:
            for r in rs:
                r["blocks"].append("paid")
                r["paid"] = ex.paid_amount
        if g["target"] == "new":
            h = g["head"]
            nm = plain(h["name"])
            why = None
            if not nm:
                why = "no_name"
            elif nm in by_name:
                why = "name_taken"
            elif nm in new_names:
                why = "name_twice"
            elif len(h["name"]) > 80:
                why = "name_long"
            else:
                new_names[nm] = h["sheet"]
            if why:
                h["blocks"].append(why)
                h["name_twin"] = new_names.get(nm)

    for r in rows:
        if r.get("state") == "unread":
            continue
        # Nothing to decide: the name is the app's or new to it, no line is
        # in doubt, and the app holds nothing that may be in the sheet.
        r["simple"] = (r["auto"] is not None and r["pick"] in (None, r["auto"]) and not r["doubts"]
                       and not r["bills"] and not r["orders"] and r["existing"] is None and not r["blocks"])
        if r["skip"]:
            r["state"] = "skip"
        elif r["blocks"]:
            r["state"] = "todo"
        elif r["ok"]:
            r["state"] = "ok"
        else:
            r["state"] = "check"
    return rows, groups


def _show_day(x):
    return _dmy(x[0]) if x[0] else (x[1] or "")


def _kind_of(lines):
    """parts | machines | None (both kinds of line) | "" (no bill to read)."""
    bills = [plain(x[2]) for x in lines if x[5] and not _carried(x[2], x[3])]
    if not bills:
        return ""
    m = sum(1 for b in bills if _MACHINE.search(b))
    if m == 0:
        return "parts"
    if m == len(bills):
        return "machines"
    return None


def _dmy(iso):
    if not iso or len(iso) != 10:
        return iso or ""
    return "%s/%s/%s" % (iso[8:10], iso[5:7], iso[0:4])
