---
name: new-money
description: Find companies getting their first contract from a federal agency, using USAspending.gov, and save the leads in this repository. Use when I name an agency and ask for new money, new contractors or contract leads.
---

Run the ready-made script in this folder. Don't write your own code, don't change the script and don't redesign the page.

1. If I haven't named an agency, ask which one. A full name ("Department of Agriculture") or an abbreviation (USDA, DHS) both work.
2. From the repository's top folder, run:
   `python3 .claude/skills/new-money/new_money.py "<agency>"`
   Add `--floor <dollars>`, `--days <number>` or `--years <number>` only if I asked for a different dollar floor, time window or lookback. The defaults are $100,000, the last 30 days and five years.
3. If the script stops with an error, tell me the error in plain words and stop. Don't guess, don't make up results and don't publish anything. If it says the agency name didn't match, show me the names it suggested.
4. If it worked, commit leads.md, leads.csv and index.html and push them to the main branch on GitHub.
5. Tell me in three or four sentences: how many contracts it looked at, how many companies came out as new, which ones weren't competed, and the biggest one. Use only the numbers the script printed. Then remind me these are leads to check, not findings.
