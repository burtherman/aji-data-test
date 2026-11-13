#!/usr/bin/env python3
"""New money: find companies getting their first contract from a federal agency.

Pulls an agency's new contracts from USAspending.gov, checks whether each company
had any contract with that agency in the years before, flags contracts that were
not competed, and writes leads.md (readable on GitHub, even in a private repository), index.html (a web page) and leads.csv (the same rows for a spreadsheet).

Usage:
  python3 new_money.py "Department of Agriculture" [--days 30] [--floor 100000] [--years 5]

Uses only the Python standard library and USAspending.gov's public API (no key).
"""
import argparse, csv, datetime as dt, html, json, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://api.usaspending.gov/api/v2"
CONTRACTS = ["A", "B", "C", "D"]          # contract award types
IDVS = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]  # umbrella contracts
NOT_COMPETED = ["B", "C", "G"]            # not available for competition, not competed, not competed under simplified acquisition
WORKERS = 4                               # lookups run four at a time


def call(path, body=None, tries=6):
    url = API + path
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"USAspending.gov request failed ({path}): {e}")
            time.sleep(min(2 ** i, 20))   # wait 1, 2, 4, 8, 16 seconds between tries


def find_agency(name):
    agencies = call("/references/toptier_agencies/")["results"]
    want = name.strip().lower()
    for a in agencies:
        if want in (a["agency_name"].lower(), (a["abbreviation"] or "").lower()):
            return a["agency_name"]
    close = [a["agency_name"] for a in agencies if want in a["agency_name"].lower()]
    sys.exit("No agency matches '%s'. %s" % (name, ("Did you mean: " + "; ".join(close)) if close else
             "Use the full name, e.g. 'Department of Agriculture', or an abbreviation like USDA."))


def search(filters, fields, limit=100, sort="Award Amount"):
    rows, page = [], 1
    while True:
        d = call("/search/spending_by_award/", {"filters": filters, "fields": fields, "limit": limit,
                                                 "page": page, "sort": sort, "order": "desc"})
        rows += d["results"]
        if not d["page_metadata"].get("hasNext"):
            return rows
        page += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agency")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--floor", type=float, default=100000)
    ap.add_argument("--years", type=int, default=5)
    a = ap.parse_args()
    t0 = time.time()

    agency = find_agency(a.agency)
    # Cloud sessions run on UTC; use Eastern time so an evening run isn't dated tomorrow
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    start = today - dt.timedelta(days=a.days)
    prior_end = start - dt.timedelta(days=1)
    prior_start = prior_end.replace(year=prior_end.year - a.years)
    ag = [{"type": "awarding", "tier": "toptier", "name": agency}]
    window = [{"start_date": start.isoformat(), "end_date": today.isoformat(), "date_type": "new_awards_only"}]
    fields = ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Base Obligation Date",
              "Awarding Sub Agency", "Description", "generated_internal_id"]

    # 1. Every new contract from the agency in the window
    allnew = search({"award_type_codes": CONTRACTS, "agencies": ag, "time_period": window}, fields)
    big = [r for r in allnew if (r["Award Amount"] or 0) >= a.floor and r["Recipient UEI"]]
    ueis = sorted({r["Recipient UEI"] for r in big})

    # 2. Which of those were not competed (one extra search)
    nc = search({"award_type_codes": CONTRACTS, "agencies": ag, "time_period": window,
                 "extent_competed_type_codes": NOT_COMPETED}, ["Award ID", "Award Amount", "generated_internal_id"])
    not_competed = {r["generated_internal_id"] for r in nc}

    # 3. For each company: any contract with this agency in the prior years?
    prior = [{"start_date": prior_start.isoformat(), "end_date": prior_end.isoformat()}]

    def had_before(uei, types=CONTRACTS):
        d = call("/search/spending_by_award/", {"filters": {
            "award_type_codes": types, "agencies": ag, "time_period": prior,
            "recipient_search_text": [uei]}, "fields": ["Award ID", "Recipient UEI"], "limit": 5, "page": 1})
        # A search by ID also returns contracts held by companies whose parent has that ID,
        # so any result at all means this company or a related one worked for the agency before.
        return uei, len(d["results"]) > 0

    with ThreadPoolExecutor(WORKERS) as ex:
        history = dict(ex.map(had_before, ueis))
    maybe_new = [u for u in ueis if not history[u]]
    # Umbrella contracts (IDVs) are searched separately; only for companies that still look new
    with ThreadPoolExecutor(WORKERS) as ex:
        idv = dict(ex.map(lambda u: had_before(u, IDVS), maybe_new))
    new_ueis = [u for u in maybe_new if not idv[u]]

    # 4. Same-name check: a company with the exact same name but a different ID that had contracts before
    names = {r["Recipient UEI"]: r["Recipient Name"] for r in big}

    def same_name(uei):
        d = call("/search/spending_by_award/", {"filters": {
            "award_type_codes": CONTRACTS, "agencies": ag, "time_period": prior,
            "recipient_search_text": [names[uei]]}, "fields": ["Award ID", "Recipient Name", "Recipient UEI"], "limit": 25, "page": 1})
        return uei, any((r["Recipient Name"] or "").upper() == names[uei].upper() and r["Recipient UEI"] != uei
                        for r in d["results"])

    with ThreadPoolExecutor(WORKERS) as ex:
        name_hit = dict(ex.map(same_name, new_ueis))

    leads = [r for r in big if r["Recipient UEI"] in new_ueis]
    for r in leads:
        r["not_competed"] = r["generated_internal_id"] in not_competed
        r["same_name"] = name_hit[r["Recipient UEI"]]
    leads.sort(key=lambda r: -(r["Award Amount"] or 0))
    secs = round(time.time() - t0)

    write_csv(leads)
    write_md(agency, a, start, today, prior_start, allnew, big, ueis, leads)
    write_page(agency, a, start, today, prior_start, prior_end, allnew, big, ueis, leads, secs)
    print(f"{agency}: {len(allnew)} new contracts; {len(big)} at ${a.floor:,.0f} or more, to {len(ueis)} companies; "
          f"{len(new_ueis)} companies new to the agency, {len(leads)} contracts; "
          f"{sum(r['not_competed'] for r in leads)} not competed; {sum(r['same_name'] for r in leads)} same-name flags. "
          f"Took {secs} seconds. Wrote leads.md, leads.csv and index.html.")


def award_url(r):
    return "https://www.usaspending.gov/award/" + urllib.parse.quote(r["generated_internal_id"])


def short_name(name):
    # Donors rarely type "LLC" or "Inc." after their employer, so drop those endings for the donation search
    import re
    n = re.sub(r"[.,]", " ", name.upper())
    n = re.sub(r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LP|LLP|PLLC|PC|THE)\b", " ", n)
    return " ".join(n.split()) or name


def fec_url(name):
    return "https://www.fec.gov/data/receipts/individual-contributions/?" + urllib.parse.urlencode({"contributor_employer": short_name(name)})


def cell(v):
    # Stop spreadsheet apps from treating a government text field as a formula
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def write_csv(leads):
    with open("leads.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "amount", "date", "awarding office", "description", "not competed",
                    "same name had contracts before", "usaspending link", "fec employer search"])
        for r in leads:
            w.writerow([cell(x) for x in [r["Recipient Name"], r["Award Amount"], r["Base Obligation Date"], r["Awarding Sub Agency"],
                        r["Description"], "yes" if r["not_competed"] else "", "yes" if r["same_name"] else "",
                        award_url(r), fec_url(r["Recipient Name"])]])


def md(v):
    # Keep table cells on one line and stop a | from breaking the table
    return " ".join(str(v or "").split()).replace("|", "/")


def write_md(agency, a, start, today, prior_start, allnew, big, ueis, leads):
    nc = sum(r["not_competed"] for r in leads)
    out = [f"# New money at {agency}", "",
           f"Contracts of ${a.floor:,.0f} or more that {agency} awarded from {fmt_date(start.isoformat())} through {fmt_date(today.isoformat())} "
           f"to companies with no contract from the agency since {fmt_date(prior_start.isoformat())}. Biggest first. **These are leads to check, not findings.**", "",
           f"{len(allnew)} new contracts in {a.days} days; {len(big)} were ${a.floor:,.0f} or more, to {len(ueis)} companies. "
           f"{len(set(r['Recipient UEI'] for r in leads))} of those companies look new to the agency; {nc} of their contracts were not competed. "
           f"Data from USAspending.gov, pulled {fmt_date(today.isoformat())}.", "",
           "| Company | Amount | Awarded | What it's for | Flags | Check it |", "|---|--:|---|---|---|---|"]
    for r in leads:
        flags = []
        if r["not_competed"]:
            flags.append("**Not competed**")
        if r["same_name"]:
            flags.append("Probably not new: same name had contracts")
        out.append(f"| {md(r['Recipient Name'])} | ${r['Award Amount']:,.0f} | {fmt_date(r['Base Obligation Date'])} | "
                   f"{md((r['Description'] or '').capitalize())} | {'; '.join(flags)} | "
                   f"[Contract record]({award_url(r)}) · [Search donations]({fec_url(r['Recipient Name'])}) |")
    if not leads:
        out.append("| No contracts matched. | | | | | |")
    out += ["", "## What this could get wrong", "",
            "- One company can have more than one ID, and a parent or subsidiary with a different name won't be caught.",
            "- Joint ventures get their own ID, so a \"new\" joint venture can be made of longtime contractors.",
            "- Only contracts count. Grants, loans, farm payments and subcontracts don't.",
            "- Many small purchases aren't reported, so a company with small past sales may look new.",
            "- Agencies report late, and amounts change as contracts are modified.",
            "- \"Search donations\" finds people who named this company as their employer. Expect small, old and wrong-company matches.",
            ""]
    open("leads.md", "w").write("\n".join(out))


def fmt_date(s):
    d = dt.date.fromisoformat(s)
    return d.strftime("%b. %-d, %Y").replace("May.", "May").replace("Jun.", "June").replace("Jul.", "July")


def write_page(agency, a, start, today, prior_start, prior_end, allnew, big, ueis, leads, secs):
    e = html.escape
    rows = []
    for r in leads:
        flags = []
        if r["not_competed"]:
            flags.append('<span class="flag red">Not competed</span>')
        if r["same_name"]:
            flags.append('<span class="flag">Probably not new: same name had contracts</span>')
        rows.append(f"""<tr><td><strong>{e(r['Recipient Name'])}</strong><br>{' '.join(flags)}</td>
<td class="num">${r['Award Amount']:,.0f}</td><td>{e(fmt_date(r['Base Obligation Date']))}</td>
<td>{e(r['Awarding Sub Agency'] or '')}</td><td>{e((r['Description'] or '').capitalize())}</td>
<td><a href="{e(award_url(r))}">Contract record</a><br><a href="{e(fec_url(r['Recipient Name']))}">Search donations</a></td></tr>""")
    nc = sum(r["not_competed"] for r in leads)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>New money at {e(agency)}</title><style>
body{{font-family:Georgia,serif;max-width:1100px;margin:2rem auto;padding:0 16px;color:#222;line-height:1.5}}
h1{{font-size:2rem;margin-bottom:.2rem}} .dek{{color:#555;margin-top:0}} table{{border-collapse:collapse;width:100%;font-size:.95rem}}
th,td{{border-bottom:1px solid #ddd;padding:.5rem;text-align:left;vertical-align:top}} th{{font-family:sans-serif;font-size:.8rem;text-transform:uppercase;color:#555}}
.num{{text-align:right;white-space:nowrap}} .flag{{font-family:sans-serif;font-size:.75rem;background:#eee;padding:1px 6px;border-radius:3px}}
.red{{background:#c62828;color:#fff}} .box{{background:#f6f6f2;padding:1rem 1.2rem;border-radius:6px}} .wrap{{overflow-x:auto}}
</style></head><body>
<h1>New money at {e(agency)}</h1>
<p class="dek">Contracts of ${a.floor:,.0f} or more that {e(agency)} awarded from {e(fmt_date(start.isoformat()))} through {e(fmt_date(today.isoformat()))} to companies with no contract from the agency in the {a.years} years before. Biggest first. These are leads to check, not findings.</p>
<div class="box"><strong>What this run found.</strong> {len(allnew)} new contracts in {a.days} days. {len(big)} were ${a.floor:,.0f} or more, to {len(ueis)} companies. {len(set(r['Recipient UEI'] for r in leads))} of those companies had no contract with the agency since {e(fmt_date(prior_start.isoformat()))}; they got the {len(leads)} contracts below. {nc} of them were not competed. Data from USAspending.gov, pulled {e(fmt_date(today.isoformat()))} in {secs} seconds.</div>
<div class="wrap"><table><thead><tr><th>Company</th><th>Amount</th><th>Awarded</th><th>Office</th><th>What it's for</th><th>Check it</th></tr></thead><tbody>
{''.join(rows) or '<tr><td colspan="6">No contracts matched.</td></tr>'}
</tbody></table></div>
<h2>How a company was counted as new</h2>
<p>Every company that wins a federal contract registers with the government and gets an ID number. For each company above, the agency's contract records from {e(fmt_date(prior_start.isoformat()))} through {e(fmt_date(prior_end.isoformat()))} were searched for that ID. No record means it's listed here as new. As a second check, the company's exact name was searched too, and a match under a different ID is flagged "probably not new."</p>
<p>"Not competed" means the agency reported that the contract wasn't open to bids from other companies. There can be good reasons, like emergencies or only one supplier, but it's where reporters look first.</p>
<p>"Search donations" opens the Federal Election Commission's search for political donations from people who listed this company as their employer. Expect noise: small donations, old ones and other companies with similar names. Read each result before you treat it as a connection.</p>
<h2>What this could get wrong</h2>
<ul>
<li>One company can have more than one ID, and a parent or subsidiary with a different name won't be caught.</li>
<li>Joint ventures get their own ID, so a "new" joint venture can be made of longtime contractors.</li>
<li>Only contracts count. Grants, loans, farm payments and subcontracts don't.</li>
<li>Many small purchases, like government credit card buys, aren't reported, so a company with small past sales may look new.</li>
<li>Agencies report late. Contracts from the last few days may be missing, and amounts change as contracts are modified.</li>
<li>Government records have errors, and they carry through to this list. Open each contract record before you rely on it.</li>
</ul>
</body></html>"""
    open("index.html", "w").write(page)


if __name__ == "__main__":
    main()
