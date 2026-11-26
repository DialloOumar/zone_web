"""Bringing an old supplier ledger in: one Excel sheet per supplier, with its
invoices (CREDIT), payments (DEBIT) and running BALANCE.

Only each supplier's balance comes in, as its solde antérieur. The lines
themselves stay in the file: the payments are not tied to the bills they
settled, some have no date, and the final balance is the figure both sides
agree on.

Nothing is written while the file is reviewed. Every point the app cannot
settle alone becomes a question with no answer, and the import stays locked
until each one has been answered by a person. The questions:

  keep   the sheet may not be a supplier (land, loans, fuel, taxes...)
  dup    the sheet looks like the same supplier as an earlier sheet
  match  the name looks like a supplier already in the app, not exactly
  bal    the sheet's last balance and the sum of its lines disagree
  neg    the balance is negative: the supplier owes the company
  type   parts or machines cannot be told from the lines
  open   the supplier already has a solde antérieur in the app
  inv:N  an unpaid bill already in the app may also be in the file
  po:N   an order still to bill may already be billed in the file

A computed solde antérieur below zero is an error, and blocks the import too.
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

class Q:
    """One question about a sheet: its options, and the answer if given."""
    def __init__(self, key, code, label, options, value, detail=None):
        self.key, self.code, self.label, self.options, self.detail = key, code, label, options, detail
        valid = {o[0] for o in options}
        self.value = value if value in valid else None

    @property
    def open(self):
        return self.value is None


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


def review(imp, t, suppliers, open_bills, opening_rows, orders):
    """Read the file against the app and the answers given so far.

    suppliers: [Supplier]; open_bills: {supplier_id: [SupplierInvoice unpaid,
    not the opening]}; opening_rows: {supplier_id: SupplierInvoice};
    orders: {supplier_id: [PurchaseOrder still to bill]}.

    Returns (rows, groups, open_count): one row per sheet, and one group per
    supplier that will receive something."""
    sheets = json.loads(imp.data)
    ans = json.loads(imp.choices or "{}")
    by_id = {s.id: s for s in suppliers}
    by_name = {plain(s.name): s for s in suppliers}
    rows = []

    def ask(row, code, label, options, detail=None):
        key = "%d:%s" % (row["i"], code)
        q = Q(key, code, label, options, ans.get(key), detail)
        row["questions"].append(q)
        return q.value

    for s in sheets:
        row = dict(i=s["i"], sheet=s["sheet"], lines=s["lines"], readable=s["readable"], questions=[],
                   status=None, target=None, merged_into=None, name=ans.get("%d:name" % s["i"]) or s["name"],
                   default_name=s["name"], balance=None, kind=None, candidates=[], errors=[])
        rows.append(row)
        if not s["readable"]:
            row["status"] = "unread"
            continue
        # 1. Is it a supplier?
        name_hit = any(w in plain(s["sheet"]).split() or w in plain(s["sheet"]) for w in _NOT_SUPPLIER)
        # Loans and the boss's own purchases billed by a supplier: a doubt
        # only while the sheet still has a balance they may be part of.
        personal = sum(x[5] for x in s["lines"] if x[5] and _PERSONAL.search(plain(x[2])))
        if not (s["last"] or s["calc"]):
            personal = 0
        if name_hit or personal:
            v = ask(row, "keep", t["imp.q.keep_name"] if name_hit else t["imp.q.keep_lines"],
                    [("yes", t["imp.keep.yes"] if name_hit else t["imp.keep.balance"]), ("no", t["imp.keep.no"])],
                    None if name_hit else t["imp.personal_lines"] % fmt(personal))
            if v == "no":
                row["status"] = "skip"
                continue
        # 2. The same supplier as an earlier sheet?
        earlier = [r for r in rows[:-1] if r["readable"] and likeness(r["sheet"], s["sheet"]) >= NEAR]
        if earlier:
            v = ask(row, "dup", t["imp.q.dup"],
                    [("no", t["imp.dup.no"])] + [(str(r["i"]), t["imp.dup.with"] % r["sheet"]) for r in earlier])
            if v and v != "no":
                row["merged_into"] = int(v)
                row["status"] = "merged"
        # 3. Which supplier in the app.
        if row["status"] != "merged":
            exact = by_name.get(plain(row["name"])) or by_name.get(plain(s["sheet"]))
            row["candidates"] = sorted((x for x in suppliers if x is not exact and
                                        max(likeness(x.name, s["sheet"]), likeness(x.name, s["name"])) >= NEAR),
                                       key=lambda x: x.name)
            key = "%d:match" % s["i"]
            picked = ans.get(key)
            if picked == "new":
                row["target"] = "new"
            elif picked and picked.isdigit() and int(picked) in by_id:
                row["target"] = by_id[int(picked)]
            elif exact:
                row["target"] = exact
            elif not row["candidates"]:
                row["target"] = "new"
            # A question only when the name is close to someone's without
            # being theirs; the choice can be changed on every row anyway.
            row["match_open"] = row["target"] is None
        # 4. Which balance.
        last, calc = s["last"], s["calc"]
        if last is not None and abs(last - calc) > 1:
            v = ask(row, "bal", t["imp.q.bal"],
                    [("last", t["imp.bal.last"] % fmt(last)), ("calc", t["imp.bal.calc"] % fmt(calc))])
            row["balance"] = {"last": last, "calc": calc}.get(v)
        else:
            row["balance"] = last if last is not None else calc
        # 5. A negative balance.
        if row["balance"] is not None and row["balance"] < 0:
            v = ask(row, "neg", t["imp.q.neg"] % fmt(-row["balance"]),
                    [("zero", t["imp.neg.zero"]), ("skip", t["imp.neg.skip"])])
            if v == "skip":
                row["status"] = "skip"
                row["questions"] = [q for q in row["questions"] if q.code in ("keep", "dup", "bal", "neg")]
                continue
            row["balance"] = 0 if v == "zero" else None
        # 6. Parts or machines.
        row["kind"] = _kind_of(s["lines"])
        if row["status"] != "merged":
            row["status"] = "ok"

    # Sheets folded into another follow it; the one they follow carries the
    # questions about the supplier in the app.
    by_i = {r["i"]: r for r in rows}

    def head(r, seen=()):
        if r["status"] == "merged" and r["merged_into"] in by_i and r["i"] not in seen:
            return head(by_i[r["merged_into"]], seen + (r["i"],))
        return r
    groups = {}
    for r in rows:
        if r["status"] not in ("ok", "merged"):
            continue
        h = head(r)
        if h["status"] != "ok":
            # It follows a sheet that was left aside.
            r["status"], r["follows_skip"] = "skip", True
            continue
        tgt = h["target"]
        gkey = ("s", tgt.id) if tgt not in (None, "new") else ("n", h["i"])
        g = groups.setdefault(gkey, dict(key=gkey, head=h, rows=[], target=tgt))
        g["rows"].append(r)
        r["group"] = gkey
    # Two sheets pointing at the same supplier in the app add up.
    for g in groups.values():
        h = g["head"]
        g["lines"] = [x for r in g["rows"] for x in r["lines"]]
        bal = [r["balance"] for r in g["rows"]]
        g["balance"] = None if any(b is None for b in bal) else sum(bal)
        tgt = g["target"]
        # Parts or machines: the lines say, or the question is asked. A
        # supplier already in the app keeps what it has and gains the rest.
        kinds = {r["kind"] for r in g["rows"]} - {""}
        kind = kinds.pop() if len(kinds) == 1 else (None if kinds else "")
        current = None
        if tgt not in (None, "new"):
            current = ("both" if tgt.provides_parts and tgt.provides_machines else
                       "machines" if tgt.provides_machines else "parts" if tgt.provides_parts else None)
        if kind is None and current is None:
            v = ask(h, "type", t["imp.q.type"], [("parts", t["imp.type.parts"]), ("machines", t["imp.type.machines"]),
                                                ("both", t["imp.type.both"])])
            g["kind"] = v
        else:
            g["kind"] = kind or current or "parts"
            if kind and current and kind != current and current != "both":
                g["kind"] = "both"
        g["kind_auto"] = not any(q.code == "type" for q in h["questions"])
        g["deduct"], g["bills"] = 0, []
        if tgt in (None, "new"):
            g["opening"] = g["balance"]
            continue
        # What the app already holds for this supplier.
        amounts = {}
        for x in g["lines"]:
            if x[5] and not _carried(x[2], x[3]):
                amounts.setdefault(x[5], x)
        existing = opening_rows.get(tgt.id)
        g["existing"] = existing
        about_old = None
        if existing is not None:
            about_old = ask(h, "open", t["imp.q.open"] % fmt(existing.amount),
                            [("replace", t["imp.open.replace"]), ("keep", t["imp.open.keep"])])
        for b in open_bills.get(tgt.id, []):
            if b.date > (imp.as_of or "9999"):
                continue
            seen = amounts.get(b.amount)
            v = ask(h, "inv:%d" % b.id,
                    t["imp.q.bill"] % dict(num=b.number or "—", day=_dmy(b.date), amount=fmt(b.amount),
                                          rest=fmt(b.remaining)),
                    [("in", t["imp.bill.in"]), ("out", t["imp.bill.out"])],
                    (t["imp.seen"] % (_dmy(seen[0]) if seen[0] else (seen[1] or "?"), seen[2][:60])) if seen else None)
            if v == "in":
                g["deduct"] += b.remaining
        for po in orders.get(tgt.id, []):
            seen = amounts.get(po.total)
            if not seen:
                continue
            v = ask(h, "po:%d" % po.id, t["imp.q.order"] % dict(num=po.number, amount=fmt(po.total)),
                    [("billed", t["imp.order.billed"]), ("not", t["imp.order.not"])],
                    t["imp.seen"] % (_dmy(seen[0]) if seen[0] else (seen[1] or "?"), seen[2][:60]))
            if v == "billed":
                g["deduct"] += po.total
                g["bills"].append((po, seen))
        g["keep_old"] = about_old == "keep"
        if g["keep_old"]:
            g["opening"] = existing.amount
        elif existing is not None and about_old is None:
            g["opening"] = None
        else:
            g["opening"] = None if g["balance"] is None else g["balance"] - g["deduct"]

    # Errors: what no single answer settles.
    new_names = {}
    for g in groups.values():
        h = g["head"]
        if g["opening"] is not None and g["opening"] < 0:
            h["errors"].append(t["imp.err.below_zero"] % fmt(g["opening"]))
        ex = g.get("existing")
        if ex is not None and not g.get("keep_old") and g["opening"] is not None and g["opening"] < ex.paid_amount:
            h["errors"].append(t["imp.err.below_paid"] % fmt(ex.paid_amount))
        if g["target"] == "new":
            nm = plain(h["name"])
            if not nm:
                h["errors"].append(t["imp.err.no_name"])
            elif nm in by_name:
                h["errors"].append(t["imp.err.name_taken"])
            elif nm in new_names:
                h["errors"].append(t["imp.err.name_twice"] % new_names[nm])
            else:
                new_names[nm] = h["sheet"]
            if len(h["name"]) > 80:
                h["errors"].append(t["imp.err.name_long"])

    for r in rows:
        r["open"] = sum(1 for q in r["questions"] if q.open) + (1 if r.get("match_open") and r["status"] == "ok" else 0) \
            + len(r["errors"])
        r["state"] = ("open" if r["open"] else
                      "skip" if r["status"] in ("skip", "unread") else "ready")
    return rows, groups, sum(r["open"] for r in rows)


def _dmy(iso):
    if not iso or len(iso) != 10:
        return iso or ""
    return "%s/%s/%s" % (iso[8:10], iso[5:7], iso[0:4])
