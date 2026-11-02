#!/usr/bin/env python3
"""Compare nekRS tgv diagnostics (energy, enstrophy) against examples/tgv/ref/data.

usage: compare_tgv.py <nekrs log> <ref/data>
"""
import re
import sys

import numpy as np

log, ref = sys.argv[1], sys.argv[2]

pat = re.compile(r"^\s*time= (\S+)\s+enst= (\S+)\s+energy= (\S+)(?:.*d\(energy\)/dt= (\S+))?")
t, enst, ener, dedt = [], [], [], []
with open(log) as f:
    for line in f:
        m = pat.match(line)
        if m:
            t.append(float(m.group(1)))
            enst.append(float(m.group(2)))
            ener.append(float(m.group(3)))
            dedt.append(float(m.group(4)) if m.group(4) else np.nan)
t, enst, ener, dedt = map(np.array, (t, enst, ener, dedt))

r = np.loadtxt(ref, comments="#")
rt, rE, rdiss, rens = r[:, 0], r[:, 1], r[:, 2], r[:, 3]

print(f"nekRS samples: {len(t)}  t = {t[0]:g} .. {t[-1]:g}")
print(f"finite: energy {np.isfinite(ener).all()}  enstrophy {np.isfinite(enst).all()}")
print()
print(f"{'t':>6} {'E nekRS':>10} {'E ref':>10} {'relErr':>8}   {'enst nekRS':>10} {'enst ref':>10} {'relErr':>8}   {'-dE/dt nek':>10} {'-dE/dt ref':>10}")
for tq in [0, 2, 4, 6, 8, 9, 10, 12, 14, 16, 18, 19.9]:
    i = np.argmin(np.abs(t - tq))
    if abs(t[i] - tq) > 0.01:
        continue
    Er, er, dr = (np.interp(t[i], rt, a) for a in (rE, rens, rdiss))
    print(f"{t[i]:6.2f} {ener[i]:10.5f} {Er:10.5f} {abs(ener[i]-Er)/Er:8.2%}   "
          f"{enst[i]:10.4f} {er:10.4f} {abs(enst[i]-er)/er:8.2%}   {-dedt[i]:10.6f} {dr:10.6f}")

mask = t <= rt[-1]
iE = np.argmax(enst[mask]); iR = np.argmax(rens)
print()
print(f"peak enstrophy: nekRS {enst[mask][iE]:.4f} at t={t[mask][iE]:.2f}   ref {rens[iR]:.4f} at t={rt[iR]:.2f}")
j = np.argmax(np.where(np.isfinite(dedt), -dedt, -np.inf)); k = np.argmax(rdiss)
print(f"peak -dE/dt:    nekRS {-dedt[j]:.6f} at t={t[j]:.2f}   ref {rdiss[k]:.6f} at t={rt[k]:.2f}")
eE = np.max(np.abs(ener[mask] - np.interp(t[mask], rt, rE)) / np.interp(t[mask], rt, rE))
print(f"max relative energy error over run: {eE:.2%}")
