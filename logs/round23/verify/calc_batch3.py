from math import sqrt
def hm(A,n1,n2):
    Q1=A/(2-A); Q2=2*A*A/(1+A)
    return sqrt((A*(1-A)+(n1-1)*(Q1-A*A)+(n2-1)*(Q2-A*A))/(n1*n2))
def bse(p,n): return sqrt(p*(1-p)/n)
def ba_se(se_,n1,sp,n0): return 0.5*sqrt(se_*(1-se_)/n1+sp*(1-sp)/n0)
def z(a,sa,b,sb): return abs(a-b)/sqrt(sa*sa+sb*sb)
print("C-77 matched (P-72 sMRI MAResNet18 vs P-82)")
s82a=bse(20/24,24); print(" P-82 acc SE",round(s82a,4))
for n in (57,171):
    s=bse(0.7368,n); print("  P-72 acc n=%d SE %.4f z %.3f"%(n,s,z(0.8333,s82a,0.7368,s)))
s82=hm(0.888,11,13); print(" P-82 AUC SE",round(s82,4))
for n1,n2 in ((22,35),(66,105)):
    s=hm(0.721,n1,n2); print("  P-72 AUC %d/%d SE %.4f z %.3f"%(n1,n2,s,z(0.888,s82,0.721,s)))
# multimodal for info
for n1,n2,n in ((22,35,57),(66,105,171)):
    print("  P-72 SPDFC acc n=%d z %.3f ; AUC z %.3f"%(n,z(0.8333,s82a,0.7719,bse(0.7719,n)),z(0.888,s82,0.755,hm(0.755,n1,n2))))
print("C-79")
s81cs=hm(0.7496,93,164); print(" P-81 CS AUC SE %.4f"%s81cs)
for name,A in (("SVM",0.832),("RFT",0.833),("MLP",0.810)):
    s=hm(A,295,298); print("  P-8 %s AUC %.3f SE %.4f z %.3f ; pool79/139 z %.3f"%(name,A,s,z(A,s,0.7496,s81cs),z(A,s,0.7496,hm(0.7496,79,139))))
s81ba=ba_se(0.5023,93,0.9238,164); print(" P-81 CS BA SE (from sens/spec) %.4f ; binomial %.4f"%(s81ba,bse(0.7130,257)))
for name,se_,sp in (("SVM",0.75,0.79),("RFT",0.77,0.78),("MLP",0.70,0.80)):
    ba=(se_+sp)/2; s=ba_se(se_,295,sp,298)
    print("  P-8 %s BA %.3f SE %.4f z %.3f (binomial z %.3f)"%(name,ba,s,z(ba,s,0.7130,s81ba),z(ba,bse(ba,593),0.7130,bse(0.7130,257))))
# rounding sensitivity of SVM BA
for ba in (0.765,0.775):
    print("   SVM BA %.3f z %.3f"%(ba,z(ba,ba_se(0.75,295,0.79,298),0.7130,s81ba)))
# headline
s81h=hm(0.7849,93,164); s8h=hm(0.874,295,298)
print(" headline AUC SE %.4f %.4f z %.3f"%(s81h,s8h,z(0.874,s8h,0.7849,s81h)))
s81hb=ba_se(0.6108,93,0.8544,164); s8hb=ba_se(0.85,295,0.78,298)
print(" headline BA SE %.4f %.4f z %.3f"%(s81hb,s8hb,z(0.815,s8hb,0.7326,s81hb)))
print("C-80")
s3a=bse(0.7461,593); s3u=hm(0.780,295,298)
print(" P-3 acc SE %.4f AUC SE %.4f"%(s3a,s3u))
print(" acc z %.3f ; AUC z %.3f"%(z(0.8333,s82a,0.7461,s3a),z(0.888,s82,0.780,s3u)))
print("C-81")
for n in (275,823):
    s=bse(0.8428,n); print(" P-122 acc n=%d SE %.4f z %.3f"%(n,s,z(0.8428,s,0.7461,s3a)))
s122=hm(0.8599,121,154); print(" P-122 AUC SE %.4f z %.3f"%(s122,z(0.8599,s122,0.780,s3u)))
# fold-based check for P-122 per brief not used as SE
