# Complete comparison method

Compare total baseline cost N * B with prepared cost P + N * H, where P is
one-time preparation, B is baseline cost per use, H is prepared cost per use,
and N is the number of reuses of the same valid prepared structure.

If B > H, the simple equal-cost point is N = P / (B - H). Integer reuse counts
strictly above it favor preparation under the measured assumptions. If B <= H,
this model supplies no finite positive break-even count for a positive P.

Check correctness and the actual reuse distribution. Repeated preparation after
structure changes must be counted, not silently amortized forever. Read
[measurement prerequisites](measurement.md) before promoting a conclusion.
