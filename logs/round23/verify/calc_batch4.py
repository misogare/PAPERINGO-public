import math

def se_acc(p, n):
    return math.sqrt(p * (1 - p) / n)

def se_hm(A, n1, n0):
    q1 = A / (2 - A)
    q2 = 2 * A * A / (1 + A)
    v = (A * (1 - A) + (n1 - 1) * (q1 - A * A) + (n0 - 1) * (q2 - A * A)) / (n1 * n0)
    return math.sqrt(v)

def z(a, sa, b, sb):
    return abs(a - b) / math.sqrt(sa * sa + sb * sb)

def show(label, a, sa, b, sb):
    print(f"{label}: diff={abs(a-b):.4f} SE_a={sa:.4f} SE_b={sb:.4f} pooled={math.sqrt(sa*sa+sb*sb):.4f} z={z(a,sa,b,sb):.3f}")

print("=== C-82 (P-122 vs P-72)")
s122 = se_acc(0.8428, 275)
s122s = se_acc(0.8428, 823)
for lab, p in [("matched sMRI-only 73.68", 0.7368), ("headline 77.19", 0.7719)]:
    for n in (57, 171):
        show(f"acc {lab} n72={n} n122=275", 0.8428, s122, p, se_acc(p, n))
    show(f"acc {lab} n72=57 n122=823 samples", 0.8428, s122s, p, se_acc(p, 57))
a122 = se_hm(0.8599, 121, 154)
for lab, A in [("matched sMRI-only AUC 0.721", 0.721), ("headline AUC 0.755", 0.755)]:
    show(f"{lab} 22/35", 0.8599, a122, A, se_hm(A, 22, 35))
    show(f"{lab} 66/105", 0.8599, a122, A, se_hm(A, 66, 105))
# threshold n for P-72 matched acc to reach z=1.96
d = 0.8428 - 0.7368
need = (d / 1.96) ** 2 - s122 ** 2
print("n72 needed for z=1.96 (matched acc):", 0.7368 * 0.2632 / need)

print("=== C-83 (P-125 vs P-66)")
for n125 in (28, 30):
    for n66, lab66 in [(252, "subjects"), (360, "images")]:
        show(f"matched amygdala-only 70.0 n125={n125} n66={n66} {lab66}", 0.80, se_acc(0.80, n125), 0.70, se_acc(0.70, n66))
        show(f"headline 72.7 n125={n125} n66={n66} {lab66}", 0.80, se_acc(0.80, n125), 0.727, se_acc(0.727, n66))
# AUC sensitivity
show("AUC RF 0.8571 (14/14) vs P-66 amygdala 0.775 (~190/170)", 0.8571, se_hm(0.8571, 14, 14), 0.775, se_hm(0.775, 190, 170))
show("AUC SVM 0.9031 (14/14) vs P-66 amygdala 0.775", 0.9031, se_hm(0.9031, 14, 14), 0.775, se_hm(0.775, 190, 170))
show("AUC RF 0.8571 vs P-66 fusion 0.800", 0.8571, se_hm(0.8571, 14, 14), 0.80, se_hm(0.80, 190, 170))

print("=== C-84 (P-109 vs P-125)")
for n125 in (28, 30):
    for n109, lab in [(69, "10% of subjects"), (685, "all contrast subjects"), (3401, "test slices")]:
        show(f"acc 94.23 n109={n109} {lab} vs 80.00 n125={n125}", 0.9423, se_acc(0.9423, n109), 0.80, se_acc(0.80, n125))
    show(f"acc 94.23 SE=0 vs 80.00 n125={n125}", 0.9423, 0.0, 0.80, se_acc(0.80, n125))
show("abstract 82.14 n=28 vs 94.23 n=685", 0.9423, se_acc(0.9423, 685), 0.8214, se_acc(0.8214, 28))
a109 = se_hm(0.9715, 37, 32)
show("AUC 0.9715 (37/32) vs RF 0.8571 (14/14)", 0.9715, a109, 0.8571, se_hm(0.8571, 14, 14))
show("AUC 0.9715 (37/32) vs SVM 0.9031 (14/14)", 0.9715, a109, 0.9031, se_hm(0.9031, 14, 14))
a109s = se_hm(0.9715, 1628, 1773)
show("AUC 0.9715 (test slices 1628/1773) vs RF 0.8571", 0.9715, a109s, 0.8571, se_hm(0.8571, 14, 14))
show("AUC 0.9715 (test slices) vs SVM 0.9031", 0.9715, a109s, 0.9031, se_hm(0.9031, 14, 14))

print("=== C-93 (P-168 vs P-156)")
s168rf = se_hm(0.90, 77, 270)
s168lstm = se_hm(0.93, 77, 270)
for n1, n0 in [(58, 111), (50, 119), (66, 103), (57, 112)]:
    s156 = se_hm(0.81, n1, n0)
    show(f"matched RF 0.90 (77/270) vs 0.81 ({n1}/{n0})", 0.90, s168rf, 0.81, s156)
    show(f"headline LSTM 0.93 (77/270) vs 0.81 ({n1}/{n0})", 0.93, s168lstm, 0.81, s156)
# fold-SD alternative for P-168 single-timepoint: SE = 0.09/sqrt(10)
show("matched RF 0.90 fold SE 0.09/sqrt10 vs 0.81 (58/111)", 0.90, 0.09 / math.sqrt(10), 0.81, se_hm(0.81, 58, 111))
print("HM SE P-168 RF", s168rf, "LSTM", s168lstm, "P-156 58/111", se_hm(0.81, 58, 111))
