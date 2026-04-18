import re

text = open('/home/nico/Documents/ia_hat/ocr/output/10088_CG.txt').read()
for line in text.splitlines():
    uline = line.upper().strip()
    m = re.search(r'\b\d{2,3}\s+([A-Z]{1,3})\s+(\d{1,4})\b', uline)
    if m and m.group(1) in ('A', 'ES'):
        print(repr(line.strip()[:80]), '->', m.group(1), m.group(2))
