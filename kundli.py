import swisseph as swe
from datetime import date, timedelta

# Birth: 2 July 1984, 3:30 AM IST = 22:00 UTC July 1
jd = swe.julday(1984, 7, 1, 22.0)
lat, lon = 28.79, 76.14

swe.set_sid_mode(swe.SIDM_LAHIRI)

signs = ['Mesha(Ari)', 'Vrishabha(Tau)', 'Mithuna(Gem)', 'Karka(Can)',
         'Simha(Leo)', 'Kanya(Vir)', 'Tula(Lib)', 'Vrischika(Sco)',
         'Dhanu(Sag)', 'Makara(Cap)', 'Kumbha(Aqu)', 'Meena(Pis)']

nakshatras = ['Ashwini','Bharani','Krittika','Rohini','Mrigashira','Ardra',
              'Punarvasu','Pushya','Ashlesha','Magha','P.Phalguni','U.Phalguni',
              'Hasta','Chitra','Swati','Vishakha','Anuradha','Jyeshtha',
              'Moola','P.Ashadha','U.Ashadha','Shravana','Dhanishta',
              'Shatabhisha','P.Bhadrapada','U.Bhadrapada','Revati']

houses, ascmc = swe.houses_ex(jd, lat, lon, b'P', swe.FLG_SIDEREAL)
asc_deg = ascmc[0]
asc_sign = signs[int(asc_deg / 30)]
asc_deg_in = asc_deg % 30
asc_nak = nakshatras[int(asc_deg / (360/27))]

print("=" * 60)
print("  KUNDLI — DINESH BOHRA")
print("  DOB: 2 July 1984, 3:30 AM IST, Bhiwani, Haryana")
print("=" * 60)
print(f"  Lagna: {asc_sign} {asc_deg_in:.2f} | Nakshatra: {asc_nak}")
print()
print(f"  {'Planet':<14} {'Sign':<16} {'Deg':>6}  {'Nakshatra'}")
print("  " + "-" * 55)

planets = [(swe.SUN,'Sun'),(swe.MOON,'Moon'),(swe.MERCURY,'Mercury'),
           (swe.VENUS,'Venus'),(swe.MARS,'Mars'),(swe.JUPITER,'Jupiter'),
           (swe.SATURN,'Saturn'),(swe.MEAN_NODE,'Rahu')]

moon_lon = rahu_lon = 0
for pid, name in planets:
    r = swe.calc_ut(jd, pid, swe.FLG_SIDEREAL)[0][0]
    s = signs[int(r/30)]
    d = r % 30
    n = nakshatras[int(r/(360/27))]
    print(f"  {name:<14} {s:<16} {d:>5.2f}  {n}")
    if name == 'Moon': moon_lon = r
    if name == 'Rahu': rahu_lon = r

ketu_lon = (rahu_lon + 180) % 360
s = signs[int(ketu_lon/30)]
d = ketu_lon % 30
n = nakshatras[int(ketu_lon/(360/27))]
print(f"  {'Ketu':<14} {s:<16} {d:>5.2f}  {n}")

print(f"\n  Moon Rashi: {signs[int(moon_lon/30)]}")
print(f"  Moon Nakshatra: {nakshatras[int(moon_lon/(360/27))]}")

# Vimshottari Dasha
nak_lords = ['Ketu','Venus','Sun','Moon','Mars','Rahu','Jupiter','Saturn','Mercury']
dasha_yrs = {'Ketu':7,'Venus':20,'Sun':6,'Moon':10,'Mars':7,'Rahu':18,'Jupiter':16,'Saturn':19,'Mercury':17}
order = nak_lords

moon_nak_idx = int(moon_lon / (360/27))
lord = nak_lords[moon_nak_idx % 9]
nak_start = moon_nak_idx * (360/27)
traversed = moon_lon - nak_start
balance = (1 - traversed / (360/27)) * dasha_yrs[lord]

birth = date(1984, 7, 2)
today = date(2026, 4, 21)

print(f"\n  VIMSHOTTARI DASHA TIMELINE")
print(f"  {'Mahadasha':<12} {'Start':<12} {'End':<12} {'Yrs':>4}")
print("  " + "-" * 45)

cur = birth
first_end = cur + timedelta(days=int(balance*365.25))
tag = " ←NOW" if cur <= today <= first_end else ""
print(f"  {lord:<12} {str(cur):<12} {str(first_end):<12} {balance:>4.1f}{tag}")

si = order.index(lord)
cur = first_end
for i in range(1, 9):
    dl = order[(si+i)%9]
    yrs = dasha_yrs[dl]
    end = cur + timedelta(days=int(yrs*365.25))
    tag = " ←NOW" if cur <= today <= end else ""
    print(f"  {dl:<12} {str(cur):<12} {str(end):<12} {yrs:>4}{tag}")
    cur = end

# Find current MD and AD
cur = birth
fe = cur + timedelta(days=int(balance*365.25))
if today <= fe:
    md, md_start, md_end = lord, birth, fe
else:
    cur = fe
    for i in range(1,9):
        dl = order[(si+i)%9]
        end = cur + timedelta(days=int(dasha_yrs[dl]*365.25))
        if cur <= today <= end:
            md, md_start, md_end = dl, cur, end
            break
        cur = end

md_idx = order.index(md)
total = (md_end - md_start).days
ad_start = md_start
for j in range(9):
    al = order[(md_idx+j)%9]
    ad_days = int(dasha_yrs[al]/120*total)
    ad_end = ad_start + timedelta(days=ad_days)
    if ad_start <= today <= ad_end:
        remaining = (ad_end - today).days
        print(f"\n  CURRENT: {md} Mahadasha / {al} Antardasha")
        print(f"  {al} AD remaining: ~{remaining} days")
        break
    ad_start = ad_end

print("\n  KEY TRANSITS (Apr 2026):")
print("  Saturn → Kumbha (Aquarius)")
print("  Jupiter → Vrishabha (Taurus)")
print("  Rahu → Meena (Pisces)")
