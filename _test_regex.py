import re
line = "ORMOY-SUR-VAUDRY ZW 22 4HA09A20C"
neg = r"(?![,\.]\d)"
pat = r"\b([A-Z]{1,3})\s+([0-9]{1,4}[A-Z]?)\b" + neg
matches = list(re.finditer(pat, line))
for m in matches:
    print(f"match: section={m.group(1)} number={m.group(2)}")
if not matches:
    print("no match!")
